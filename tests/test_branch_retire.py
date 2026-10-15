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


@pytest.mark.parametrize("price", [None, 0.0])
def test_reactivate_requires_explicit_branch_price(price):
    pid = make_product(configs=((2, price, False),))
    r = client.post(f"/branches/2/products/{pid}/reactivate", json={})
    assert r.status_code == 409
    assert r.headers["X-Error-Code"] == "BRANCH_PRICE_REQUIRED_FOR_REACTIVATION"
    assert isinstance(r.json()["detail"], str)
    assert config_row(pid, 2)["is_available"] is False
    assert audit_rows(pid) == []


def test_admin_list_exposes_configured_branch_price_without_global_fallback():
    priced = make_product("Con precio")
    unpriced = make_product("Sin precio", configs=((2, None, False),))
    assert admin_row(2, priced)["branch_price_configured"] == 5000.0
    row = admin_row(2, unpriced)
    assert row["branch_price_configured"] is None
    assert row["branch_price"] == 4000            # el campo histórico sigue mostrando el respaldo global


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


def ingress_trace(product_id):
    """Todo lo que un ingreso crea o modifica, para verificar que un rechazo no deja rastros."""
    db = main.SessionLocal()
    p = db.query(main.ProductDB).filter_by(id=product_id).first()
    data = {
        "ingresses": db.query(main.ProductIngressDB).filter_by(product_id=product_id).count(),
        "lots": db.query(main.ProductLotDB).filter_by(product_id=product_id).count(),
        "movements": db.query(main.StockMovementDB).filter_by(product_id=product_id).count(),
        "product": (p.stock, p.cost_price, p.previous_cost_price, p.supplier, p.is_active),
        "audits": len(audit_rows(product_id)),
    }
    db.close()
    return data


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


def test_retired_with_price_and_confirmation_ingresses_and_reactivates():
    pid = make_product(configs=((2, 5000.0, False),))
    before = ingress_trace(pid)
    r = ingress(pid, reactivate_branch_product=True)
    assert r.status_code == 200, r.text
    after = ingress_trace(pid)
    assert (after["ingresses"], after["lots"], after["movements"]) == (1, 1, 1)
    assert after["audits"] == before["audits"] + 1
    assert config_row(pid, 2) == {"is_available": True, "price": 5000.0}
    assert any(p["id"] == pid and p["price_per_unit"] == 5000.0
               for p in client.get("/products?branch_id=2").json())


@pytest.mark.parametrize("price", [0.0, None])
def test_retired_without_valid_price_rejects_ingress_atomically(price):
    pid = make_product(configs=((2, price, False),))
    before = ingress_trace(pid)
    r = ingress(pid, reactivate_branch_product=True)
    assert r.status_code == 409
    assert r.headers["X-Error-Code"] == "BRANCH_PRICE_REQUIRED_FOR_REACTIVATION"
    assert isinstance(r.json()["detail"], str)
    assert ingress_trace(pid) == before           # sin ingreso, lote, movimiento, auditoría ni cambios en producto
    assert ingress_trace(pid)["ingresses"] == 0 and ingress_trace(pid)["lots"] == 0
    assert ingress_trace(pid)["movements"] == 0
    assert config_row(pid, 2) == {"is_available": False, "price": price}   # sigue RETIRADO, precio intacto
    assert admin_row(2, pid)["branch_status"] == "RETIRADO"


@pytest.mark.parametrize("price", [5000.0, 0.0, None])
def test_retired_without_confirmation_still_returns_retired_code(price):
    pid = make_product(configs=((2, price, False),))
    before = ingress_trace(pid)
    r = ingress(pid)
    assert r.status_code == 409
    assert r.headers["X-Error-Code"] == "PRODUCT_RETIRED_IN_BRANCH"
    assert ingress_trace(pid) == before
    assert config_row(pid, 2)["is_available"] is False


def test_ingress_on_available_and_unconfigured_products_is_unchanged():
    available = make_product("Disponible")
    assert ingress(available).status_code == 200
    unconfigured = make_product("Nuevo en Feria", configs=())
    assert ingress(unconfigured).status_code == 200       # comportamiento previo: lo habilita
    assert config_row(unconfigured, 2)["is_available"] is True
    assert audit_rows(available) == [] and audit_rows(unconfigured) == []


# --- PUT /branches/{b}/products/{p}: misma regla RETIRADO -> DISPONIBLE ------------------------

def config_full(product_id, branch_id):
    db = main.SessionLocal()
    row = db.query(main.BranchProductDB).filter_by(product_id=product_id, branch_id=branch_id).first()
    data = None if row is None else {"is_available": bool(row.is_available), "price": row.price_per_unit,
                                     "minimum_stock": row.minimum_stock, "is_exclusive": bool(row.is_exclusive)}
    db.close()
    return data


def put_config(branch_id, product_id, **payload):
    return client.put(f"/branches/{branch_id}/products/{product_id}", json=payload)


def retired_product(price):
    """Producto RETIRADO en Feria con stock mínimo 2; el precio global es 4000 (make_product)."""
    pid = make_product(configs=((2, price, False),))
    db = main.SessionLocal()
    db.query(main.BranchProductDB).filter_by(product_id=pid, branch_id=2).update({"minimum_stock": 2.0})
    db.commit()
    db.close()
    return pid


def test_put_reactivation_with_valid_new_price_succeeds():
    pid = retired_product(5000.0)
    r = put_config(2, pid, price_per_unit=6000, is_available=True, is_exclusive=False)
    assert r.status_code == 200, r.text
    assert config_full(pid, 2) == {"is_available": True, "price": 6000.0, "minimum_stock": 2.0,
                                   "is_exclusive": False}
    assert audit_rows(pid)[-1][:2] == ("False", "True")
    assert any(p["id"] == pid and p["price_per_unit"] == 6000.0
               for p in client.get("/products?branch_id=2").json())


def test_put_reactivation_without_price_uses_own_branch_price_never_global():
    pid = retired_product(5000.0)
    r = put_config(2, pid, is_available=True)
    assert r.status_code == 200, r.text
    assert config_full(pid, 2)["price"] == 5000.0          # conserva el propio; no lo pisa con el global 4000
    assert config_full(pid, 2)["is_available"] is True


@pytest.mark.parametrize("stored_price,sent", [
    (5000.0, {"price_per_unit": 0}),          # precio enviado = 0 (aunque la sucursal tenía 5000)
    (None, {"price_per_unit": None}),         # NULL explícito y sin precio propio
    (None, {}),                               # sin precio enviado ni propio: no usa el global 4000
    (0.0, {}),                                # precio propio 0: no usa el global 4000
])
def test_put_reactivation_without_valid_own_price_is_rejected_without_changes(stored_price, sent):
    pid = retired_product(stored_price)
    before = config_full(pid, 2)
    r = put_config(2, pid, is_available=True, minimum_stock=9, is_exclusive=True, **sent)
    assert r.status_code == 409
    assert r.headers["X-Error-Code"] == "BRANCH_PRICE_REQUIRED_FOR_REACTIVATION"
    assert isinstance(r.json()["detail"], str)
    assert config_full(pid, 2) == before                   # ni disponibilidad, ni precio, ni mínimo, ni exclusivo
    assert before["is_available"] is False
    assert audit_rows(pid) == []
    assert all(p["id"] != pid for p in client.get("/products?branch_id=2").json())


def test_put_on_retired_product_that_stays_retired_is_allowed():
    pid = retired_product(None)
    r = put_config(2, pid, price_per_unit=7000, minimum_stock=3, is_available=False, is_exclusive=False)
    assert r.status_code == 200, r.text
    assert config_full(pid, 2) == {"is_available": False, "price": 7000.0, "minimum_stock": 3.0,
                                   "is_exclusive": False}
    assert audit_rows(pid) == []
    # Con el precio ya cargado, ahora sí puede reactivarse.
    assert client.post(f"/branches/2/products/{pid}/reactivate", json={}).status_code == 200


def test_put_keeps_previous_behavior_for_available_and_never_configured_products():
    available = make_product("Disponible")
    r = put_config(2, available, minimum_stock=1, is_available=True)
    assert r.status_code == 200
    assert config_full(available, 2)["price"] == 4000.0    # comportamiento previo, fuera del alcance de Fase 1
    never = make_product("Nunca configurado", configs=())
    r = put_config(2, never, is_available=True)
    assert r.status_code == 200
    assert config_full(never, 2) == {"is_available": True, "price": 4000.0, "minimum_stock": None,
                                     "is_exclusive": False}
