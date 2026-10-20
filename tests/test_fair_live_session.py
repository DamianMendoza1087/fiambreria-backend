"""Tests de la Jornada de Feria en vivo (abrir / seguir / cerrar un Day Plan).

Comparten la base SQLite temporal de test_day_plans_api (main.py se importa una sola vez
por proceso). La hora "actual" de la jornada se fija reemplazando main._fair_live_utc_now.

Nota: la validación de actor (usuario activo con rol masteradmin/superadmin/dueno) es una
comprobación de COHERENCIA, no autenticación real: el backend todavía no verifica tokens.
"""
import datetime
import json
import os
import sqlite3
import subprocess
import sys
from decimal import Decimal

import pytest

from test_day_plans_api import (  # noqa: E402  (fija DATABASE_URL temporal antes de importar main)
    REPO_DIR, _DB_PATH, _run, add_lot, add_product, client, main, weekend_payload,
)
import authkit  # noqa: E402
import fair_planning  # noqa: E402

SAT_OPEN = datetime.datetime(2026, 10, 17, 12, 0)    # sábado 09:00 AR
SAT_LATER = datetime.datetime(2026, 10, 17, 20, 0)   # sábado 17:00 AR
SUN_NOON = datetime.datetime(2026, 10, 18, 15, 0)    # domingo 12:00 AR
LIVE_TABLES = ("day_plan_snapshots", "day_plans", "fair_cost_configs", "sale_items", "sales",
               "cash_movements", "cash_sessions", "product_ingresses", "stock_movements", "product_lots",
               "branch_products", "promotion_branches", "promotion_items", "promotions", "products")


@pytest.fixture(autouse=True)
def clean_live_db():
    assert main.SQLALCHEMY_DATABASE_URL.endswith(_DB_PATH)
    conn = sqlite3.connect(_DB_PATH)
    for table in LIVE_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM users WHERE email LIKE '%@live.test'")
    conn.commit()
    conn.close()
    for email, role, active in (("dueno@live.test", "dueno", True), ("super@live.test", "superadmin", True),
                                ("vende@live.test", "vendedor", True), ("baja@live.test", "dueno", False)):
        add_user(email, role, active)
    yield


@pytest.fixture
def clock(monkeypatch):
    state = {"now": SAT_OPEN}
    monkeypatch.setattr(main, "_fair_live_utc_now", lambda: state["now"])
    return state


def add_user(email, role, active=True):
    db = main.SessionLocal()
    db.add(main.UserDB(name=email, email=email, hashed_password="x", role=role, is_active=active))
    db.commit()
    db.close()


def create_plan(**extra):
    payload = weekend_payload(**{"debt_target": 0, "profit_target": 0, **extra})
    r = client.post("/day-plans", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["plan"]["id"]


def single_day_plan(date="17/10/2026", plan_type="SABADO"):
    r = client.post("/day-plans", json={"branch_id": 2, "plan_type": plan_type, "start_date": date,
                                        "transport_outbound": 10000, "transport_return": 10000,
                                        "created_by": "test"})
    assert r.status_code == 200, r.text
    return r.json()["plan"]["id"]


def as_user(email):
    """Fase 2.2: quien abre o cierra es el usuario de la sesión (con acceso a ambas sucursales)."""
    return authkit.headers_for(main, email, branch_ids=[1, 2])


def open_plan(plan_id, actor="dueno@live.test"):
    return client.post(f"/day-plans/{plan_id}/open", json={"actor": actor}, headers=as_user(actor))


def close_plan(plan_id, actor="dueno@live.test"):
    return client.post(f"/day-plans/{plan_id}/close", json={"actor": actor}, headers=as_user(actor))


def live(plan_id):
    r = client.get(f"/day-plans/{plan_id}/live")
    assert r.status_code == 200, r.text
    return r.json()


def add_sale(amount, cogs, created_at, branch_id=2, product_id=None, items_revenue=None, promo=False):
    db = main.SessionLocal()
    sale = main.SaleDB(branch_id=branch_id, total_amount=amount, created_at=created_at)
    db.add(sale)
    db.flush()
    revenue = amount if items_revenue is None else items_revenue
    db.add(main.SaleItemDB(sale_id=sale.id, product_id=product_id, quantity=1, unit_price=revenue,
                           cogs=cogs, gross_profit=revenue - cogs, is_promotion=promo))
    db.commit()
    sid = sale.id
    db.close()
    return sid


def add_cash(category, amount, created_at, movement_type="OUT", branch_id=2):
    db = main.SessionLocal()
    db.add(main.CashMovementDB(branch_id=branch_id, movement_type=movement_type, category=category,
                               amount=amount, concept="test", created_at=created_at))
    db.commit()
    db.close()


def add_loss(quantity, unit_cost, created_at, branch_id=2, product_id=1):
    db = main.SessionLocal()
    db.add(main.StockMovementDB(branch_id=branch_id, product_id=product_id, movement_type="LOSS",
                                quantity=-quantity, unit_cost=unit_cost, created_at=created_at))
    db.commit()
    db.close()


# ---------- Apertura ----------

def test_open_valid_plan(clock):
    pid = create_plan()
    r = open_plan(pid)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ABIERTA" and data["source"] == "LIVE"
    assert data["opened_by"] == "dueno@live.test"
    assert data["opened_at"] == "2026-10-17T09:00:00-03:00"   # se presenta en hora argentina
    assert data["branch_name"] == "Feria Damyale"
    plan = client.get(f"/day-plans/{pid}").json()["plan"]
    assert plan["status"] == "ABIERTA" and plan["opened_at"].endswith("-03:00")
    # internamente se guarda en UTC, como sales.created_at
    conn = sqlite3.connect(_DB_PATH)
    stored = conn.execute("SELECT opened_at FROM day_plans WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    assert stored.startswith("2026-10-17 12:00:00")


def test_open_keeps_frozen_plan_amounts_even_if_config_changes(clock):
    pid = create_plan()
    before = client.get(f"/day-plans/{pid}").json()["plan"]
    assert open_plan(pid).status_code == 200
    client.put("/branches/2/fair-config", json={"stall_cost_per_day": 999999, "updated_by": "x"})
    data = live(pid)
    assert data["result"]["planned_operating_costs"] == before["operating_cost_total"] == 500000
    # un plan abierto ya no se puede editar ni recalcular
    assert client.put(f"/day-plans/{pid}", json={"other_expenses": 1}).status_code == 409
    assert client.post(f"/day-plans/{pid}/recalculate", json={}).status_code == 409


def test_open_only_within_plan_dates(clock):
    pid = create_plan()
    clock["now"] = datetime.datetime(2026, 10, 16, 20, 0)   # viernes AR
    r = open_plan(pid)
    assert r.status_code == 409 and "17/10/2026" in r.json()["detail"]
    clock["now"] = datetime.datetime(2026, 10, 19, 13, 0)   # lunes AR
    assert open_plan(pid).status_code == 409
    clock["now"] = datetime.datetime(2026, 10, 18, 2, 30)   # sábado 23:30 AR (domingo UTC)
    assert open_plan(pid).status_code == 200


def test_weekend_can_open_on_sunday_single_day_only_that_day(clock):
    pid = create_plan()
    clock["now"] = SUN_NOON
    assert open_plan(pid).status_code == 200
    assert close_plan(pid).status_code == 200
    sat = single_day_plan()
    assert open_plan(sat).status_code == 409   # domingo: un plan SABADO no se abre
    assert client.get(f"/day-plans/{sat}").json()["plan"]["can_open_today"] is False


def test_only_one_open_session(clock):
    first, second = create_plan(), create_plan()
    assert open_plan(first).status_code == 200
    r = open_plan(second)
    assert r.status_code == 409 and f"plan #{first}" in r.json()["detail"]
    assert client.get(f"/day-plans/{second}").json()["plan"]["status"] == "BORRADOR"
    current = client.get("/day-plans/open?branch_id=2").json()
    assert current["open"] is True and current["plan"]["id"] == first
    assert open_plan(first).status_code == 409   # reabrir la misma tampoco


def test_unique_partial_index_blocks_second_open_at_db_level(clock):
    first, second = create_plan(), create_plan()
    assert open_plan(first).status_code == 200
    conn = sqlite3.connect(_DB_PATH)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE day_plans SET status='ABIERTA' WHERE id=?", (second,))
    conn.execute("UPDATE day_plans SET status='CERRADA' WHERE id=?", (second,))  # otras no se bloquean
    conn.rollback()
    conn.close()


def test_cancelled_plan_cannot_open(clock):
    pid = create_plan()
    client.patch(f"/day-plans/{pid}/status", json={"status": "CANCELADA", "actor": "x"})
    r = open_plan(pid)
    assert r.status_code == 409 and "cancelado" in r.json()["detail"]


def test_status_patch_still_cannot_open_or_close(clock):
    pid = create_plan()
    for status in ("ABIERTA", "CERRADA"):
        assert client.patch(f"/day-plans/{pid}/status", json={"status": status}).status_code == 422


@pytest.mark.parametrize("actor,code", [("vende@live.test", 403), ("baja@live.test", 401)])
def test_only_owner_or_admin_session_can_open(clock, actor, code):
    # Fase 2.2: lo decide la sesión (vendedor -> 403 por rol; usuario deshabilitado -> 401).
    pid = create_plan()
    assert client.post(f"/day-plans/{pid}/open", json={"actor": actor}, headers=as_user(actor)).status_code == code
    assert client.get(f"/day-plans/{pid}").json()["plan"]["status"] == "BORRADOR"


def test_actor_in_body_is_ignored(clock):
    pid = create_plan()
    r = client.post(f"/day-plans/{pid}/open", json={"actor": "vende@live.test"}, headers=as_user("dueno@live.test"))
    assert r.status_code == 200 and r.json()["opened_by"] == "dueno@live.test"


def test_superadmin_can_open_and_close(clock):
    pid = create_plan()
    assert open_plan(pid, "SUPER@live.test").status_code == 200   # email sin distinguir mayúsculas
    assert close_plan(pid, "super@live.test").json()["closed_by"] == "super@live.test"


# ---------- Cierre ----------

def test_close_open_session_and_close_twice_rejected(clock):
    pid = create_plan()
    assert close_plan(pid).status_code == 409   # BORRADOR: no se cierra
    open_plan(pid)
    clock["now"] = SAT_LATER
    r = close_plan(pid)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "CERRADA" and data["source"] == "FINAL_SNAPSHOT"
    assert data["closed_at"] == "2026-10-17T17:00:00-03:00" and data["today"] is None
    assert data["elapsed_minutes"] == 480
    r2 = close_plan(pid)
    assert r2.status_code == 409 and "ya fue cerrada" in r2.json()["detail"]
    assert open_plan(pid).status_code == 409   # cerrada no se reabre
    assert client.patch(f"/day-plans/{pid}/status", json={"status": "CANCELADA"}).status_code == 409


def test_live_requires_started_session(clock):
    pid = create_plan()
    assert client.get(f"/day-plans/{pid}/live").status_code == 409


# ---------- Ventas de la jornada ----------

def test_sales_window_branch_and_time(clock):
    pid = create_plan()
    add_sale(1000, 600, SAT_OPEN - datetime.timedelta(minutes=1))           # antes de abrir
    add_sale(5000, 3000, SAT_OPEN + datetime.timedelta(hours=1), branch_id=1)  # Fiambrería Local
    open_plan(pid)
    add_sale(10000, 6000, SAT_OPEN)                                        # justo al abrir: entra
    add_sale(20000, 12000, SAT_OPEN + datetime.timedelta(hours=2))
    clock["now"] = SAT_LATER
    data = live(pid)
    assert data["result"]["revenue"] == 30000 and data["accumulated"]["sales_count"] == 2
    assert data["result"]["cogs"] == 18000 and data["result"]["gross_margin"] == 12000
    add_sale(7000, 1, SAT_LATER + datetime.timedelta(minutes=5))          # después de "ahora"
    assert live(pid)["result"]["revenue"] == 30000
    close_plan(pid)
    add_sale(99999, 1, SAT_LATER + datetime.timedelta(minutes=1))         # después del cierre
    assert live(pid)["result"]["revenue"] == 30000


def test_weekend_today_vs_accumulated_and_argentine_days(clock):
    pid = create_plan()
    open_plan(pid)
    add_sale(100000, 60000, SAT_OPEN + datetime.timedelta(hours=1))
    add_sale(50000, 30000, datetime.datetime(2026, 10, 18, 2, 30))   # sábado 23:30 AR
    add_sale(80000, 40000, datetime.datetime(2026, 10, 18, 13, 0))   # domingo 10:00 AR
    clock["now"] = datetime.datetime(2026, 10, 17, 23, 0)            # sábado: no obliga a cerrar
    sat = live(pid)
    assert sat["today"]["date"] == "2026-10-17" and sat["today"]["revenue"] == 100000
    clock["now"] = SUN_NOON
    data = live(pid)
    assert data["multi_day"] is True
    assert data["today"]["date"] == "2026-10-18" and data["today"]["revenue"] == 80000
    assert data["accumulated"]["revenue"] == 230000
    assert [(d["date"], d["revenue"]) for d in data["days"]] == [("2026-10-17", 150000), ("2026-10-18", 80000)]
    final = close_plan(pid).json()
    assert final["result"]["revenue"] == 230000 and len(final["days"]) == 2


def test_single_day_today_equals_accumulated(clock):
    pid = single_day_plan()
    open_plan(pid)
    add_sale(40000, 20000, SAT_OPEN + datetime.timedelta(hours=1))
    clock["now"] = SAT_LATER
    data = live(pid)
    assert data["multi_day"] is False
    assert data["today"]["revenue"] == data["accumulated"]["revenue"] == 40000


def test_open_and_close_do_not_touch_sales_or_stock(clock):
    pid = create_plan()
    prod = add_product("Salame")
    add_lot(prod, 10, 500)
    add_sale(1000, 500, SAT_OPEN + datetime.timedelta(hours=1))

    def snapshot():
        conn = sqlite3.connect(_DB_PATH)
        out = {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
               for t in ("sales", "sale_items", "product_lots", "stock_movements", "cash_movements")}
        conn.close()
        return out

    before = snapshot()
    open_plan(pid)
    clock["now"] = SAT_LATER
    live(pid)
    close_plan(pid)
    assert snapshot() == before


# ---------- Promociones y COGS histórico (con /sales/finalize real) ----------

def _real_now_plan():
    """Plan PERSONALIZADA que incluye hoy, para usar ventas reales de /sales/finalize."""
    today = main.ar_now().date()
    r = client.post("/day-plans", json={"branch_id": 2, "plan_type": "PERSONALIZADA",
                                        "start_date": today.strftime("%d/%m/%Y"),
                                        "end_date": (today + datetime.timedelta(days=1)).strftime("%d/%m/%Y"),
                                        "transport_outbound": 0, "transport_return": 0, "created_by": "t"})
    assert r.status_code == 200, r.text
    return r.json()["plan"]["id"]


def test_promotion_sale_uses_charged_total_and_real_component_cost():
    # Sin reloj fijo: /sales/finalize guarda created_at con la hora real.
    pid = _real_now_plan()
    assert open_plan(pid).status_code == 200
    jamon = add_product("Jamón promo", branch_price=10000)
    queso = add_product("Queso promo", branch_price=8000)
    add_lot(jamon, 5, 4000)
    add_lot(queso, 5, 3000)
    db = main.SessionLocal()
    promo = main.PromotionDB(name="Picada", active=True)
    db.add(promo)
    db.flush()
    db.add(main.PromotionItemDB(promotion_id=promo.id, product_id=jamon, included_qty=0.5))
    db.add(main.PromotionItemDB(promotion_id=promo.id, product_id=queso, included_qty=0.5))
    db.add(main.PromotionBranchDB(promotion_id=promo.id, branch_id=2, promo_price=7000, enabled=True))
    db.commit()
    promo_id = promo.id
    db.close()
    # Precio normal teórico = 0.5*10000 + 0.5*8000 = 9000; se cobra la promo: 7000.
    r = client.post("/sales/finalize?branch_id=2", json={
        "items": [{"product_id": jamon, "quantity": 0.5}, {"product_id": queso, "quantity": 0.5}],
        "total_amount": 7000, "amount_cash": 7000, "amount_mp": 0, "payment_method": "Efectivo",
        "sold_by": "t", "is_promotion": True, "promotion_id": promo_id})
    assert r.status_code == 200, r.text
    data = live(pid)
    assert data["result"]["revenue"] == 7000                 # cobrado, no 9000
    assert data["result"]["cogs"] == 3500                    # 0.5*4000 + 0.5*3000 (FEFO real)
    assert data["result"]["gross_margin"] == 3500
    assert data["accumulated"]["promo_sales_count"] == 1
    assert data["data_quality"]["sales_total_mismatches"] == []
    close_plan(pid)


def test_cogs_is_historical_not_current_cost():
    pid = _real_now_plan()
    assert open_plan(pid).status_code == 200
    prod = add_product("Bondiola", branch_price=5000)
    add_lot(prod, 10, 2000)
    r = client.post("/sales/finalize?branch_id=2", json={
        "items": [{"product_id": prod, "quantity": 2}], "total_amount": 10000, "amount_cash": 10000,
        "amount_mp": 0, "payment_method": "Efectivo", "sold_by": "t"})
    assert r.status_code == 200, r.text
    first = live(pid)["result"]
    assert first["cogs"] == 4000
    # Cambian costos y precios después de la venta: el resultado no se mueve.
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("UPDATE product_lots SET cost_price=9999 WHERE product_id=?", (prod,))
    conn.execute("UPDATE products SET cost_price=9999, price_per_unit=1 WHERE id=?", (prod,))
    conn.execute("UPDATE branch_products SET price_per_unit=1 WHERE product_id=?", (prod,))
    conn.commit()
    conn.close()
    assert live(pid)["result"] == first
    close_plan(pid)


def test_items_without_cost_and_total_mismatch_are_flagged(clock):
    pid = create_plan()
    open_plan(pid)
    add_sale(1000, 0, SAT_OPEN + datetime.timedelta(minutes=10))                       # sin costo
    sid = add_sale(5000, 2000, SAT_OPEN + datetime.timedelta(minutes=20), items_revenue=4500)
    clock["now"] = SAT_LATER
    data = live(pid)
    codes = {w["code"] for w in data["warnings"]}
    assert {"COSTO_INCOMPLETO", "DIFERENCIA_TOTAL_VENTA"} <= codes
    assert data["data_quality"]["complete"] is False and data["data_quality"]["items_without_cost"] == 1
    assert data["data_quality"]["sales_total_mismatches"] == [
        {"sale_id": sid, "total_amount": 5000, "items_revenue": 4500, "difference": 500}]
    assert data["result"]["revenue"] == 6000   # se usa el total cobrado, sin corregirlo


# ---------- Resultado: gastos, mermas, deuda y ganancia ----------

def test_operating_result_formula_with_expenses_and_losses(clock):
    pid = create_plan(debt_target=300000, profit_target=100000)   # opex fin de semana = 500000
    open_plan(pid)
    t = SAT_OPEN + datetime.timedelta(hours=1)
    add_sale(1000000, 600000, t)
    add_cash("OPERATIVO", 10000, t)        # hielo
    add_cash("COMPRA_MENOR", 5000, t)      # bolsas
    add_cash("SERVICIOS", 3000, t)
    add_cash("OTRO", 2000, t)
    add_cash("EMPLEADO", 100000, t)        # informativo: personal ya está en el plan
    add_cash("PROVEEDOR", 400000, t)       # mercadería: ya entra por COGS
    add_cash("RETIRO_DUENO", 50000, t)     # no es gasto
    add_cash("OTRO", 7777, t, movement_type="IN")   # ingreso de caja: no cuenta
    add_cash("OPERATIVO", 9999, t, branch_id=1)     # otra sucursal
    add_cash("OPERATIVO", 8888, SAT_OPEN - datetime.timedelta(hours=1))  # antes de abrir
    add_loss(2, 1500, t)                   # merma a costo: 3000
    add_loss(1, 0, t)                      # merma sin costo: no se inventa
    add_loss(5, 1000, SAT_OPEN - datetime.timedelta(hours=1))           # antes de abrir
    add_loss(5, 1000, t, branch_id=1)                                   # otra sucursal
    clock["now"] = SAT_LATER
    data = live(pid)
    res = data["result"]
    assert res == {"revenue": 1000000, "cogs": 600000, "gross_margin": 400000, "gross_margin_pct": 0.4,
                   "planned_operating_costs": 500000, "extra_expenses": 20000, "losses_at_cost": 3000,
                   "real_costs_total": 523000, "operating_result": -123000}
    acc = data["accumulated"]
    assert acc["employee_payments_info"] == 100000
    assert acc["supplier_payments_excluded"] == 400000 and acc["owner_withdrawals_excluded"] == 50000
    assert data["data_quality"]["losses_without_cost"] == 1
    assert "MERMA_SIN_COSTO" in {w["code"] for w in data["warnings"]}
    # Deuda y ganancia son objetivos: no aparecen dentro de los costos.
    obj = data["objectives"]
    assert obj["equilibrium"]["required_margin"] == 523000 and obj["equilibrium"]["missing"] == 123000
    assert obj["debt"]["required_margin"] == 823000 and obj["debt"]["missing"] == 423000
    assert obj["total"]["required_margin"] == 923000 and obj["total"]["missing"] == 523000
    assert obj["debt"]["plan_required_margin"] == 800000
    assert obj["equilibrium"]["estimated_sales_missing"] == 307500   # 123000 / 0.40
    assert data["indicator"]["code"] == "FALTA_EQUILIBRIO"


def test_debt_and_profit_are_not_operating_costs(clock):
    pid = create_plan(debt_target=300000, profit_target=50000)
    open_plan(pid)
    add_sale(1625000, 975000, SAT_OPEN + datetime.timedelta(hours=1))   # margen 650000
    clock["now"] = SAT_LATER
    data = live(pid)
    assert data["result"]["planned_operating_costs"] == 500000
    assert data["result"]["operating_result"] == 150000                 # 650000 - 500000
    assert data["objectives"]["debt"]["missing"] == 150000               # 300000 - 150000
    assert data["objectives"]["total"]["missing"] == 200000
    assert data["indicator"]["code"] == "EQUILIBRIO_ALCANZADO"
    codes = [o["code"] for o in data["outcomes"]]
    assert codes == ["GANANCIA_OPERATIVA", "OBJETIVO_DEUDA_NO_ALCANZADO", "OBJETIVO_TOTAL_NO_ALCANZADO"]
    assert data["outcomes"][1]["amount"] == 150000


def test_objectives_surpassed_never_negative_missing(clock):
    pid = create_plan(debt_target=100000, profit_target=100000)
    open_plan(pid)
    add_sale(2000000, 1000000, SAT_OPEN + datetime.timedelta(hours=1))   # margen 1.000.000
    clock["now"] = SAT_LATER
    data = live(pid)
    for key in ("equilibrium", "debt", "total"):
        assert data["objectives"][key]["missing"] == 0 and data["objectives"][key]["reached"] is True
    assert data["objectives"]["total"]["surplus"] == 300000
    assert data["indicator"]["code"] == "OBJETIVO_ALCANZADO"
    assert [o["code"] for o in data["outcomes"]] == [
        "GANANCIA_OPERATIVA", "OBJETIVO_DEUDA_ALCANZADO", "OBJETIVO_TOTAL_ALCANZADO"]


# ---------- Snapshot final ----------

def test_final_snapshot_is_immutable(clock):
    pid = create_plan(debt_target=1000)
    prod = add_product("Lomito")
    add_lot(prod, 10, 100)
    open_plan(pid)
    add_sale(50000, 20000, SAT_OPEN + datetime.timedelta(hours=1), product_id=prod)
    clock["now"] = SAT_LATER
    final = close_plan(pid).json()
    stored = live(pid)
    assert stored == final
    # Cambios posteriores: precio, costo, configuración, stock, producto, promo y hasta datos retroactivos.
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("UPDATE sale_items SET cogs=1, unit_price=1")
    conn.execute("UPDATE products SET cost_price=1, price_per_unit=1, name='Otro'")
    conn.execute("UPDATE product_lots SET current_qty=0, cost_price=1")
    conn.execute("UPDATE day_plans SET stall_total=1 WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    client.put("/branches/2/fair-config", json={"stall_cost_per_day": 1, "updated_by": "x"})
    add_sale(99999, 1, SAT_OPEN + datetime.timedelta(hours=2))
    add_cash("OPERATIVO", 5000, SAT_OPEN + datetime.timedelta(hours=2))
    clock["now"] = datetime.datetime(2026, 11, 1, 12, 0)
    assert live(pid) == final
    summary = [p for p in client.get("/day-plans?branch_id=2").json() if p["id"] == pid][0]["session_summary"]
    assert summary["source"] == "FINAL_SNAPSHOT" and summary["revenue"] == 50000
    conn = sqlite3.connect(_DB_PATH)
    version = conn.execute("SELECT final_calc_version FROM day_plans WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    assert version == fair_planning.LIVE_CALC_VERSION


def test_list_and_open_endpoint_summaries(clock):
    pid = create_plan()
    assert client.get("/day-plans/open?branch_id=2").json() == {"open": False, "plan": None}
    open_plan(pid)
    add_sale(10000, 4000, SAT_OPEN + datetime.timedelta(hours=1))
    clock["now"] = SAT_LATER
    item = [p for p in client.get("/day-plans?branch_id=2").json() if p["id"] == pid][0]
    assert item["session_summary"]["source"] == "LIVE" and item["session_summary"]["gross_margin"] == 6000
    assert "final_results_json" not in item and item["has_final_results"] is False
    assert client.get("/day-plans/open?branch_id=1").status_code == 422


def test_open_past_end_date_shows_warning_without_auto_close(clock):
    pid = create_plan()
    open_plan(pid)
    clock["now"] = datetime.datetime(2026, 10, 19, 15, 0)   # lunes
    data = live(pid)
    assert data["status"] == "ABIERTA"
    assert "ABIERTA_FUERA_DE_PERIODO" in {w["code"] for w in data["warnings"]}
    final = close_plan(pid).json()
    assert "CERRADA_FUERA_DE_PERIODO" in {w["code"] for w in final["warnings"]}


# ---------- Cálculo puro ----------

def test_pure_live_calc_without_sales():
    plan = {"stall_total": Decimal("100"), "staff_total": 0, "transport_outbound": 0, "transport_return": 0,
            "other_expenses": 0, "debt_target": 0, "profit_target": 0, "end_date": "2026-10-17"}
    day = datetime.date(2026, 10, 17)
    out = fair_planning.compute_live_session(plan, [], [], [], day, day, day)
    assert out["result"]["operating_result"] == -100 and out["result"]["gross_margin_pct"] is None
    assert out["objectives"]["equilibrium"]["missing"] == 100
    assert out["objectives"]["equilibrium"]["estimated_sales_missing"] is None
    assert out["objectives"]["debt"]["applies"] is False
    assert out["indicator"]["code"] == "FALTA_EQUILIBRIO"
    assert out["today"]["revenue"] == 0 and len(out["days"]) == 1
    json.dumps(out)   # serializable para el snapshot final


def test_pure_cash_categories_classification():
    plan = {"stall_total": 0, "staff_total": 0, "transport_outbound": 0, "transport_return": 0,
            "end_date": "2026-10-17"}
    day = datetime.date(2026, 10, 17)
    restan = ("OPERATIVO", "OPERATIVOS", "GASTO_OPERATIVO", "SERVICIOS", "SERVICIO", "COMPRA_MENOR",
              "OTRO", "OTROS", "CAJA_CHICA", "operativo")
    no_restan = ("PROVEEDOR", "RETIRO_DUENO", "EMPLEADO")
    movs = [{"date": day, "movement_type": "OUT", "category": c, "amount": 100} for c in restan + no_restan]
    out = fair_planning.compute_live_session(plan, [], movs, [], day, day, None)
    assert out["result"]["extra_expenses"] == 1000
    assert out["result"]["operating_result"] == -1000
    acc = out["accumulated"]
    assert acc["employee_payments_info"] == 100
    assert acc["supplier_payments_excluded"] == 100 and acc["owner_withdrawals_excluded"] == 100
    assert acc["unclassified_expenses"] == 0 and out["data_quality"]["unclassified_cash_movements"] == []
    assert "CATEGORIA_GASTO_NO_RECONOCIDA" not in {w["code"] for w in out["warnings"]}


def test_pure_unknown_cash_category_is_not_subtracted():
    plan = {"stall_total": 1000, "staff_total": 0, "transport_outbound": 0, "transport_return": 0,
            "end_date": "2026-10-17"}
    day = datetime.date(2026, 10, 17)
    movs = [{"date": day, "movement_type": "OUT", "category": "PUBLICIDAD", "amount": 1234.5},
            {"date": day, "movement_type": "OUT", "category": None, "amount": 100},
            {"date": day, "movement_type": "OUT", "category": "OPERATIVO", "amount": 50},
            {"date": day, "movement_type": "IN", "category": "PUBLICIDAD", "amount": 999}]  # ingresos: ignorados
    out = fair_planning.compute_live_session(plan, [], movs, [], day, day, day)
    # El resultado no cambia por la categoría desconocida: solo resta OPERATIVO.
    assert out["result"]["extra_expenses"] == 50
    assert out["result"]["operating_result"] == -1050
    assert out["objectives"]["equilibrium"]["required_margin"] == 1050
    # Visible como no clasificado, con monto y advertencia de calidad de datos.
    assert out["accumulated"]["unclassified_expenses"] == 1334.5
    assert out["accumulated"]["unclassified_count"] == 2
    assert out["today"]["unclassified_expenses"] == 1334.5
    dq = out["data_quality"]
    assert dq["complete"] is False and dq["unclassified_expenses"] == 1334.5
    assert dq["unclassified_cash_movements"] == [
        {"date": "2026-10-17", "category": "PUBLICIDAD", "amount": 1234.5},
        {"date": "2026-10-17", "category": None, "amount": 100.0}]
    w = [w for w in out["warnings"] if w["code"] == "CATEGORIA_GASTO_NO_RECONOCIDA"][0]
    assert w["amount"] == 1334.5
    assert "$1.334,50" in w["detail"] and "PUBLICIDAD" in w["detail"] and "NO se restaron" in w["detail"]


def test_unknown_category_via_api_does_not_change_result(clock):
    pid = create_plan()   # opex 500000
    open_plan(pid)
    t = SAT_OPEN + datetime.timedelta(hours=1)
    add_sale(800000, 400000, t)
    add_cash("SERVICIO", 1000, t)       # alias histórico: resta
    add_cash("CAJA_CHICA", 2000, t)     # alias histórico: resta
    add_cash("OPERATIVOS", 400, t)      # alias histórico de OPERATIVO: resta
    add_cash("GASTO_OPERATIVO", 300, t) # alias histórico de OPERATIVO: resta
    add_cash("OTROS", 300, t)           # alias histórico de OTRO: resta
    clock["now"] = SAT_LATER
    before = live(pid)["result"]
    assert before["extra_expenses"] == 4000 and before["operating_result"] == -104000
    assert live(pid)["accumulated"]["unclassified_expenses"] == 0
    add_cash("NUEVA_CATEGORIA", 50000, t)
    data = live(pid)
    assert data["result"] == before
    assert data["accumulated"]["unclassified_expenses"] == 50000
    assert "CATEGORIA_GASTO_NO_RECONOCIDA" in {w["code"] for w in data["warnings"]}
    final = close_plan(pid).json()
    assert final["result"] == before
    assert final["data_quality"]["unclassified_cash_movements"][0]["category"] == "NUEVA_CATEGORIA"


def test_ar_money_format():
    assert fair_planning.ar_money(Decimal("1334.5")) == "$1.334,50"
    assert fair_planning.ar_money(0) == "$0,00"
    assert fair_planning.ar_money(-1234567.891) == "-$1.234.567,89"


# ---------- Base SQLite existente ----------

SEED_OLD = """
import sys, os
sys.path.insert(0, {repo!r})
sys.path.insert(0, os.path.join({repo!r}, "tests"))
import main, authkit
db = main.SessionLocal()
s = main.SaleDB(branch_id=2, total_amount=1500); db.add(s); db.flush()
db.add(main.SaleItemDB(sale_id=s.id, product_id=1, quantity=1, unit_price=1500, cogs=800, gross_profit=700))
db.commit(); db.close()
from fastapi.testclient import TestClient
c = TestClient(main.app)
c.headers.update(authkit.headers_for(main, "admin@fiambreria.com"))
r = c.post("/day-plans", json={{"branch_id": 2, "plan_type": "FIN_DE_SEMANA", "start_date": "17/10/2026"}})
assert r.status_code == 200, r.text
"""

CHECK_LIVE = """
import sys, os
sys.path.insert(0, {repo!r})
sys.path.insert(0, os.path.join({repo!r}, "tests"))
import main, authkit
from fastapi.testclient import TestClient
c = TestClient(main.app)
c.headers.update(authkit.headers_for(main, "admin@fiambreria.com"))
assert c.get("/health").json()["database_ready"] is True
plans = c.get("/day-plans?branch_id=2").json()
assert len(plans) == 1 and plans[0]["status"] == "BORRADOR" and plans[0]["opened_at"] is None
print("OK")
"""

NEW_COLUMNS = ("opened_at", "opened_by", "closed_at", "closed_by", "final_results_json", "final_calc_version")


def test_existing_sqlite_db_gets_new_columns_without_losing_data(tmp_path):
    db_path = tmp_path / "produccion_vieja.db"
    seeded = _run(SEED_OLD, db_path, tmp_path)
    assert seeded.returncode == 0, seeded.stderr
    # Simula la base desplegada antes de este cambio: day_plans sin columnas nuevas ni índice.
    conn = sqlite3.connect(db_path)
    conn.execute("DROP INDEX ux_day_plans_one_open_per_branch")
    for col in NEW_COLUMNS:
        conn.execute(f"ALTER TABLE day_plans DROP COLUMN {col}")
    conn.commit()
    tables = ("day_plans", "day_plan_snapshots", "sales", "sale_items", "users", "branches")
    before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in tables}
    conn.close()

    for _ in range(2):   # idempotente: dos arranques seguidos
        checked = _run(CHECK_LIVE, db_path, tmp_path)
        assert checked.returncode == 0, checked.stderr
        assert "OK" in checked.stdout

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(day_plans)")]
    idx = conn.execute("SELECT sql FROM sqlite_master WHERE name='ux_day_plans_one_open_per_branch'").fetchone()
    after = {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in tables}
    conn.close()
    assert all(c in cols for c in NEW_COLUMNS)
    assert idx is not None and "WHERE status = 'ABIERTA'" in idx[0]
    n_new = len(NEW_COLUMNS)
    assert [row[:-n_new] for row in after["day_plans"]] == before["day_plans"]   # datos previos intactos
    assert all(row[-n_new:] == (None,) * n_new for row in after["day_plans"])
    for t in tables[1:]:
        assert after[t] == before[t]
