"""Diagnóstico de SOLO LECTURA del estado de la base (no importa main.py, no escribe nada).

Uso en Render (Shell del servicio):

    python diagnose_db.py            # resumen + primeros ejemplos de cada hallazgo
    python diagnose_db.py --full     # todos los ejemplos

Abre la base con mode=ro. No muestra contraseñas: solo informa si la cuenta maestra
sigue usando la contraseña por defecto y cuántas cuentas guardan la clave en texto plano.
"""
import argparse
import json
import os
import sqlite3
import sys

import db_config

MASTER_EMAIL = "admin@fiambreria.com"
DEFAULT_MASTER_PASSWORD = "admin123"
EXAMPLES_LIMIT = 10


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _check(name, rows, description, severity, limit):
    return {
        "check": name,
        "severity": severity,
        "description": description,
        "count": len(rows),
        "examples": rows if limit is None else rows[:limit],
    }


def run_diagnostics(db_path, limit=EXAMPLES_LIMIT):
    if not os.path.isfile(db_path):
        raise db_config.DatabaseConfigError("La base no existe.")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = _tables(conn)
        checks = []
        skipped = []

        def need(*names):
            missing = [t for t in names if t not in tables]
            if missing:
                skipped.append({"missing_tables": missing})
            return not missing

        if need("products"):
            checks.append(_check("duplicate_barcodes", _rows(conn, """
                SELECT TRIM(barcode) AS barcode, COUNT(*) AS products, GROUP_CONCAT(id) AS product_ids
                FROM products WHERE barcode IS NOT NULL AND TRIM(barcode) <> ''
                GROUP BY TRIM(barcode) HAVING COUNT(*) > 1 ORDER BY products DESC"""),
                "Mismo EAN en más de un producto (bloquea crear un índice único).", "ALTA", limit))
            checks.append(_check("duplicate_names", _rows(conn, """
                SELECT LOWER(TRIM(name)) AS name, COUNT(*) AS products, GROUP_CONCAT(id) AS product_ids,
                       GROUP_CONCAT(COALESCE(brand, '-'), ' | ') AS brands
                FROM products GROUP BY LOWER(TRIM(name)) HAVING COUNT(*) > 1 ORDER BY products DESC"""),
                "Productos con el mismo nombre (pueden ser marcas distintas o duplicados reales).", "MEDIA", limit))

        if need("branch_products"):
            checks.append(_check("duplicate_branch_configs", _rows(conn, """
                SELECT branch_id, product_id, COUNT(*) AS rows_count FROM branch_products
                GROUP BY branch_id, product_id HAVING COUNT(*) > 1"""),
                "Más de una configuración del mismo producto en la misma sucursal.", "ALTA", limit))
            checks.append(_check("available_without_branch_price", _rows(conn, """
                SELECT bp.branch_id, bp.product_id, p.name FROM branch_products bp
                JOIN products p ON p.id = bp.product_id
                WHERE bp.is_available = 1 AND p.is_active = 1
                  AND (bp.price_per_unit IS NULL OR bp.price_per_unit <= 0)"""),
                "Disponible en la sucursal sin precio propio > 0 (hoy usa el precio global o vende a 0).", "MEDIA", limit))

        if need("branch_products", "product_lots", "products"):
            checks.append(_check("retired_with_stock", _rows(conn, """
                SELECT bp.branch_id, bp.product_id, p.name, ROUND(SUM(l.current_qty), 3) AS stock,
                       ROUND(SUM(l.current_qty * COALESCE(l.cost_price, 0)), 2) AS value_at_cost
                FROM branch_products bp
                JOIN products p ON p.id = bp.product_id
                JOIN product_lots l ON l.product_id = bp.product_id AND l.branch_id = bp.branch_id
                WHERE bp.is_available = 0 AND l.current_qty > 0
                GROUP BY bp.branch_id, bp.product_id"""),
                "Productos RETIRADOS que todavía tienen stock inmovilizado en esa sucursal.", "INFO", limit))

        if need("product_lots"):
            checks.append(_check("negative_lot_qty", _rows(conn, """
                SELECT id, product_id, branch_id, current_qty FROM product_lots WHERE current_qty < -0.000001"""),
                "Partidas con saldo negativo.", "ALTA", limit))
            checks.append(_check("lot_qty_above_initial", _rows(conn, """
                SELECT id, product_id, branch_id, initial_qty, current_qty FROM product_lots
                WHERE current_qty > initial_qty + 0.000001"""),
                "Partidas con saldo mayor a lo ingresado.", "MEDIA", limit))
            checks.append(_check("non_iso_expiration", _rows(conn, """
                SELECT id, product_id, branch_id, expiration_date FROM product_lots
                WHERE expiration_date IS NOT NULL AND TRIM(expiration_date) <> ''
                  AND expiration_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"""),
                "Vencimientos fuera de formato AAAA-MM-DD (FEFO los ordenaría mal).", "MEDIA", limit))
            checks.append(_check("lots_without_cost", _rows(conn, """
                SELECT id, product_id, branch_id, current_qty FROM product_lots
                WHERE current_qty > 0 AND (cost_price IS NULL OR cost_price <= 0)"""),
                "Partidas con saldo y costo 0: su COGS y su valor a costo no son confiables.", "MEDIA", limit))

        if need("products", "product_lots"):
            checks.append(_check("global_stock_without_lots", _rows(conn, """
                SELECT p.id, p.name, p.stock FROM products p
                WHERE p.is_active = 1 AND COALESCE(p.stock, 0) > 0.000001
                  AND NOT EXISTS (SELECT 1 FROM product_lots l WHERE l.product_id = p.id AND l.current_qty > 0)"""),
                "Stock global legado sin partidas: no se puede vender (el stock real sale de las partidas).", "INFO", limit))

        if need("product_ingresses", "stock_movements"):
            checks.append(_check("ingresses_without_traceable_lot", _rows(conn, """
                SELECT i.id, i.product_id, i.branch_id, i.received_at FROM product_ingresses i
                WHERE NOT EXISTS (SELECT 1 FROM stock_movements m WHERE m.reference_type = 'product_ingress'
                                  AND m.reference_id = i.id AND m.lot_id IS NOT NULL)"""),
                "Ingresos sin partida trazable (no podrán anularse automáticamente).", "MEDIA", limit))

        if need("sales", "sale_items"):
            checks.append(_check("sale_total_mismatch", _rows(conn, """
                SELECT s.id, s.branch_id, s.created_at, ROUND(s.total_amount, 2) AS total_amount,
                       ROUND(COALESCE(SUM(i.unit_price * i.quantity), 0), 2) AS items_revenue
                FROM sales s LEFT JOIN sale_items i ON i.sale_id = s.id
                GROUP BY s.id HAVING ABS(s.total_amount - COALESCE(SUM(i.unit_price * i.quantity), 0)) > 0.05
                ORDER BY s.id DESC"""),
                "Ventas cuyo total cobrado difiere de la suma de sus ítems.", "ALTA", limit))
            checks.append(_check("sale_items_without_cogs", _rows(conn, """
                SELECT i.id, i.sale_id, i.product_id, i.quantity FROM sale_items i
                WHERE COALESCE(i.cogs, 0) <= 0 AND COALESCE(i.quantity, 0) > 0 ORDER BY i.id DESC"""),
                "Ítems vendidos sin costo registrado (margen inflado en KPI).", "MEDIA", limit))

        security = {}
        if need("users"):
            cols = _columns(conn, "users")
            users = _rows(conn, "SELECT email, hashed_password, role, is_active FROM users")
            security = {
                "users_total": len(users),
                "users_active": sum(1 for u in users if u["is_active"]),
                # Hoy /login compara texto plano: todas las claves están guardadas así.
                "plaintext_password_storage": "password_hash" not in cols,
                "default_master_password_in_use": any(
                    (u["email"] or "").lower() == MASTER_EMAIL and u["hashed_password"] == DEFAULT_MASTER_PASSWORD
                    for u in users),
                "roles": {r: sum(1 for u in users if u["role"] == r) for r in sorted({u["role"] for u in users})},
                # Para revisar si alguien usó la escalada anónima previa al parche U1: cuentas
                # administrativas (sin contraseñas) y variantes del email maestro con otras mayúsculas.
                "admin_level_users": [
                    {"email": u["email"], "role": u["role"], "is_active": bool(u["is_active"])}
                    for u in users if u["role"] in ("masteradmin", "superadmin")],
                "master_email_variants": [
                    u["email"] for u in users
                    if (u["email"] or "").strip().lower() == MASTER_EMAIL and u["email"] != MASTER_EMAIL],
                "unknown_roles": sorted({u["role"] for u in users
                                         if u["role"] not in ("masteradmin", "superadmin", "dueno", "vendedor")},
                                        key=str),
            }

        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in sorted(tables) if not t.startswith("sqlite_")}
        integrity = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()]
    finally:
        conn.close()

    return {
        "database_size_bytes": os.path.getsize(db_path),
        "integrity_quick_check": integrity[:5],
        "table_counts": counts,
        "security": security,
        "checks": checks,
        "skipped": skipped,
        "summary": {c["check"]: c["count"] for c in checks},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Diagnóstico de solo lectura de la base.")
    parser.add_argument("--full", action="store_true", help="Mostrar todos los ejemplos")
    args = parser.parse_args(argv)
    url = os.environ.get("DATABASE_URL") or db_config.DEV_DEFAULT_DATABASE_URL
    path = db_config.sqlite_path_from_url(url)
    if not path:
        print(json.dumps({"status": "error", "detail": "DATABASE_URL no es SQLite de archivo"}))
        return 1
    try:
        report = run_diagnostics(os.path.abspath(path), None if args.full else EXAMPLES_LIMIT)
    except db_config.DatabaseConfigError as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", **report}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
