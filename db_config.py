"""Configuración y protección de la base SQLite.

- Desarrollo (APP_ENV distinto de "production"): usa DATABASE_URL o, si no existe,
  sqlite:///./fiambreria.db (comportamiento histórico).
- Producción (APP_ENV=production): DATABASE_URL es obligatoria, debe ser SQLite con
  ruta absoluta dentro de PRODUCTION_DATA_DIR (Persistent Disk de Render), y la base
  debe existir y ser válida. Solo con ALLOW_DB_INITIALIZATION=true se permite crearla
  cuando todavía no existe (primer arranque de un disco vacío).

Este módulo no importa nada de main.py y solo usa la biblioteca estándar.
"""
import hashlib
import os
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from typing import Optional

PRODUCTION_DATA_DIR = "/var/data"
DEV_DEFAULT_DATABASE_URL = "sqlite:///./fiambreria.db"
SQLITE_ABSOLUTE_PREFIX = "sqlite:////"

# Tablas que una base real de producción debe tener antes de arrancar.
ESSENTIAL_TABLES = (
    "users",
    "branches",
    "branch_products",
    "products",
    "product_lots",
    "sales",
    "sale_items",
    "cash_sessions",
    "cash_movements",
)
# Sucursales iniciales: 1 = Fiambrería Local, 2 = Feria Damyale.
EXPECTED_BRANCH_IDS = (1, 2)


class DatabaseConfigError(RuntimeError):
    """Configuración o estado de la base inválido para arrancar."""


@dataclass(frozen=True)
class DatabaseConfig:
    url: str
    is_production: bool
    sqlite_path: Optional[str]          # ruta del archivo SQLite (None si no es SQLite)
    allow_initialization: bool
    data_dir: Optional[str]             # directorio persistente esperado (solo producción)
    initialization_pending: bool = False  # True si este arranque va a crear la base


def _log(message: str) -> None:
    print(f"[DB] {message}", file=sys.stderr, flush=True)


def _env_flag_true(value: Optional[str]) -> bool:
    return (value or "").strip().lower() == "true"


def _is_production(env) -> bool:
    return (env.get("APP_ENV") or "").strip().lower() == "production"


def sqlite_path_from_url(url: str) -> Optional[str]:
    """Devuelve la ruta del archivo para URLs sqlite:///... (None si no es SQLite de archivo)."""
    if not url.startswith("sqlite:///"):
        return None
    path = url[len("sqlite:///"):]
    if not path or path == ":memory:":
        return None
    return path


def _dir_is_writable(directory: str) -> bool:
    """Prueba real de escritura: crea y borra un archivo temporal."""
    probe = os.path.join(directory, f".write-test-{uuid.uuid4().hex}")
    try:
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _validate_production_url(url: str, data_dir: str) -> str:
    if not url.startswith(SQLITE_ABSOLUTE_PREFIX):
        raise DatabaseConfigError(
            "En producción DATABASE_URL debe ser SQLite con ruta absoluta "
            f"(ej: sqlite:///{data_dir}/fiambreria.db, con cuatro barras)."
        )
    if "?" in url:
        raise DatabaseConfigError("DATABASE_URL de producción no debe incluir parámetros (?...).")
    path = os.path.normpath(url[len("sqlite:///"):])
    root = os.path.normpath(data_dir)
    if os.path.commonpath([path, root]) != root or path == root:
        raise DatabaseConfigError(
            f"En producción la base debe estar dentro de {data_dir} (Persistent Disk)."
        )
    if not os.path.isdir(root):
        raise DatabaseConfigError(
            f"El directorio persistente {data_dir} no existe. ¿Está montado el Persistent Disk?"
        )
    if not _dir_is_writable(root):
        raise DatabaseConfigError(f"El directorio persistente {data_dir} no es escribible.")
    if not os.path.isdir(os.path.dirname(path)):
        raise DatabaseConfigError("El directorio de la base dentro del disco persistente no existe.")
    return path


def validate_existing_database(path: str) -> dict:
    """Comprueba que el archivo sea una base real utilizable. Nunca crea el archivo."""
    if not os.path.exists(path):
        raise DatabaseConfigError(
            "La base de producción no existe. No se crea una base nueva automáticamente "
            "(para un disco vacío usar ALLOW_DB_INITIALIZATION=true solo en el primer arranque)."
        )
    if not os.path.isfile(path):
        raise DatabaseConfigError("La ruta de la base de producción no es un archivo.")
    if os.path.getsize(path) == 0:
        raise DatabaseConfigError("La base de producción tiene 0 bytes.")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = [t for t in ESSENTIAL_TABLES if t not in tables]
            if missing:
                raise DatabaseConfigError(
                    f"La base de producción no tiene tablas esenciales: {', '.join(missing)}."
                )
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            branch_ids = {r[0] for r in conn.execute("SELECT id FROM branches")}
        finally:
            conn.close()
    except DatabaseConfigError:
        raise
    except sqlite3.Error as exc:
        raise DatabaseConfigError(f"La base de producción no puede abrirse: {exc}") from exc
    if users < 1:
        raise DatabaseConfigError("La base de producción no tiene usuarios.")
    missing_branches = [b for b in EXPECTED_BRANCH_IDS if b not in branch_ids]
    if missing_branches:
        raise DatabaseConfigError(
            f"La base de producción no tiene las sucursales iniciales: {missing_branches}."
        )
    return {"users": users, "branches": sorted(branch_ids)}


def load_database_config(env=None, data_dir: Optional[str] = None) -> DatabaseConfig:
    """Resuelve la URL y, en producción, valida configuración, ruta y estado de la base.

    Se ejecuta ANTES de crear tablas: si la base no puede usarse, falla aquí.
    """
    env = os.environ if env is None else env
    data_dir = PRODUCTION_DATA_DIR if data_dir is None else data_dir
    url = (env.get("DATABASE_URL") or "").strip()

    if not _is_production(env):
        url = url or DEV_DEFAULT_DATABASE_URL
        return DatabaseConfig(url=url, is_production=False, sqlite_path=sqlite_path_from_url(url),
                              allow_initialization=True, data_dir=None)

    if not url:
        raise DatabaseConfigError("APP_ENV=production requiere DATABASE_URL.")
    path = _validate_production_url(url, data_dir)
    allow_init = _env_flag_true(env.get("ALLOW_DB_INITIALIZATION"))

    if not os.path.exists(path):
        if not allow_init:
            validate_existing_database(path)  # lanza el error explícito de base inexistente
        _log("ALLOW_DB_INITIALIZATION=true y la base no existe: se inicializará una base nueva "
             "en el disco persistente. Quitar ALLOW_DB_INITIALIZATION después de este arranque.")
        return DatabaseConfig(url=url, is_production=True, sqlite_path=path,
                              allow_initialization=True, data_dir=data_dir,
                              initialization_pending=True)

    # La base existe: siempre debe ser válida, aunque la inicialización esté permitida.
    validate_existing_database(path)
    if allow_init:
        _log("ALLOW_DB_INITIALIZATION=true pero la base ya existe: se usa la existente. "
             "Quitar ALLOW_DB_INITIALIZATION de la configuración.")
    return DatabaseConfig(url=url, is_production=True, sqlite_path=path,
                          allow_initialization=allow_init, data_dir=data_dir)


def verify_database_after_init(config: DatabaseConfig) -> None:
    """En producción, confirma que la base quedó válida tras la inicialización/migraciones."""
    if not config.is_production:
        return
    validate_existing_database(config.sqlite_path)
    if config.initialization_pending:
        _log("Base de producción inicializada correctamente.")


def database_health(config: DatabaseConfig, engine) -> dict:
    """Diagnóstico sin secretos: no devuelve la URL ni rutas completas."""
    ready = False
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        ready = True
    except Exception:
        ready = False
    persistent = bool(
        config.is_production
        and config.sqlite_path
        and config.data_dir
        and os.path.commonpath([os.path.normpath(config.sqlite_path), os.path.normpath(config.data_dir)])
        == os.path.normpath(config.data_dir)
        and os.path.isdir(config.data_dir)
    )
    return {
        "status": "ok" if ready else "error",
        "environment": "production" if config.is_production else "development",
        "database_type": "sqlite" if config.url.startswith("sqlite") else "other",
        "database_configured": bool(config.url),
        "database_ready": ready,
        "persistent_storage": persistent,
    }


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_sqlite_database(source_path: str, dest_path: str) -> dict:
    """Copia consistente con sqlite3.Connection.backup() y verificación de integridad.

    No sobrescribe un destino existente. Si la copia no pasa integrity_check se elimina
    y se lanza un error.
    """
    if not os.path.isfile(source_path) or os.path.getsize(source_path) == 0:
        raise DatabaseConfigError("La base de origen no existe o está vacía.")
    if os.path.abspath(source_path) == os.path.abspath(dest_path):
        raise DatabaseConfigError("El destino del backup no puede ser la base de origen.")
    if os.path.exists(dest_path):
        raise DatabaseConfigError("El archivo de destino del backup ya existe.")

    tmp_path = f"{dest_path}.tmp-{uuid.uuid4().hex}"
    try:
        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(tmp_path)
            try:
                src.backup(dst)
                result = dst.execute("PRAGMA integrity_check").fetchall()
            finally:
                dst.close()
        finally:
            src.close()
        if [r[0] for r in result] != ["ok"]:
            raise DatabaseConfigError(f"El backup no pasó integrity_check: {result[:5]}")
        os.replace(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return {
        "path": dest_path,
        "size_bytes": os.path.getsize(dest_path),
        "sha256": _sha256_file(dest_path),
        "integrity_check": "ok",
    }
