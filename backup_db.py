"""Backup preventivo de la base SQLite (sin importar main.py: no crea ni modifica tablas).

Uso en Render (Shell del servicio), antes de desplegar cambios:

    python backup_db.py
    python backup_db.py --dest-dir /var/data/backups --label pre-fase1

- Lee DATABASE_URL (o la URL de desarrollo por defecto).
- Copia con sqlite3 backup() + PRAGMA integrity_check (db_config.backup_sqlite_database).
- Nunca sobrescribe un backup existente ni borra backups anteriores.
- Se niega a copiar si el espacio libre no alcanza para dos veces el tamaño de la base
  (el disco persistente es de 1 GB).
"""
import argparse
import datetime
import json
import os
import shutil
import sys

import db_config

DEFAULT_BACKUP_SUBDIR = "backups"


def resolve_db_path(url=None):
    url = url or os.environ.get("DATABASE_URL") or db_config.DEV_DEFAULT_DATABASE_URL
    path = db_config.sqlite_path_from_url(url)
    if not path:
        raise db_config.DatabaseConfigError("DATABASE_URL no es una base SQLite de archivo.")
    return os.path.abspath(path)


def existing_backups(dest_dir):
    if not os.path.isdir(dest_dir):
        return []
    files = [os.path.join(dest_dir, f) for f in sorted(os.listdir(dest_dir)) if f.endswith(".db")]
    return [{"file": os.path.basename(f), "size_bytes": os.path.getsize(f)} for f in files]


def run_backup(db_path, dest_dir=None, label=None, now=None):
    if not os.path.isfile(db_path):
        raise db_config.DatabaseConfigError("La base de origen no existe.")
    dest_dir = os.path.abspath(dest_dir or os.path.join(os.path.dirname(db_path), DEFAULT_BACKUP_SUBDIR))

    # El espacio se mide antes de crear nada (en el destino o, si todavía no existe, junto a la base).
    db_size = os.path.getsize(db_path)
    free = shutil.disk_usage(dest_dir if os.path.isdir(dest_dir) else os.path.dirname(db_path)).free
    if free < db_size * 2:
        raise db_config.DatabaseConfigError(
            f"Espacio libre insuficiente para un backup seguro: libre {free} bytes, base {db_size} bytes.")
    os.makedirs(dest_dir, exist_ok=True)

    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c for c in (label or "") if c.isalnum() or c in "-_")[:40]
    name = f"fiambreria-{stamp}{'-' + safe_label if safe_label else ''}.db"
    result = db_config.backup_sqlite_database(db_path, os.path.join(dest_dir, name))
    backups = existing_backups(dest_dir)
    result.update({
        "source_size_bytes": db_size,
        "free_bytes_after": shutil.disk_usage(dest_dir).free,
        "backups_count": len(backups),
        "backups_total_bytes": sum(b["size_bytes"] for b in backups),
    })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backup verificado de la base SQLite.")
    parser.add_argument("--dest-dir", default=None, help="Carpeta destino (por defecto <carpeta de la base>/backups)")
    parser.add_argument("--label", default=None, help="Etiqueta corta para el nombre del archivo")
    args = parser.parse_args(argv)
    try:
        result = run_backup(resolve_db_path(), args.dest_dir, args.label)
    except db_config.DatabaseConfigError as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
