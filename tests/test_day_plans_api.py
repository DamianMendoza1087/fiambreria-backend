"""Tests de endpoints y persistencia del Objetivo de Jornada.

Usan una base SQLite temporal (nunca /var/data ni la base real). DATABASE_URL se fija
antes de importar main.py, que inicializa la base al importarse.
"""
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from decimal import Decimal

import pytest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)

_TMP_DIR = tempfile.mkdtemp(prefix="fiambreria-test-")
_DB_PATH = os.path.join(_TMP_DIR, "test.db")
assert not _DB_PATH.startswith("/var/data")
os.environ.pop("APP_ENV", None)
os.environ.pop("ALLOW_DB_INITIALIZATION", None)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

assert main.SQLALCHEMY_DATABASE_URL == f"sqlite:///{_DB_PATH}"
import authkit  # noqa: E402

# Fase 2.2: toda la API exige sesión. El cliente compartido opera como un MASTERADMIN de tests
# (todas las sucursales y permisos); `anon` no envía credenciales.
client = TestClient(main.app)
client.headers.update(authkit.headers_for(main, authkit.ADMIN_EMAIL, role="masteradmin"))
anon = TestClient(main.app)

SATURDAY = "17/10/2026"
CLEAN_TABLES = ("day_plan_snapshots", "day_plans", "fair_cost_configs", "sale_items", "sales",
                "product_ingresses", "stock_movements", "product_lots", "branch_products",
                "promotion_branches", "promotion_items", "promotions", "products")


@pytest.fixture(autouse=True)
def clean_db():
    # Solo sobre la base temporal de tests.
    assert main.SQLALCHEMY_DATABASE_URL.endswith(_DB_PATH)
    conn = sqlite3.connect(_DB_PATH)
    for table in CLEAN_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    yield


def add_product(name, branch_price=2000, base_price=2000, branch_id=2, available=True):
    db = main.SessionLocal()
    p = main.ProductDB(name=name, price_per_unit=base_price, unit_type="kg", cost_price=999999, is_active=True)
    db.add(p)
    db.flush()
    db.add(main.BranchProductDB(branch_id=branch_id, product_id=p.id, price_per_unit=branch_price,
                                is_available=available))
    db.commit()
    pid = p.id
    db.close()
    return pid


def add_lot(product_id, qty, cost, branch_id=2):
    db = main.SessionLocal()
    db.add(main.ProductLotDB(branch_id=branch_id, product_id=product_id, cost_price=cost,
                             initial_qty=qty, current_qty=qty))
    db.commit()
    db.close()


def add_ingress(product_id, cost, branch_id, received_at):
    db = main.SessionLocal()
    db.add(main.ProductIngressDB(branch_id=branch_id, product_id=product_id, cost_price=cost, quantity=10,
                                 received_at=received_at))
    db.commit()
    db.close()


def add_sale(product_id, amount, gross_profit, created_at_utc, branch_id=2):
    db = main.SessionLocal()
    sale = main.SaleDB(branch_id=branch_id, total_amount=amount, created_at=created_at_utc)
    db.add(sale)
    db.flush()
    db.add(main.SaleItemDB(sale_id=sale.id, product_id=product_id, quantity=1, unit_price=amount,
                           cogs=amount - gross_profit, gross_profit=gross_profit))
    db.commit()
    db.close()


def weekend_payload(**extra):
    payload = {"branch_id": 2, "plan_type": "FIN_DE_SEMANA", "start_date": SATURDAY, "created_by": "test"}
    payload.update(extra)
    return payload


def counts():
    conn = sqlite3.connect(_DB_PATH)
    plans = conn.execute("SELECT COUNT(*) FROM day_plans").fetchone()[0]
    snaps = conn.execute("SELECT COUNT(*) FROM day_plan_snapshots").fetchone()[0]
    conn.close()
    return plans, snaps


# ---------- Configuración ----------

def test_fair_config_defaults_and_weekend_opex():
    r = client.get("/branches/2/fair-config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["source"] == "default" and cfg["version"] is None
    assert cfg["stall_cost_per_day"] == 120000 and cfg["staff_count"] == 2
    assert cfg["weekend_operating_cost"] == 500000


def test_weekend_plan_opex_500000():
    add_lot(add_product("Jamón"), 1000, 1000)
    r = client.post("/day-plans/preview", json=weekend_payload())
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert res["operating_costs"]["opex"] == 500000
    assert res["operating_costs"]["transport"] == 60000


def test_fair_config_validation_and_versioning():
    assert client.put("/branches/2/fair-config", json={"stall_cost_per_day": -1}).status_code == 422
    assert client.put("/branches/2/fair-config", json={"green_max_stock_pct": 0.9}).status_code == 422
    assert client.put("/branches/2/fair-config", json={"safety_margin_pct": 1.5}).status_code == 422
    assert client.put("/branches/2/fair-config", json={"minimum_comparable_days": 20}).status_code == 422
    assert client.put("/branches/1/fair-config", json={}).status_code == 422
    assert client.put("/branches/99/fair-config", json={}).status_code == 422
    r1 = client.put("/branches/2/fair-config", json={"stall_cost_per_day": 130000, "updated_by": "a"})
    assert r1.status_code == 200 and r1.json()["version"] == 1
    r2 = client.put("/branches/2/fair-config", json={"staff_count": 3})
    assert r2.json()["version"] == 2
    assert r2.json()["stall_cost_per_day"] == 130000 and r2.json()["staff_count"] == 3
    conn = sqlite3.connect(_DB_PATH)
    rows = conn.execute("SELECT version, is_current FROM fair_cost_configs ORDER BY version").fetchall()
    conn.close()
    assert rows == [(1, 0), (2, 1)]  # la versión anterior queda guardada


def test_individual_day_requires_transport_and_valid_weekday():
    r = client.post("/day-plans/preview", json={"plan_type": "SABADO", "start_date": SATURDAY})
    assert r.status_code == 422 and "transport" in r.json()["detail"]
    r = client.post("/day-plans/preview", json={"plan_type": "SABADO", "start_date": "18/10/2026",
                                                "transport_outbound": 0, "transport_return": 0})
    assert r.status_code == 422
    r = client.post("/day-plans/preview", json={"plan_type": "SABADO", "start_date": SATURDAY,
                                                "transport_outbound": 15000, "transport_return": 15000})
    assert r.status_code == 200
    assert r.json()["results"]["operating_costs"]["opex"] == 120000 + 100000 + 30000


# ---------- Persistencia de planes ----------

def test_preview_does_not_persist():
    add_lot(add_product("Jamón"), 1000, 1000)
    before = counts()
    r = client.post("/day-plans/preview", json=weekend_payload())
    assert r.status_code == 200 and r.json()["persisted"] is False
    assert counts() == before


def test_create_persists_plan_and_snapshot():
    add_lot(add_product("Jamón"), 1000, 1000)
    r = client.post("/day-plans", json=weekend_payload(debt_target=50000, notes="primera"))
    assert r.status_code == 200, r.text
    body = r.json()
    plan = body["plan"]
    assert plan["status"] == "BORRADOR" and plan["start_date"] == "2026-10-17"
    assert plan["start_date_display"] == "17/10/2026" and plan["end_date"] == "2026-10-18"
    assert plan["stall_total"] == 240000 and plan["staff_total"] == 200000
    assert plan["transport_total"] == 60000 and plan["green_max_stock_pct"] == 0.6
    assert counts() == (1, 1)
    snap = body["latest_snapshot"]
    assert snap["calc_version"] == 2
    assert snap["economic_verdict"] == snap["results"]["economic"]["verdict"]
    assert snap["inputs"]["plan"]["debt_target"] == 50000
    assert client.get("/day-plans?branch_id=2").json()[0]["latest_snapshot"]["headline"]


def test_recalculate_adds_snapshot_without_deleting():
    pid = add_product("Jamón")
    add_lot(pid, 1000, 1000)
    plan_id = client.post("/day-plans", json=weekend_payload()).json()["plan"]["id"]
    first = client.get(f"/day-plans/{plan_id}").json()["latest_snapshot"]
    add_lot(pid, 500, 1000)  # cambia el stock real
    r = client.post(f"/day-plans/{plan_id}/recalculate", json={"actor": "test"})
    assert r.status_code == 200
    detail = r.json()
    assert len(detail["snapshots"]) == 2
    assert detail["latest_snapshot"]["results"]["stock_summary"]["sale_value"] == 3000000
    conn = sqlite3.connect(_DB_PATH)
    old = conn.execute("SELECT results_json FROM day_plan_snapshots WHERE id=?", (first["id"],)).fetchone()[0]
    conn.close()
    assert json.loads(old)["stock_summary"]["sale_value"] == 2000000


def test_put_keeps_previous_snapshots():
    add_lot(add_product("Jamón"), 1000, 1000)
    plan_id = client.post("/day-plans", json=weekend_payload()).json()["plan"]["id"]
    r = client.put(f"/day-plans/{plan_id}", json={"profit_target": 100000, "updated_by": "test"})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["plan"]["profit_target"] == 100000
    assert len(detail["snapshots"]) == 2
    assert detail["latest_snapshot"]["results"]["targets"]["objective"] == 600000


def test_cancel_is_logical():
    add_lot(add_product("Jamón"), 1000, 1000)
    plan_id = client.post("/day-plans", json=weekend_payload()).json()["plan"]["id"]
    assert client.patch(f"/day-plans/{plan_id}/status", json={"status": "ABIERTA"}).status_code == 422
    r = client.patch(f"/day-plans/{plan_id}/status", json={"status": "CANCELADA", "actor": "ana", "reason": "lluvia"})
    assert r.status_code == 200
    assert r.json()["plan"]["status"] == "CANCELADA" and "lluvia" in r.json()["plan"]["notes"]
    assert counts() == (1, 1)  # nada se borró
    assert client.get(f"/day-plans/{plan_id}").status_code == 200
    assert client.put(f"/day-plans/{plan_id}", json={"profit_target": 1}).status_code == 409
    assert client.post(f"/day-plans/{plan_id}/recalculate").status_code == 409
    assert client.patch(f"/day-plans/{plan_id}/status", json={"status": "BORRADOR"}).status_code == 409
    assert [p["status"] for p in client.get("/day-plans?branch_id=2").json()] == ["CANCELADA"]


def test_old_plan_keeps_historical_config():
    add_lot(add_product("Jamón"), 1000, 1000)
    plan_id = client.post("/day-plans", json=weekend_payload()).json()["plan"]["id"]
    client.put("/branches/2/fair-config", json={"stall_cost_per_day": 200000, "green_max_stock_pct": 0.5})
    detail = client.post(f"/day-plans/{plan_id}/recalculate").json()
    assert detail["plan"]["stall_total"] == 240000
    assert detail["plan"]["green_max_stock_pct"] == 0.6
    assert detail["latest_snapshot"]["inputs"]["thresholds"]["green_max_stock_pct"] == 0.6
    # Cambiar fechas recalcula puesto/personal con la configuración ORIGINAL del plan.
    r = client.put(f"/day-plans/{plan_id}", json={"plan_type": "PERSONALIZADA", "start_date": "17/10/2026",
                                                  "end_date": "19/10/2026", "transport_outbound": 30000,
                                                  "transport_return": 30000})
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["days_count"] == 3
    assert r.json()["plan"]["stall_total"] == 360000  # 120.000 × 3, no 200.000 × 3
    # Un plan nuevo sí usa la configuración vigente.
    new = client.post("/day-plans/preview", json=weekend_payload()).json()
    assert new["results"]["operating_costs"]["stall"] == 400000


def test_type_change_does_not_carry_weekend_transport():
    add_lot(add_product("Jamón"), 1000, 1000)
    plan_id = client.post("/day-plans", json=weekend_payload()).json()["plan"]["id"]
    r = client.put(f"/day-plans/{plan_id}", json={"plan_type": "SABADO"})
    assert r.status_code == 422


# ---------- Datos de la sucursal ----------

def test_other_branch_stock_is_not_mixed():
    pid = add_product("Jamón")
    add_lot(pid, 1000, 1000, branch_id=2)
    add_lot(pid, 5000, 1, branch_id=1)
    other = add_product("Solo local", branch_id=1)
    add_lot(other, 5000, 100, branch_id=1)
    res = client.post("/day-plans/preview", json=weekend_payload()).json()["results"]
    assert res["stock_summary"]["sale_value"] == 2000000
    assert [p["product_name"] for p in res["stock_summary"]["products"]] == ["Jamón"]
    assert client.post("/day-plans/preview", json=weekend_payload(branch_id=1)).status_code == 422
    assert client.get("/day-plans?branch_id=1").status_code == 422


def test_missing_fair_price_uses_no_global_price():
    pid = add_product("Sin precio Feria", branch_price=None, base_price=5000)
    add_lot(pid, 100, 1000)
    res = client.post("/day-plans/preview", json=weekend_payload()).json()["results"]
    assert res["stock_summary"]["sale_value"] == 0
    assert res["data_quality"]["excluded"][0]["reason"] == "PRECIO_FERIA_INCOMPLETO"


def test_replacement_cost_only_from_fair_branch():
    pid = add_product("Jamón")
    add_lot(pid, 600, 1000)
    add_ingress(pid, 1200, branch_id=2, received_at=datetime.datetime(2026, 9, 1))
    add_ingress(pid, 100, branch_id=1, received_at=datetime.datetime(2026, 10, 1))  # más nuevo, otra sucursal
    res = client.post("/day-plans/preview", json=weekend_payload(profit_target=200000)).json()
    assert res["inputs"]["data_sources"]["replacement_costs"] == {str(pid): 1200.0}
    assert res["results"]["capital"]["replacement_margin"] == 0.4
    assert res["results"]["capital"]["capital_required"] == 150000


def test_history_grouped_by_argentine_day():
    pid = add_product("Jamón")
    add_lot(pid, 1000, 1000)
    # Sábado 22:30 AR = domingo 01:30 UTC: debe contar como sábado.
    for d in (10, 3):
        add_sale(pid, 400000, 160000, datetime.datetime(2026, 10, d + 1, 1, 30))
    add_sale(pid, 400000, 160000, datetime.datetime(2026, 9, 26, 18, 0))
    add_sale(pid, 999999, 1, datetime.datetime(2026, 10, 17, 18, 0))  # el mismo día del plan: no cuenta
    res = client.post("/day-plans/preview", json={"plan_type": "SABADO", "start_date": SATURDAY,
                                                  "transport_outbound": 0, "transport_return": 0}).json()
    c = res["results"]["commercial"]
    assert c["comparables_count"] == 3
    assert [x["dates"][0] for x in c["comparables"]] == ["2026-10-10", "2026-10-03", "2026-09-26"]
    assert c["historical_margin"] == 0.4


def test_health_still_works():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["database_ready"] is True and r.json()["status"] == "ok"


# ---------- Base existente ----------

SEED_SCRIPT = """
import sys, sqlite3
sys.path.insert(0, {repo!r})
import main
db = main.SessionLocal()
p = main.ProductDB(name="Mortadela", price_per_unit=1500, unit_type="kg")
db.add(p); db.flush()
db.add(main.ProductLotDB(branch_id=2, product_id=p.id, cost_price=800, initial_qty=10, current_qty=10))
s = main.SaleDB(branch_id=1, total_amount=1500); db.add(s); db.flush()
db.add(main.SaleItemDB(sale_id=s.id, product_id=p.id, quantity=1, unit_price=1500, cogs=800, gross_profit=700))
pr = main.PromotionDB(name="Picada", active=True); db.add(pr); db.flush()
db.add(main.PromotionItemDB(promotion_id=pr.id, product_id=p.id, included_qty=0.25))
db.add(main.PromotionBranchDB(promotion_id=pr.id, branch_id=2, promo_price=9000, enabled=True))
db.commit(); db.close()
"""

CHECK_SCRIPT = """
import sys, os
sys.path.insert(0, {repo!r})
sys.path.insert(0, os.path.join({repo!r}, "tests"))
import main, authkit
from fastapi.testclient import TestClient
c = TestClient(main.app)
# Sesión de la cuenta maestra ya existente: no agrega filas a users.
c.headers.update(authkit.headers_for(main, "admin@fiambreria.com"))
assert c.get("/health").json()["database_ready"] is True
assert c.get("/branches/2/fair-config").status_code == 200
print("OK")
"""


def _run(script, db_path, cwd):
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "ALLOW_DB_INITIALIZATION")}
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    return subprocess.run([sys.executable, "-c", script.format(repo=REPO_DIR)], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=120)


def test_existing_db_keeps_data_when_new_tables_are_created(tmp_path):
    db_path = tmp_path / "existente.db"
    seeded = _run(SEED_SCRIPT, db_path, tmp_path)
    assert seeded.returncode == 0, seeded.stderr
    # Simula la base de producción anterior a este módulo: sin las tablas nuevas.
    conn = sqlite3.connect(db_path)
    for t in ("day_plan_snapshots", "day_plans", "fair_cost_configs"):
        conn.execute(f"DROP TABLE {t}")
    conn.commit()
    tables = ("products", "product_lots", "sales", "sale_items", "promotions", "promotion_items",
              "promotion_branches", "users", "branches")
    before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in tables}
    conn.close()

    checked = _run(CHECK_SCRIPT, db_path, tmp_path)
    assert checked.returncode == 0, checked.stderr
    assert "OK" in checked.stdout

    conn = sqlite3.connect(db_path)
    after = {t: conn.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in tables}
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert after == before  # productos, ventas, promociones, usuarios y sucursales intactos
    assert {"fair_cost_configs", "day_plans", "day_plan_snapshots"} <= names


# ---------- Validación de umbrales y montos ----------

@pytest.mark.parametrize("payload", [
    {"green_max_stock_pct": 0.85},                                # verde == amarillo
    {"green_max_stock_pct": 0.9},                                 # verde > amarillo
    {"green_max_stock_pct": 0.7, "yellow_max_stock_pct": 0.65},
    {"green_max_stock_pct": -0.1},
    {"yellow_max_stock_pct": 1.2},
    {"safety_margin_pct": -0.01},
    {"safety_margin_pct": 1.5},
    {"incomplete_data_tolerance_pct": 2},
    {"minimum_comparable_days": 9},                               # > comparable_days_count (8)
    {"comparable_days_count": 2},                                 # mínimo (3) > comparables
    {"comparable_days_count": 0},
    {"stall_cost_per_day": -1},
    {"staff_cost_per_person_day": -0.01},
    {"transport_outbound": -100},
    {"transport_return": -100},
    {"staff_count": -1},
])
def test_fair_config_rejects_incoherent_values(payload):
    r = client.put("/branches/2/fair-config", json=payload)
    assert r.status_code == 422, (payload, r.text)
    conn = sqlite3.connect(_DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM fair_cost_configs").fetchone()[0] == 0
    conn.close()


def test_fair_config_rejects_nan():
    r = client.put("/branches/2/fair-config", content='{"stall_cost_per_day": NaN}',
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 422


# ---------- Persistencia monetaria ----------

MONEY_COLUMNS = {
    "fair_cost_configs": {"stall_cost_per_day", "staff_cost_per_person_day", "transport_outbound",
                          "transport_return"},
    "day_plans": {"stall_total", "staff_total", "transport_outbound", "transport_return", "transport_total",
                  "other_expenses", "debt_target", "profit_target", "capital_available", "manual_expected_sales"},
}


def test_money_columns_are_numeric_and_percentages_unchanged():
    conn = sqlite3.connect(_DB_PATH)
    for table, money_cols in MONEY_COLUMNS.items():
        types = {r[1]: r[2] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in money_cols:
            assert types[col] == "NUMERIC(18, 2)", (table, col, types[col])
        for col in ("green_max_stock_pct", "yellow_max_stock_pct", "safety_margin_pct",
                    "incomplete_data_tolerance_pct"):
            assert types[col] == "FLOAT", (table, col)
    conn.close()


def test_money_values_round_trip_to_exact_cents():
    add_lot(add_product("Jamón"), 1000, 1000)
    cfg = client.put("/branches/2/fair-config", json={"stall_cost_per_day": 120000.10}).json()
    assert cfg["stall_cost_per_day"] == 120000.10
    body = client.post("/day-plans", json=weekend_payload(other_expenses=1234.565, debt_target=0.1,
                                                          capital_available=99999999.99,
                                                          manual_expected_sales=0.3)).json()
    plan = body["plan"]
    assert plan["other_expenses"] == 1234.57      # redondeo a centavos antes de guardar
    assert plan["stall_total"] == 240000.20       # 120.000,10 × 2 exacto
    assert plan["debt_target"] == 0.1 and plan["capital_available"] == 99999999.99
    db = main.SessionLocal()
    stored = db.query(main.DayPlanDB).filter(main.DayPlanDB.id == plan["id"]).first()
    assert stored.other_expenses == Decimal("1234.57")
    assert stored.stall_total == Decimal("240000.20")
    assert stored.manual_expected_sales == Decimal("0.30")
    assert isinstance(stored.capital_available, Decimal)
    cfg_row = db.query(main.FairCostConfigDB).first()
    assert cfg_row.stall_cost_per_day == Decimal("120000.10")
    db.close()
    res = body["latest_snapshot"]["results"]
    assert res["operating_costs"]["opex"] == 240000.20 + 200000 + 60000 + 1234.57
    assert res["targets"]["debt_target"] == 0.1
    # Recalcular lee los Numeric y sigue dando lo mismo.
    again = client.post(f"/day-plans/{plan['id']}/recalculate").json()["latest_snapshot"]["results"]
    assert again["operating_costs"] == res["operating_costs"]
