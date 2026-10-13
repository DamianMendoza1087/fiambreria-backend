"""Tests de configuración/protección de la base y del backup SQLite.

Nunca usan la base real: todo ocurre en directorios temporales. Los tests de arranque
importan main.py en un proceso separado porque main.py inicializa la base al importarse.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)

import db_config  # noqa: E402
from db_config import DatabaseConfigError, load_database_config  # noqa: E402

STARTUP_SCRIPT = """
import json, sys
sys.path.insert(0, {repo!r})
import db_config
db_config.PRODUCTION_DATA_DIR = {data_dir!r}
import main
from fastapi.testclient import TestClient
print("HEALTH=" + json.dumps(TestClient(main.app).get("/health").json()))
print("URL=" + main.SQLALCHEMY_DATABASE_URL)
"""


def run_startup(workdir, data_dir, extra_env):
    """Importa main.py en un proceso aparte con el entorno indicado."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("APP_ENV", "DATABASE_URL", "ALLOW_DB_INITIALIZATION")}
    env.update(extra_env)
    code = STARTUP_SCRIPT.format(repo=REPO_DIR, data_dir=str(data_dir))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(workdir), env=env,
                          capture_output=True, text=True, timeout=120)
    health = None
    for line in proc.stdout.splitlines():
        if line.startswith("HEALTH="):
            health = json.loads(line[len("HEALTH="):])
    return proc, health


def prod_env(db_path, allow_init=False):
    env = {"APP_ENV": "production", "DATABASE_URL": f"sqlite:///{db_path}"}
    if allow_init:
        env["ALLOW_DB_INITIALIZATION"] = "true"
    return env


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "var-data"
    d.mkdir()
    return d


@pytest.fixture(scope="session")
def initialized_db(tmp_path_factory):
    """Base completa creada por el arranque autorizado (primer arranque)."""
    root = tmp_path_factory.mktemp("seed")
    data = root / "var-data"
    data.mkdir()
    db = data / "fiambreria.db"
    proc, _ = run_startup(root, data, prod_env(db, allow_init=True))
    assert proc.returncode == 0, proc.stderr
    return db


def copy_db(src, dest):
    dest.write_bytes(src.read_bytes())
    return dest


# ---------- Desarrollo ----------

def test_dev_without_database_url_uses_local_file(tmp_path):
    proc, health = run_startup(tmp_path, tmp_path / "unused", {})
    assert proc.returncode == 0, proc.stderr
    assert "URL=sqlite:///./fiambreria.db" in proc.stdout
    assert (tmp_path / "fiambreria.db").stat().st_size > 0
    assert health["environment"] == "development"
    assert health["database_ready"] is True
    assert health["persistent_storage"] is False


def test_dev_config_fallback_unit():
    cfg = load_database_config(env={})
    assert cfg.url == "sqlite:///./fiambreria.db"
    assert cfg.is_production is False


# ---------- Validación de configuración en producción ----------

def test_production_without_database_url_fails(data_dir):
    with pytest.raises(DatabaseConfigError, match="requiere DATABASE_URL"):
        load_database_config(env={"APP_ENV": "production"}, data_dir=str(data_dir))


def test_production_without_database_url_startup_fails(tmp_path, data_dir):
    proc, _ = run_startup(tmp_path, data_dir, {"APP_ENV": "production"})
    assert proc.returncode != 0
    assert "requiere DATABASE_URL" in proc.stderr
    assert not (tmp_path / "fiambreria.db").exists()


def test_production_path_outside_data_dir_fails(tmp_path, data_dir):
    outside = tmp_path / "otro" / "fiambreria.db"
    with pytest.raises(DatabaseConfigError, match="dentro de"):
        load_database_config(env=prod_env(outside, allow_init=True), data_dir=str(data_dir))


def test_production_path_traversal_fails(data_dir):
    sneaky = f"{data_dir}/../fiambreria.db"
    with pytest.raises(DatabaseConfigError, match="dentro de"):
        load_database_config(env=prod_env(sneaky, allow_init=True), data_dir=str(data_dir))


def test_production_relative_sqlite_url_fails(data_dir):
    env = {"APP_ENV": "production", "DATABASE_URL": "sqlite:///./fiambreria.db",
           "ALLOW_DB_INITIALIZATION": "true"}
    with pytest.raises(DatabaseConfigError, match="ruta absoluta"):
        load_database_config(env=env, data_dir=str(data_dir))


def test_production_non_sqlite_url_fails(data_dir):
    env = {"APP_ENV": "production", "DATABASE_URL": "postgresql://u:p@h/db"}
    with pytest.raises(DatabaseConfigError, match="SQLite"):
        load_database_config(env=env, data_dir=str(data_dir))


def test_production_missing_data_dir_fails(tmp_path):
    missing = tmp_path / "no-existe"
    with pytest.raises(DatabaseConfigError, match="no existe"):
        load_database_config(env=prod_env(missing / "fiambreria.db", allow_init=True),
                             data_dir=str(missing))


def test_production_not_writable_data_dir_fails(data_dir, monkeypatch):
    # Se simula: el proceso de tests puede correr como root, que ignora permisos.
    monkeypatch.setattr(db_config, "_dir_is_writable", lambda d: False)
    with pytest.raises(DatabaseConfigError, match="no es escribible"):
        load_database_config(env=prod_env(data_dir / "fiambreria.db", allow_init=True),
                             data_dir=str(data_dir))


# ---------- Primer arranque y arranques normales ----------

def test_first_startup_with_allow_initialization_creates_db(tmp_path, data_dir):
    db = data_dir / "fiambreria.db"
    proc, health = run_startup(tmp_path, data_dir, prod_env(db, allow_init=True))
    assert proc.returncode == 0, proc.stderr
    assert db.stat().st_size > 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert sorted(r[0] for r in conn.execute("SELECT id FROM branches")) == [1, 2]
    conn.close()
    assert health == {
        "status": "ok", "environment": "production", "database_type": "sqlite",
        "database_configured": True, "database_ready": True, "persistent_storage": True,
    }
    # No se crea nada fuera del disco persistente.
    assert not (tmp_path / "fiambreria.db").exists()


def test_missing_db_without_allow_initialization_fails(tmp_path, data_dir):
    db = data_dir / "fiambreria.db"
    proc, _ = run_startup(tmp_path, data_dir, prod_env(db))
    assert proc.returncode != 0
    assert "no existe" in proc.stderr
    assert not db.exists(), "No debe crearse una base vacía en producción"


def test_zero_byte_db_fails_without_touching_it(tmp_path, data_dir):
    db = data_dir / "fiambreria.db"
    db.write_bytes(b"")
    proc, _ = run_startup(tmp_path, data_dir, prod_env(db))
    assert proc.returncode != 0
    assert "0 bytes" in proc.stderr
    assert db.stat().st_size == 0, "create_all no debe ejecutarse sobre la base vacía"


def test_zero_byte_db_fails_even_with_allow_initialization(data_dir):
    db = data_dir / "fiambreria.db"
    db.write_bytes(b"")
    with pytest.raises(DatabaseConfigError, match="0 bytes"):
        load_database_config(env=prod_env(db, allow_init=True), data_dir=str(data_dir))


def test_corrupt_db_fails(data_dir):
    db = data_dir / "fiambreria.db"
    db.write_bytes(b"esto no es una base sqlite" * 100)
    with pytest.raises(DatabaseConfigError, match="no puede abrirse"):
        load_database_config(env=prod_env(db), data_dir=str(data_dir))


def test_db_without_essential_tables_fails(data_dir):
    db = data_dir / "fiambreria.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE otra (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseConfigError, match="tablas esenciales"):
        load_database_config(env=prod_env(db), data_dir=str(data_dir))


def test_db_without_users_fails(initialized_db, data_dir):
    db = copy_db(initialized_db, data_dir / "fiambreria.db")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseConfigError, match="no tiene usuarios"):
        load_database_config(env=prod_env(db), data_dir=str(data_dir))


def test_db_without_initial_branches_fails(initialized_db, data_dir):
    db = copy_db(initialized_db, data_dir / "fiambreria.db")
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM branches WHERE id=2")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseConfigError, match="sucursales iniciales"):
        load_database_config(env=prod_env(db), data_dir=str(data_dir))


def test_valid_existing_db_starts(tmp_path, initialized_db, data_dir):
    db = copy_db(initialized_db, data_dir / "fiambreria.db")
    proc, health = run_startup(tmp_path, data_dir, prod_env(db))
    assert proc.returncode == 0, proc.stderr
    assert health["database_ready"] is True
    assert health["persistent_storage"] is True


def test_health_does_not_expose_url_or_path(tmp_path, initialized_db, data_dir):
    db = copy_db(initialized_db, data_dir / "fiambreria.db")
    _, health = run_startup(tmp_path, data_dir, prod_env(db))
    dumped = json.dumps(health)
    assert "sqlite:" not in dumped
    assert str(data_dir) not in dumped
    assert "fiambreria.db" not in dumped


# ---------- Backup ----------

def test_backup_is_consistent_with_sha_and_size(initialized_db, tmp_path):
    dest = tmp_path / "backup.db"
    result = db_config.backup_sqlite_database(str(initialized_db), str(dest))
    assert result["integrity_check"] == "ok"
    assert result["size_bytes"] > 0
    assert result["size_bytes"] == dest.stat().st_size
    assert result["sha256"] == hashlib.sha256(dest.read_bytes()).hexdigest()
    conn = sqlite3.connect(dest)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()


def test_backup_while_source_has_open_connection(initialized_db, tmp_path):
    src = copy_db(initialized_db, tmp_path / "live.db")
    writer = sqlite3.connect(src)
    writer.execute("CREATE TABLE IF NOT EXISTS tmp_rows (v INTEGER)")
    writer.executemany("INSERT INTO tmp_rows VALUES (?)", [(i,) for i in range(500)])
    writer.commit()
    result = db_config.backup_sqlite_database(str(src), str(tmp_path / "copy.db"))
    writer.close()
    conn = sqlite3.connect(tmp_path / "copy.db")
    assert conn.execute("SELECT COUNT(*) FROM tmp_rows").fetchone()[0] == 500
    conn.close()
    assert result["integrity_check"] == "ok"


def test_backup_refuses_overwrite_and_same_path(initialized_db, tmp_path):
    dest = tmp_path / "exists.db"
    dest.write_bytes(b"x")
    with pytest.raises(DatabaseConfigError, match="ya existe"):
        db_config.backup_sqlite_database(str(initialized_db), str(dest))
    with pytest.raises(DatabaseConfigError, match="no puede ser la base de origen"):
        db_config.backup_sqlite_database(str(initialized_db), str(initialized_db))


def test_backup_fails_on_empty_source(tmp_path):
    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    with pytest.raises(DatabaseConfigError, match="vacía"):
        db_config.backup_sqlite_database(str(empty), str(tmp_path / "out.db"))
    assert not (tmp_path / "out.db").exists()
