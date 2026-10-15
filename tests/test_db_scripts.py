"""Scripts de soporte de Fase 1: backup preventivo y diagnóstico de solo lectura.

No importan main.py: trabajan sobre una base SQLite mínima creada en el test.
"""
import hashlib
import os
import sqlite3
import sys

import pytest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)

import backup_db  # noqa: E402
import db_config  # noqa: E402
import diagnose_db  # noqa: E402

SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, hashed_password TEXT, role TEXT, is_active BOOLEAN);
CREATE TABLE branches (id INTEGER PRIMARY KEY, name TEXT, code TEXT, is_active BOOLEAN);
CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, brand TEXT, barcode TEXT, stock FLOAT,
                       is_active BOOLEAN, price_per_unit FLOAT);
CREATE TABLE branch_products (id INTEGER PRIMARY KEY, branch_id INTEGER, product_id INTEGER,
                              price_per_unit FLOAT, is_available BOOLEAN);
CREATE TABLE product_lots (id INTEGER PRIMARY KEY, branch_id INTEGER, product_id INTEGER, cost_price FLOAT,
                           initial_qty FLOAT, current_qty FLOAT, expiration_date TEXT);
CREATE TABLE product_ingresses (id INTEGER PRIMARY KEY, branch_id INTEGER, product_id INTEGER, received_at TEXT);
CREATE TABLE stock_movements (id INTEGER PRIMARY KEY, lot_id INTEGER, reference_type TEXT, reference_id INTEGER);
CREATE TABLE sales (id INTEGER PRIMARY KEY, branch_id INTEGER, created_at TEXT, total_amount FLOAT);
CREATE TABLE sale_items (id INTEGER PRIMARY KEY, sale_id INTEGER, product_id INTEGER, quantity FLOAT,
                         unit_price FLOAT, cogs FLOAT);
"""


def build_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO users VALUES (?,?,?,?,?)", [
        (1, "admin@fiambreria.com", "admin123", "masteradmin", 1),
        (2, "vende@test", "otra", "vendedor", 1),
        (3, "Admin@Fiambreria.com", "x", "vendedor", 0),       # variante del email maestro
        (4, "intruso@test", "y", "superadmin", 1)])
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", [
        (1, "Miel 500 ml", "Arturo", "7790000000011", 0, 1, 2000),
        (2, "Miel 500 kg", None, "7790000000011", 0, 1, 2000),   # EAN duplicado
        (3, "agua 500 ml", "Placer", None, 0, 1, 900),
        (4, "Agua 500 ml ", "Villamanaos", None, 0, 1, 900),       # mismo nombre, otra marca
        (5, "Legado", None, None, 12, 1, 100)])                   # stock global sin partidas
    conn.executemany("INSERT INTO branch_products VALUES (?,?,?,?,?)", [
        (1, 2, 1, 2500, 0),       # retirado con stock
        (2, 2, 3, None, 1)])      # disponible sin precio de sucursal
    conn.executemany("INSERT INTO product_lots VALUES (?,?,?,?,?,?,?)", [
        (1, 2, 1, 1200, 8, 8, "2026-12-10"),
        (2, 2, 3, 0, 5, 5, "10/12/2026"),      # sin costo y fecha no ISO
        (3, 1, 4, 500, 3, -1, None)])          # saldo negativo
    conn.execute("INSERT INTO product_ingresses VALUES (1, 2, 1, '2026-10-01')")   # sin movimiento trazable
    conn.execute("INSERT INTO sales VALUES (1, 2, '2026-10-02', 9999)")
    conn.execute("INSERT INTO sale_items VALUES (1, 1, 1, 2, 2500, 0)")              # total != ítems, sin COGS
    conn.commit()
    conn.close()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_diagnostics_detects_issues_without_writing(tmp_path):
    db = str(tmp_path / "f.db")
    build_db(db)
    before = sha(db)
    report = diagnose_db.run_diagnostics(db, limit=None)
    assert sha(db) == before                                   # solo lectura
    s = report["summary"]
    assert s["duplicate_barcodes"] == 1
    assert s["duplicate_names"] == 1                           # "agua 500 ml" x2 (marcas distintas)
    assert s["retired_with_stock"] == 1
    assert s["available_without_branch_price"] == 1
    assert s["negative_lot_qty"] == 1
    assert s["non_iso_expiration"] == 1
    assert s["lots_without_cost"] == 1
    assert s["global_stock_without_lots"] == 1
    assert s["ingresses_without_traceable_lot"] == 1
    assert s["sale_total_mismatch"] == 1
    assert s["sale_items_without_cogs"] == 1
    retired = next(c for c in report["checks"] if c["check"] == "retired_with_stock")["examples"][0]
    assert retired["stock"] == 8 and retired["value_at_cost"] == 9600
    sec = report["security"]
    assert sec["default_master_password_in_use"] is True
    assert sec["plaintext_password_storage"] is True
    assert sec["master_email_variants"] == ["Admin@Fiambreria.com"]
    assert {u["email"] for u in sec["admin_level_users"]} == {"admin@fiambreria.com", "intruso@test"}
    assert sec["unknown_roles"] == []
    assert all(set(u) == {"email", "role", "is_active"} for u in sec["admin_level_users"])
    assert "admin123" not in str(report)                      # nunca muestra contraseñas
    assert report["integrity_quick_check"] == ["ok"]


def test_diagnostics_tolerates_missing_tables(tmp_path):
    db = str(tmp_path / "parcial.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, brand TEXT, barcode TEXT)")
    conn.commit()
    conn.close()
    report = diagnose_db.run_diagnostics(db)
    assert report["summary"]["duplicate_barcodes"] == 0
    assert report["skipped"]


def test_backup_creates_verified_copy_and_never_overwrites(tmp_path):
    import datetime
    db = str(tmp_path / "data" / "fiambreria.db")
    os.makedirs(os.path.dirname(db))
    build_db(db)
    now = datetime.datetime(2026, 10, 15, 22, 0, 0)
    result = backup_db.run_backup(db, label="pre fase1!", now=now)
    assert result["integrity_check"] == "ok"
    assert os.path.basename(result["path"]) == "fiambreria-20261015-220000-prefase1.db"
    assert os.path.dirname(result["path"]) == os.path.join(os.path.dirname(db), "backups")
    copy = sqlite3.connect(result["path"])
    assert copy.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 5
    copy.close()
    assert result["backups_count"] == 1
    with pytest.raises(db_config.DatabaseConfigError):
        backup_db.run_backup(db, label="pre fase1!", now=now)


def test_backup_refuses_when_disk_is_too_small(tmp_path, monkeypatch):
    db = str(tmp_path / "f.db")
    build_db(db)
    monkeypatch.setattr(backup_db.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(db_config.DatabaseConfigError):
        backup_db.run_backup(db)
    assert not os.path.exists(tmp_path / "backups")
