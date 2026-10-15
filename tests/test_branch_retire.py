"""Fase 1: Retirar / Reactivar producto por sucursal e Ingresos sin reactivación silenciosa.

Usan la base SQLite temporal de test_day_plans_api (main.py se importa una sola vez por proceso).
"""
import sqlite3

import pytest

from test_day_plans_api import _DB_PATH, add_lot, client, main  # noqa: E402

TABLES = ("audit_events", "presale_items", "presales", "sale_items", "sales", "cash_movements",
          "cash_sessions", "product_ingresses", "stock_movements", "product_lots", "branch_products",
          "promotion_branches", "promotion_items", "promotions", "inventory_counts", "products")


@pytest.fixture(autouse=True)
def clean_db():
    assert main.SQLALCHEMY_DATABASE_URL.endswith(_DB_PATH)
    conn = sqlite3.connect(_DB_PATH)
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    yield


def make_product(name="Queso Tybo", unit="kg", barcode=None, configs=((2, 5000.0, True),)):
    """configs: (branch_id, price, available) por sucursal configurada."""
    db = main.SessionLocal()
    p = main.ProductDB(name=name, price_per_unit=4000, unit_type=unit, cost_price=0, barcode=barcode,
                       is_active=True)
    db.add(p)
    db.flush()
    for branch_id, price, available in configs:
        db.add(main.BranchProductDB(branch_id=branch_id, product_id=p.id, price_per_unit=price,
                                    is_available=available))
    db.commit()
    pid = p.id
    db.close()
    return pid


def config_row(product_id, branch_id):
    db = main.SessionLocal()
    row = db.query(main.BranchProductDB).filter_by(product_id=product_id, branch_id=branch_id).first()
    data = None if row is None else {"is_available": bool(row.is_available), "price": row.price_per_unit}
    db.close()
    return data


def lots_of(product_id, branch_id):
    db = main.SessionLocal()
    rows = db.query(main.ProductLotDB).filter_by(product_id=product_id, branch_id=branch_id).all()
    data = [(l.initial_qty, l.current_qty, l.cost_price) for l in rows]
    db.close()
    return data


def audit_rows(product_id):
    db = main.SessionLocal()
    rows = db.query(main.AuditEventDB).filter(
        main.AuditEventDB.entity_type == "branch_product",
        main.AuditEventDB.field_name.like(f"%_product_{product_id}")).order_by(main.AuditEventDB.id).all()
    data = [(r.old_value, r.new_value, r.actor, r.reason) for r in rows]
    db.close()
    return data


def admin_row(branch_id, product_id):
    rows = client.get(f"/branches/{branch_id}/products").json()
    return next(r for r in rows if r["product_id"] == product_id)


def retire(branch_id, product_id, **params):
    return client.delete(f"/branches/{branch_id}/products/{product_id}", params=params)


# --- Listado de administración -------------------------------------------------------------

def test_admin_list_reports_branch_status():
    available = make_product("Disponible")
    retired = make_product("Retirado", configs=((2, 5000.0, False),))
    not_enabled = make_product("Sin configurar", configs=())
    assert admin_row(2, available)["branch_status"] == "DISPONIBLE"
    assert admin_row(2, retired)["branch_status"] == "RETIRADO"
    assert admin_row(2, retired)["is_available"] is False
    assert admin_row(2, not_enabled)["branch_status"] == "NO_HABILITADO"


# --- Retirar ----------------------------------------------------------------------------------

def test_retire_without_stock_hides_product_but_keeps_master_and_history():
    pid = make_product(barcode="7790000000017")
    r = retire(2, pid, actor="dueno@test", reason="Ya no se vende en Feria")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_available"] is False and body["branch_status"] == "RETIRADO"
    assert body["already_retired"] is False

    assert config_row(pid, 2)["is_available"] is False
    assert admin_row(2, pid)["branch_status"] == "RETIRADO"          # sigue visible en Administración
    assert all(p["id"] != pid for p in client.get("/products?branch_id=2").json())          # Caja / Cartelería
    assert all(p["id"] != pid for p in client.get("/products/search?q=Queso&branch_id=2").json())
    assert client.get("/products/by-barcode/7790000000017?branch_id=2").status_code == 404
    assert client.get("/products/master/by-barcode/7790000000017").status_code == 200   # maestro intacto

    assert audit_rows(pid) == [("True", "False", "dueno@test", "Ya no se vende en Feria")]


def test_retired_product_cannot_be_presold_or_sold():
    pid = make_product()
    add_lot(pid, 5, 1000, branch_id=2)
    assert retire(2, pid, confirm_stock="true").status_code == 200
    r = client.post("/presales?branch_id=2", json={"items": [{"product_id": pid, "quantity": 1}]})
    assert r.status_code == 409
    r = client.post("/sales/finalize?branch_id=2", json={
        "items": [{"product_id": pid, "quantity": 1}], "total_amount": 5000, "amount_cash": 5000,
        "amount_mp": 0, "payment_method": "Efectivo"})
    assert r.status_code == 409
    # El rechazo no consumió stock.
    assert lots_of(pid, 2) == [(5, 5, 1000)]


def test_retire_with_stock_requires_confirmation_and_keeps_lots():
    pid = make_product()
    add_lot(pid, 2.5, 1000, branch_id=2)
    add_lot(pid, 1.5, 1200, branch_id=2)

    preview = client.get(f"/branches/2/products/{pid}/retire-preview").json()
    assert preview["requires_confirmation"] is True
    assert preview["stock"] == 4.0
    assert preview["stock_value_at_cost"] == 4300.0      # 2,5 x 1000 + 1,5 x 1200
    assert preview["cost_reliable"] is True
    assert preview["branch_status"] == "DISPONIBLE"

    r = retire(2, pid)
    assert r.status_code == 409
    assert r.headers["X-Error-Code"] == "RETIRE_REQUIRES_CONFIRMATION"
    assert isinstance(r.json()["detail"], str) and "4 kg" in r.json()["detail"]
    assert config_row(pid, 2)["is_available"] is True     # sin confirmación no cambia nada
    assert audit_rows(pid) == []

    r = retire(2, pid, confirm_stock="true", actor="dueno@test")
    assert r.status_code == 200, r.text
    assert r.json()["stock"] == 4.0
    assert config_row(pid, 2)["is_available"] is False
    assert sorted(lots_of(pid, 2)) == [(1.5, 1.5, 1200), (2.5, 2.5, 1000)]   # partidas intactas
    assert "stock al retirar: 4.0" in audit_rows(pid)[0][3]


def test_preview_marks_value_unreliable_when_a_lot_has_no_cost():
    pid = make_product()
    add_lot(pid, 2, 1000, branch_id=2)
    add_lot(pid, 1, 0, branch_id=2)
    preview = client.get(f"/branches/2/products/{pid}/retire-preview").json()
    assert preview["stock"] == 3.0
    assert preview["cost_reliable"] is False
    assert preview["stock_value_at_cost"] is None


def test_retire_is_idempotent_and_only_affects_that_branch():
    pid = make_product(configs=((1, 4500.0, True), (2, 5000.0, True)))
    assert retire(2, pid).status_code == 200
    again = retire(2, pid)
    assert again.status_code == 200 and again.json()["already_retired"] is True
    assert len(audit_rows(pid)) == 1
    assert config_row(pid, 1)["is_available"] is True
    assert any(p["id"] == pid for p in client.get("/products?branch_id=1").json())


def test_retire_unconfigured_product_returns_404():
    pid = make_product(configs=())
    assert retire(2, pid).status_code == 404


# --- Reactivar --------------------------------------------------------------------------------

def test_reactivate_restores_availability_with_branch_price():
    pid = make_product()
    retire(2, pid)
    r = client.post(f"/branches/2/products/{pid}/reactivate", json={"actor": "dueno@test"})
    assert r.status_code == 200, r.text
    assert r.json()["branch_status"] == "DISPONIBLE" and r.json()["price_per_unit"] == 5000.0
    sold = next(p for p in client.get("/products?branch_id=2").json() if p["id"] == pid)
    assert sold["price_per_unit"] == 5000.0
    assert audit_rows(pid)[-1][:3] == ("False", "True", "dueno@test")


def test_reactivate_requires_explicit_branch_price():
    pid = make_product(configs=((2, None, False),))
    r = client.post(f"/branches/2/products/{pid}/reactivate", json={})
    assert r.status_code == 409
    assert config_row(pid, 2)["is_available"] is False


def test_reactivate_is_idempotent_and_404_when_not_configured():
    pid = make_product()
    r = client.post(f"/branches/2/products/{pid}/reactivate")
    assert r.status_code == 200 and r.json()["already_available"] is True
    assert audit_rows(pid) == []
    other = make_product("Otro", configs=())
    assert client.post(f"/branches/2/products/{other}/reactivate").status_code == 404


# --- Ingresos ---------------------------------------------------------------------------------

def ingress(pid, **extra):
    payload = {"product_id": pid, "supplier": "Arturo", "cost_price": 1500, "quantity": 7,
               "received_by": "recibe@test", **extra}
    return client.post("/ingresses?branch_id=2", json=payload)


def test_ingress_on_retired_product_requires_explicit_reactivation():
    pid = make_product()
    retire(2, pid)
    r = ingress(pid)
    assert r.status_code == 409
    assert r.headers["X-Error-Code"] == "PRODUCT_RETIRED_IN_BRANCH"
    assert isinstance(r.json()["detail"], str)
    assert lots_of(pid, 2) == []                          # no se registró nada
    assert config_row(pid, 2)["is_available"] is False

    r = ingress(pid, reactivate_branch_product=True)
    assert r.status_code == 200, r.text
    assert lots_of(pid, 2) == [(7, 7, 1500)]
    assert config_row(pid, 2)["is_available"] is True
    assert audit_rows(pid)[-1][:3] == ("False", "True", "recibe@test")


def test_ingress_on_available_and_unconfigured_products_is_unchanged():
    available = make_product("Disponible")
    assert ingress(available).status_code == 200
    unconfigured = make_product("Nuevo en Feria", configs=())
    assert ingress(unconfigured).status_code == 200       # comportamiento previo: lo habilita
    assert config_row(unconfigured, 2)["is_available"] is True
    assert audit_rows(available) == [] and audit_rows(unconfigured) == []
