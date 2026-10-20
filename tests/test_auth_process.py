"""Fase 2.1 — pruebas en procesos separados (main.py se inicializa al importarse):

- el bloqueo por intentos sobrevive a un reinicio del proceso;
- una base con el esquema previo a 2.1 migra de forma aditiva e idempotente, sin crear ni tocar
  credenciales al arrancar; la conversión a hash solo ocurre con manage_users migrate-credentials;
- en producción no hay /docs, /redoc ni /openapi.json, la semilla no tiene contraseña utilizable,
  la credencial de una base existente no se modifica al arrancar y el script de Render Shell
  recupera el MASTERADMIN mostrando la clave temporal una sola vez.

Nunca usan /var/data ni la base real: todo ocurre en directorios temporales.
"""
import json
import os
import sqlite3
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRONG = "salame tandilero 2026"
CLEAN_ENV_KEYS = ("APP_ENV", "DATABASE_URL", "ALLOW_DB_INITIALIZATION", "TRUSTED_PROXY_HOPS", "AUTH_IP_LIMIT_MODE")

PRELUDE = """
import json, sys
sys.path.insert(0, {repo!r})
import db_config
db_config.PRODUCTION_DATA_DIR = {data_dir!r}
import main, auth_core, manage_users
from fastapi.testclient import TestClient
c = TestClient(main.app)
def login(email, password):
    r = c.post("/login", data={{"username": email, "password": password}})
    return {{"status": r.status_code, "code": r.headers.get("X-Error-Code"),
             "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else None}}
def q(sql, params=None):
    db = main.SessionLocal()
    try:
        return [list(r) for r in db.execute(main.text(sql), params or {{}}).fetchall()]
    finally:
        db.close()
out = {{}}
"""


def run(script, cwd, env_extra, data_dir=""):
    env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_KEYS}
    env.update(env_extra)
    code = PRELUDE.format(repo=REPO_DIR, data_dir=str(data_dir)) + script + "\nprint('OUT=' + json.dumps(out, default=str))\n"
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-3000:]
    line = [l for l in proc.stdout.splitlines() if l.startswith("OUT=")][-1]
    return json.loads(line[len("OUT="):]), proc


def dev_env(db_path):
    return {"DATABASE_URL": f"sqlite:///{db_path}"}


def prod_env(db_path, allow_init=False):
    env = {"APP_ENV": "production", "DATABASE_URL": f"sqlite:///{db_path}"}
    if allow_init:
        env["ALLOW_DB_INITIALIZATION"] = "true"
    return env


# --- Bloqueo persistente -----------------------------------------------------------------------------

def test_account_lock_survives_process_restart(tmp_path):
    db = tmp_path / "f.db"
    out, _ = run(f"""
db = main.SessionLocal()
u = main.UserDB(name="x", email="lock@proc.test", role="vendedor", is_active=True,
                hashed_password=auth_core.unusable_legacy_value())
db.add(u); db.flush()
main.set_user_password(db, u, {STRONG!r}, False, None, main._auth_utcnow()); db.commit(); db.close()
out["fails"] = [login("lock@proc.test", "incorrecta incorrecta")["status"] for _ in range(5)]
""", tmp_path, dev_env(db))
    assert out["fails"] == [400] * 5
    # Proceso nuevo (como un reinicio de Render): el bloqueo sigue vigente.
    out, _ = run(f'out["after_restart"] = login("lock@proc.test", {STRONG!r})', tmp_path, dev_env(db))
    assert out["after_restart"]["status"] == 429 and out["after_restart"]["code"] == "ACCOUNT_LOCKED"


# --- Migración sobre una base previa a 2.1 ------------------------------------------------------------

OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, email VARCHAR NOT NULL UNIQUE,
  hashed_password VARCHAR NOT NULL, role VARCHAR, is_active BOOLEAN, can_preventa BOOLEAN, can_caja BOOLEAN,
  can_stock BOOLEAN, can_ingreso BOOLEAN, can_alertas BOOLEAN, can_rrhh BOOLEAN, can_mrp BOOLEAN,
  can_verificacion BOOLEAN, can_kpis BOOLEAN, can_carteleria BOOLEAN, can_edit_records BOOLEAN, branch_id INTEGER);
CREATE TABLE branches (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, code VARCHAR NOT NULL UNIQUE, is_active BOOLEAN);
CREATE TABLE audit_events (id INTEGER PRIMARY KEY, entity_type VARCHAR NOT NULL, entity_id INTEGER,
  field_name VARCHAR, old_value TEXT, new_value TEXT, actor VARCHAR, reason VARCHAR, created_at DATETIME);
INSERT INTO branches VALUES (1, 'Fiambrería Local', 'FIAMBRERIA', 1), (2, 'Feria Damyale', 'FERIA', 1);
INSERT INTO users (id, name, email, hashed_password, role, is_active, branch_id) VALUES
  (1, 'Super Admin', 'admin@fiambreria.com', 'admin123', 'masteradmin', 1, 1),
  (2, 'Empleado', 'emp@proc.test', 'clave vieja empleado', 'vendedor', 1, 2),
  (3, 'Baja', 'baja@proc.test', 'clave de la baja', 'vendedor', 0, 1);
INSERT INTO audit_events (entity_type, entity_id, field_name, old_value, new_value, actor, reason, created_at)
  VALUES ('product', 7, 'price_per_unit', '100', '120', 'alguien', 'historico', '2026-09-01 10:00:00');
"""


def test_old_database_migrates_additively_and_idempotently(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    startup = """
out["credentials"] = q("SELECT COUNT(*) FROM user_credentials")[0][0]
out["plaintext"] = q("SELECT email, hashed_password FROM users ORDER BY id")
out["audit_cols"] = sorted(r[1] for r in q("PRAGMA table_info(audit_events)"))
out["audit_old"] = q("SELECT entity_type, actor, reason, actor_user_id, actor_source FROM audit_events")
out["migrations"] = q("SELECT version FROM schema_migrations")
out["tables"] = sorted(r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'"))
"""
    first, _ = run(startup, tmp_path, dev_env(db))
    # Arrancar NO crea credenciales ni modifica las contraseñas heredadas.
    assert first["credentials"] == 0
    assert first["plaintext"] == [["admin@fiambreria.com", "admin123"], ["emp@proc.test", "clave vieja empleado"],
                                  ["baja@proc.test", "clave de la baja"]]
    assert {"actor_user_id", "actor_source", "session_id"} <= set(first["audit_cols"])
    assert first["audit_old"] == [["product", "alguien", "historico", None, None]]
    assert first["migrations"] == [["2.1-auth"]]
    assert {"user_credentials", "auth_sessions", "auth_login_failures", "schema_migrations"} <= set(first["tables"])

    second, _ = run(startup, tmp_path, dev_env(db))
    assert second == first                                          # reiniciar no cambia nada

    migrate = """
db = main.SessionLocal()
out["dry"] = manage_users.migrate_credentials(db, main, dry_run=True)
out["after_dry"] = q("SELECT COUNT(*) FROM user_credentials")[0][0]
out["run1"] = manage_users.migrate_credentials(db, main)
out["hashes1"] = q("SELECT user_id, password_hash, must_change_password, change_reason FROM user_credentials ORDER BY user_id")
out["run2"] = manage_users.migrate_credentials(db, main)
out["hashes2"] = q("SELECT user_id, password_hash, must_change_password, change_reason FROM user_credentials ORDER BY user_id")
db.close()
out["plaintext"] = q("SELECT hashed_password FROM users ORDER BY id")
out["admin"] = login("admin@fiambreria.com", "admin123")
out["emp"] = login("emp@proc.test", "clave vieja empleado")
out["baja"] = login("baja@proc.test", "clave de la baja")
"""
    m, _ = run(migrate, tmp_path, dev_env(db))
    assert m["dry"]["migrated"] == 3 and m["dry"]["dry_run"] is True and m["after_dry"] == 0
    assert m["run1"] == {"users": 3, "already_migrated": 0, "migrated": 3, "without_usable_password": 0,
                         "dry_run": False}
    assert m["run2"]["migrated"] == 0 and m["run2"]["already_migrated"] == 3
    assert m["hashes1"] == m["hashes2"]                               # idempotente: no re-hashea
    assert [h[2:] for h in m["hashes1"]] == [[1, "DEFAULT_PASSWORD"], [1, "LEGACY_PLAINTEXT"], [1, "LEGACY_PLAINTEXT"]]
    assert all(h[1].startswith("pbkdf2_sha256$600000$") for h in m["hashes1"])
    assert m["plaintext"] == [["admin123"], ["clave vieja empleado"], ["clave de la baja"]]   # U8=A
    assert m["admin"]["status"] == 200 and m["admin"]["body"]["password_change_reason"] == "DEFAULT_PASSWORD"
    assert m["emp"]["status"] == 200 and m["emp"]["body"]["must_change_password"] is True
    assert m["emp"]["body"]["allowed_branch_ids"] == [2]
    assert m["baja"]["status"] == 403 and m["baja"]["code"] == "USER_DISABLED"


# --- Producción -----------------------------------------------------------------------------------------

PROD_CHECKS = """
out["docs"] = [c.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")]
out["health"] = c.get("/health").json()
out["master"] = q("SELECT role, is_active, substr(hashed_password, 1, 10) FROM users WHERE email = 'admin@fiambreria.com'")
out["credentials"] = q("SELECT COUNT(*) FROM user_credentials")[0][0]
out["settings"] = main.auth_settings()
out["admin123"] = login("admin@fiambreria.com", "admin123")
"""


def test_production_has_no_docs_and_seed_has_no_usable_password(tmp_path):
    data_dir = tmp_path / "var-data"
    data_dir.mkdir()
    db = data_dir / "fiambreria.db"
    out, _ = run(PROD_CHECKS + """
db = main.SessionLocal()
user, temporary = manage_users.reset_password(db, main, "admin@fiambreria.com")
db.close()
out["temp_ok"] = login("admin@fiambreria.com", temporary)
""", tmp_path, prod_env(db, allow_init=True), data_dir)
    assert out["docs"] == [404, 404, 404]
    assert out["health"]["status"] == "ok" and out["health"]["database_ready"] is True
    assert out["master"] == [["masteradmin", 1, "!unusable:"]]
    assert out["credentials"] == 0
    assert out["settings"] == {"trusted_proxy_hops": 0, "ip_limit_mode": "observe"}   # no bloquea por IP
    assert out["admin123"]["status"] == 400                                            # sin clave conocida
    assert out["temp_ok"]["status"] == 200
    assert out["temp_ok"]["body"]["password_change_reason"] == "TEMPORARY"


def _simulate_current_production(tmp_path):
    """Base de producción con la cuenta maestra como hoy: admin123 en texto plano, sin credencial."""
    data_dir = tmp_path / "var-data"
    data_dir.mkdir()
    db = data_dir / "fiambreria.db"
    run("pass", tmp_path, prod_env(db, allow_init=True), data_dir)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET hashed_password = 'admin123' WHERE email = 'admin@fiambreria.com'")
    conn.execute("DELETE FROM user_credentials")
    conn.commit()
    conn.close()
    return data_dir, db


def test_existing_production_credential_is_not_modified_on_startup(tmp_path):
    data_dir, db = _simulate_current_production(tmp_path)
    out, _ = run(PROD_CHECKS + """
out["credentials_after_login"] = q("SELECT COUNT(*) FROM user_credentials")[0][0]
out["plaintext_after"] = q("SELECT hashed_password FROM users WHERE email = 'admin@fiambreria.com'")
out["sessions"] = q("SELECT ip, peer_ip, ip_source FROM auth_sessions")
""", tmp_path, prod_env(db), data_dir)
    assert out["credentials"] == 0 and out["credentials_after_login"] == 0
    assert out["plaintext_after"] == [["admin123"]]
    assert out["admin123"]["status"] == 200
    assert out["admin123"]["body"]["must_change_password"] is True
    assert out["admin123"]["body"]["password_change_reason"] == "DEFAULT_PASSWORD"
    assert out["sessions"] == [["testclient", "testclient", "peer"]]


def test_render_shell_reset_shows_temporary_password_once(tmp_path):
    data_dir, db = _simulate_current_production(tmp_path)
    env = prod_env(db)
    env_list = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV_KEYS}
    env_list.update(env)
    cli = (f"import sys; sys.path.insert(0, {REPO_DIR!r}); import db_config; "
           f"db_config.PRODUCTION_DATA_DIR = {str(data_dir)!r}; import manage_users; "
           "sys.exit(manage_users.main_cli(sys.argv[1:]))")
    proc = subprocess.run([sys.executable, "-c", cli, "reset-password", "--email", "admin@fiambreria.com", "--yes"],
                          cwd=str(tmp_path), env=env_list, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    lines = proc.stdout.strip().splitlines()
    temporary = lines[-1].strip()
    assert len(temporary) >= 16 and proc.stdout.count(temporary) == 1 and temporary not in proc.stderr

    out, _ = run(f"""
out["old"] = login("admin@fiambreria.com", "admin123")
out["temp"] = login("admin@fiambreria.com", {temporary!r})
out["audit"] = q("SELECT actor, actor_source, old_value, new_value, reason FROM audit_events WHERE field_name = 'password'")
out["stored"] = q("SELECT password_hash FROM user_credentials")
out["plaintext"] = q("SELECT hashed_password FROM users WHERE email = 'admin@fiambreria.com'")
""", tmp_path, env, data_dir)
    assert out["old"]["status"] == 400                       # la credencial nueva manda; admin123 ya no entra
    assert out["temp"]["status"] == 200 and out["temp"]["body"]["password_change_reason"] == "TEMPORARY"
    assert out["audit"][0][:4] == ["render-shell", "cli", None, None]
    assert temporary not in json.dumps(out["audit"]) and temporary not in json.dumps(out["stored"])
    assert out["plaintext"] == [["admin123"]]                # U8=A: se borra en un paso posterior

    listing = subprocess.run([sys.executable, "-c", cli, "list-admins"], cwd=str(tmp_path), env=env_list,
                             capture_output=True, text=True, timeout=180)
    assert listing.returncode == 0 and "admin@fiambreria.com" in listing.stdout
    assert "pbkdf2" not in listing.stdout and "admin123" not in listing.stdout and temporary not in listing.stdout
