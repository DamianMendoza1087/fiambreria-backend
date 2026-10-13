"""Tests unitarios del cálculo de Objetivo de Jornada (sin base de datos)."""
import datetime
import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fair_planning as fp  # noqa: E402
from fair_planning import D  # noqa: E402

THRESHOLDS = {k: fp.DEFAULT_FAIR_CONFIG[k] for k in fp.THRESHOLD_KEYS}
SATURDAY = "2026-10-17"


def weekend_plan(**overrides):
    plan = {
        "plan_type": "FIN_DE_SEMANA", "start_date": SATURDAY,
        "stall_total": 240000, "staff_total": 200000,
        "transport_outbound": 30000, "transport_return": 30000,
        "other_expenses": 0, "debt_target": 0, "profit_target": 0, "capital_available": 0,
        "manual_expected_sales": None,
    }
    plan.update(overrides)
    return plan


def product(name, price, available=True, active=True, base_price=None):
    return {"name": name, "unit_type": "kg", "branch_price": price, "branch_available": available,
            "product_active": active, "base_price": base_price}


def compute(plan=None, lots=None, products=None, daily=None, revenue=None, history_days=0,
            replacement=None, thresholds=None):
    return fp.compute_day_plan(plan or weekend_plan(), thresholds or THRESHOLDS, lots or [],
                               products or {}, daily or [], revenue or {}, history_days, replacement or {})


def one_product_stock(qty, price, cost):
    return [{"lot_id": 1, "product_id": 1, "qty": qty, "cost_price": cost}], {1: product("Jamón", price)}


# ---------- Costo operativo y objetivos ----------

def test_weekend_opex_with_defaults_is_500000():
    cfg = fp.DEFAULT_FAIR_CONFIG
    costs = fp.operating_costs({
        "stall_total": cfg["stall_cost_per_day"] * 2,
        "staff_total": cfg["staff_cost_per_person_day"] * cfg["staff_count"] * 2,
        "transport_outbound": cfg["transport_outbound"], "transport_return": cfg["transport_return"],
    })
    assert costs["opex"] == Decimal("500000")


def test_debt_is_not_counted_twice():
    costs = fp.operating_costs(weekend_plan(debt_target=100000))
    assert costs["opex"] == Decimal("500000")  # la deuda no es gasto
    tg = fp.targets(costs["opex"], D(100000), D(50000))
    assert tg == {"g_eq": Decimal("500000"), "g_debt": Decimal("600000"), "g_obj": Decimal("650000")}
    lots, prods = one_product_stock(1000, 2000, 1000)
    result = compute(weekend_plan(debt_target=100000, profit_target=50000), lots, prods)
    assert result["operating_costs"]["opex"] == 500000
    assert result["targets"]["objective"] == 650000


# ---------- Viabilidad económica ----------

def test_non_positive_margin_is_red():
    lots, prods = one_product_stock(1000, 100, 120)
    result = compute(lots=lots, products=prods)
    assert result["economic"]["verdict"] == "NO_VIABLE"
    assert "MARGEN_NO_POSITIVO" in result["economic"]["reasons"]
    assert result["headline"] == "🔴 NO CONVIENE ABRIR"


def test_stock_insufficient_for_equilibrium_is_red():
    lots, prods = one_product_stock(10, 1000, 500)  # margen total 5.000 << 500.000
    result = compute(lots=lots, products=prods)
    assert result["economic"]["verdict"] == "NO_VIABLE"
    assert "NO_CUBRE_EQUILIBRIO_CON_STOCK_Y_CAPITAL" in result["economic"]["reasons"]


def test_covers_equilibrium_but_not_objective_is_yellow():
    lots, prods = one_product_stock(600, 2000, 1000)  # MB 600.000
    result = compute(weekend_plan(profit_target=200000), lots, prods)
    assert result["economic"]["verdict"] == "VIABLE_AJUSTADO"
    assert "NO_ALCANZA_OBJETIVO" in result["economic"]["reasons"]


def test_green_with_safety_margin():
    lots, prods = one_product_stock(1000, 2000, 1000)  # V 2M, MB 1M, m 0,5
    result = compute(lots=lots, products=prods)
    # 500.000 × 1,10 / 0,5 = 1.100.000 → 55% del stock (<= 60%)
    cons = result["scenarios"]["conservative"]["objective_with_safety"]
    assert cons["sales_needed"] == 1100000
    assert cons["stock_pct"] == 0.55
    assert result["economic"]["verdict"] == "VIABLE"
    assert result["economic"]["label"] == "🟢 ECONÓMICAMENTE VIABLE"


def test_safety_margin_pushes_to_yellow():
    lots, prods = one_product_stock(1000, 2000, 1000)
    # Sin seguridad: 580.000/0,5 = 58%; con 10%: 63,8% > 60%
    result = compute(weekend_plan(profit_target=80000), lots, prods)
    assert result["scenarios"]["conservative"]["objective"]["stock_pct"] == 0.58
    assert result["economic"]["verdict"] == "VIABLE_AJUSTADO"
    assert "OBJETIVO_CON_SEGURIDAD_SUPERA_UMBRAL_VERDE" in result["economic"]["reasons"]
    assert result["economic"]["very_tight"] is False


def test_above_85_percent_is_very_tight():
    lots, prods = one_product_stock(1000, 2000, 1000)
    # G_obj × 1,1 = 900.000 → 90% del stock
    profit = Decimal("900000") / Decimal("1.1") - Decimal("500000")
    result = compute(weekend_plan(profit_target=str(profit)), lots, prods)
    assert result["economic"]["verdict"] == "VIABLE_AJUSTADO"
    assert result["economic"]["very_tight"] is True
    assert "MUY AJUSTADO" in result["economic"]["label"]


# ---------- Calidad de datos ----------

def test_lot_with_zero_cost_is_excluded():
    lots = [{"lot_id": 1, "product_id": 1, "qty": 100, "cost_price": 1000},
            {"lot_id": 2, "product_id": 1, "qty": 50, "cost_price": 0},
            {"lot_id": 3, "product_id": 1, "qty": 20, "cost_price": None}]
    result = compute(lots=lots, products={1: product("Queso", 2000)})
    p = result["stock_summary"]["products"][0]
    assert p["qty"] == 100 and p["cost"] == 100000 and p["sale_value"] == 200000
    excluded = result["data_quality"]["excluded"]
    assert {e["lot_id"] for e in excluded} == {2, 3}
    assert all(e["reason"] == "COSTO_INCOMPLETO" for e in excluded)
    # 70 kg × 2000 excluidos sobre 270 kg valorizados
    assert result["data_quality"]["incomplete_pct"] == round(140000 / 340000, 4)


def test_missing_fair_price_is_excluded_not_replaced_by_global():
    lots = [{"lot_id": 1, "product_id": 1, "qty": 100, "cost_price": 1000}]
    result = compute(lots=lots, products={1: product("Salame", None, base_price=3000)})
    assert result["stock_summary"]["products"] == []
    assert result["stock_summary"]["sale_value"] == 0
    assert result["data_quality"]["excluded"][0]["reason"] == "PRECIO_FERIA_INCOMPLETO"
    assert result["economic"]["verdict"] == "NO_VIABLE"


def test_invalid_and_unavailable_prices_are_excluded():
    lots = [{"lot_id": 1, "product_id": 1, "qty": 10, "cost_price": 100},
            {"lot_id": 2, "product_id": 2, "qty": 10, "cost_price": 100}]
    prods = {1: product("A", 0), 2: product("B", 500, available=False)}
    reasons = {e["product_id"]: e["reason"] for e in compute(lots=lots, products=prods)["data_quality"]["excluded"]}
    assert reasons == {1: "PRECIO_FERIA_INVALIDO", 2: "PRODUCTO_NO_HABILITADO_EN_FERIA"}


def test_price_below_cost_is_included_with_warning():
    lots = [{"lot_id": 1, "product_id": 1, "qty": 1000, "cost_price": 1000},
            {"lot_id": 2, "product_id": 2, "qty": 10, "cost_price": 600}]
    prods = {1: product("Bueno", 2000), 2: product("Pérdida", 500)}
    result = compute(lots=lots, products=prods)
    names = [p["product_name"] for p in result["stock_summary"]["products"]]
    assert "Pérdida" in names
    loss = next(p for p in result["stock_summary"]["products"] if p["product_name"] == "Pérdida")
    assert loss["gross_margin"] == -1000 and loss["below_cost"] is True
    assert any(w["code"] == "VENTA_BAJO_COSTO" and w["product_id"] == 2 for w in result["warnings"])


def test_incomplete_data_tolerance():
    base_lots = [{"lot_id": 1, "product_id": 1, "qty": 1000, "cost_price": 1000}]
    prods = {1: product("Jamón", 2000), 2: product("Sin costo", 2000)}
    # 5% incompleto: puede seguir verde
    small = base_lots + [{"lot_id": 2, "product_id": 2, "qty": Decimal("1000") * Decimal("0.05") / Decimal("0.95"),
                          "cost_price": 0}]
    r_small = compute(lots=small, products=prods)
    assert r_small["data_quality"]["incomplete_pct"] == 0.05
    assert r_small["economic"]["verdict"] == "VIABLE"
    # 20% incompleto: nunca verde aunque el resto alcance
    big = base_lots + [{"lot_id": 2, "product_id": 2, "qty": 250, "cost_price": 0}]
    r_big = compute(lots=big, products=prods)
    assert r_big["data_quality"]["incomplete_pct"] == 0.2
    assert r_big["economic"]["verdict"] == "VIABLE_AJUSTADO"
    assert "DATOS_INCOMPLETOS_SUPERAN_TOLERANCIA" in r_big["economic"]["reasons"]


def test_zero_cost_never_creates_artificial_margin():
    lots = [{"lot_id": 1, "product_id": 1, "qty": 100000, "cost_price": 0}]
    result = compute(lots=lots, products={1: product("Regalo", 5000)})
    assert result["stock_summary"]["gross_margin"] == 0
    assert result["economic"]["verdict"] == "NO_VIABLE"


# ---------- Escenarios ----------

def two_products():
    lots = [{"lot_id": 1, "product_id": 1, "qty": 1000, "cost_price": 900},   # margen 10%
            {"lot_id": 2, "product_id": 2, "qty": 1000, "cost_price": 500}]   # margen 50%
    return lots, {1: product("Bajo margen", 1000), 2: product("Alto margen", 1000)}


def test_proportional_scenario():
    lots, prods = two_products()  # V 2M, MB 600k, m 0,3
    result = compute(weekend_plan(transport_outbound=0, transport_return=0, stall_total=100000,
                                  staff_total=200000), lots, prods)  # G = 300.000
    prop = result["scenarios"]["proportional"]["equilibrium"]
    assert prop["sales_needed"] == 1000000
    assert prop["stock_pct"] == 0.5


def test_conservative_scenario_sells_lowest_margin_first():
    lots, prods = two_products()
    result = compute(weekend_plan(transport_outbound=0, transport_return=0, stall_total=100000,
                                  staff_total=200000), lots, prods)
    cons = result["scenarios"]["conservative"]["equilibrium"]
    # Todo el producto de 10% (1M, margen 100k) + 40% del de 50% (400k, margen 200k)
    assert cons["sales_needed"] == 1400000
    assert cons["stock_pct"] == 0.7
    assert [o["product_id"] for o in cons["order"]] == [1, 2]
    assert cons["order"][1]["fraction_sold"] == 0.4


def test_conservative_not_reachable_when_stock_short():
    lots, prods = two_products()
    result = compute(weekend_plan(profit_target=1000000), lots, prods)
    cons = result["scenarios"]["conservative"]["objective"]
    assert cons["reachable_with_stock"] is False and cons["sales_needed"] is None


def test_historical_without_data():
    lots, prods = one_product_stock(1000, 2000, 1000)
    result = compute(weekend_plan(plan_type="SABADO", stall_total=120000, staff_total=100000,
                                  transport_outbound=30000, transport_return=30000), lots, prods)
    assert result["scenarios"]["historical"] == {"available": False, "reason": "SIN_HISTORIAL_SUFICIENTE",
                                                 "history_days": 0}
    assert result["commercial"]["verdict"] == "SIN_HISTORIAL_SUFICIENTE"
    assert result["commercial"]["comparables_count"] == 0


def saturday_days(values, gross_ratio=Decimal("0.4")):
    start = datetime.date(2026, 10, 10)
    return [{"date": start - datetime.timedelta(days=7 * i), "revenue": D(v), "gross_profit": D(v) * gross_ratio}
            for i, v in enumerate(values)]


def test_historical_with_less_than_three_comparables():
    plan = weekend_plan(plan_type="SABADO", stall_total=120000, staff_total=100000)
    result = compute(plan, daily=saturday_days([800000, 900000]))
    assert result["commercial"]["verdict"] == "SIN_HISTORIAL_SUFICIENTE"
    assert result["commercial"]["comparables_count"] == 2


def test_median_p25_p75():
    assert fp.percentile([D(100), D(200), D(300), D(400)], Decimal("0.5")) == Decimal("250")
    assert fp.percentile([D(100), D(200), D(300), D(400)], Decimal("0.25")) == Decimal("175")
    assert fp.percentile([D(100), D(200), D(300), D(400)], Decimal("0.75")) == Decimal("325")
    plan = weekend_plan(plan_type="SABADO", stall_total=120000, staff_total=100000)  # OPEX 280.000
    result = compute(plan, daily=saturday_days([400000, 300000, 200000, 100000]))
    c = result["commercial"]
    assert (c["expected_sales"], c["p25"], c["p75"]) == (250000, 175000, 325000)
    assert c["historical_margin"] == 0.4
    assert c["sales_needed_equilibrium"] == 700000
    assert c["verdict"] == "RIESGO_ALTO"


def test_commercial_verdicts_and_economic_independence():
    lots, prods = one_product_stock(1000, 2000, 1000)
    # OPEX 280.000; objetivo 400.000. Con margen histórico 0,4: equilibrio 700.000, objetivo 1.000.000
    plan = weekend_plan(plan_type="SABADO", stall_total=120000, staff_total=100000, profit_target=120000)
    # Demanda histórica baja: la economía sigue siendo VIABLE.
    low = compute(plan, lots, prods, daily=saturday_days([100000, 120000, 110000]))
    assert low["commercial"]["verdict"] == "RIESGO_ALTO"
    assert low["economic"]["verdict"] == "VIABLE"
    assert low["headline"] == "🟢 ECONÓMICAMENTE VIABLE · 🟠 DEMANDA HISTÓRICA INSUFICIENTE / RIESGO ALTO"
    high = compute(plan, lots, prods, daily=saturday_days([1100000, 1000000, 1200000]))
    assert high["commercial"]["verdict"] == "DEMANDA_PROBABLE"
    mid = compute(plan, lots, prods, daily=saturday_days([700000, 650000, 750000]))
    assert mid["commercial"]["verdict"] == "DEMANDA_INCIERTA"


def test_weekend_comparables_require_both_days():
    sat = datetime.date(2026, 10, 10)
    daily = []
    for i in range(3):
        s = sat - datetime.timedelta(days=7 * i)
        daily.append({"date": s, "revenue": D(500000), "gross_profit": D(200000)})
        daily.append({"date": s + datetime.timedelta(days=1), "revenue": D(400000), "gross_profit": D(160000)})
    daily.append({"date": sat - datetime.timedelta(days=21), "revenue": D(999999), "gross_profit": D(1)})  # sin domingo
    c = compute(weekend_plan(), daily=daily)["commercial"]
    assert c["comparables_count"] == 3
    assert c["expected_sales"] == 900000


def test_holiday_has_no_comparable_and_manual_is_separate():
    plan = weekend_plan(plan_type="FERIADO", stall_total=120000, staff_total=100000,
                        manual_expected_sales=1500000)
    lots, prods = one_product_stock(1000, 2000, 1000)
    c = compute(plan, lots, prods, daily=saturday_days([500000] * 5))["commercial"]
    assert c["verdict"] == "SIN_REFERENCIA_COMPARABLE"
    assert c["manual_scenario"]["expected_sales"] == 1500000
    assert c["manual_scenario"]["margin_basis"] == "STOCK_ACTUAL"
    assert c["manual_scenario"]["verdict"] == "DEMANDA_PROBABLE"


def test_historical_mix_scenario_limited_to_stock():
    lots, prods = two_products()
    revenue = {1: D(900000), 2: D(100000), 99: D(500000)}  # 99 no está en stock
    result = compute(weekend_plan(transport_outbound=0, transport_return=0, stall_total=50000,
                                  staff_total=50000), lots, prods, revenue=revenue, history_days=5)
    hist = result["scenarios"]["historical"]
    assert hist["available"] is True
    # mezcla 90/10: margen 0,9×0,1 + 0,1×0,5 = 0,14
    assert hist["equilibrium"]["mix_margin"] == 0.14
    assert hist["equilibrium"]["sales_needed"] == round(100000 / 0.14, 2)


# ---------- Capital y reposición ----------

def test_capital_insufficient():
    lots, prods = one_product_stock(600, 2000, 1000)  # MB 600.000
    plan = weekend_plan(profit_target=200000, capital_available=50000)  # G_obj 700.000
    result = compute(plan, lots, prods, replacement={1: D(1200)})  # m_rep = 0,4
    cap = result["capital"]
    assert cap["purchase_needed"] is True
    assert cap["margin_gap"] == 100000
    assert cap["replacement_margin"] == 0.4
    assert cap["capital_required"] == 150000  # 100.000 × 0,6 / 0,4
    assert cap["capital_difference"] == -100000
    assert result["economic"]["verdict"] == "VIABLE_AJUSTADO"
    assert "REQUIERE_COMPRAR_STOCK" in result["economic"]["reasons"]


def test_capital_sufficient_still_needs_purchase():
    lots, prods = one_product_stock(600, 2000, 1000)
    plan = weekend_plan(profit_target=200000, capital_available=200000)
    cap = compute(plan, lots, prods, replacement={1: D(1200)})["capital"]
    assert cap["capital_required"] == 150000
    assert cap["capital_difference"] == 50000


def test_capital_can_rescue_equilibrium():
    lots, prods = one_product_stock(400, 2000, 1000)  # MB 400.000 < OPEX 500.000
    no_cap = compute(weekend_plan(), lots, prods, replacement={1: D(1200)})
    assert no_cap["economic"]["verdict"] == "NO_VIABLE"
    with_cap = compute(weekend_plan(capital_available=200000), lots, prods, replacement={1: D(1200)})
    # 200.000 de capital a margen de reposición 0,4 → 133.333 de margen adicional
    assert with_cap["economic"]["verdict"] == "VIABLE_AJUSTADO"
    assert with_cap["capital"]["capital_required_for_equilibrium"] == 150000


def test_non_profitable_replacement():
    lots, prods = one_product_stock(600, 2000, 1000)
    cap = compute(weekend_plan(profit_target=200000), lots, prods, replacement={1: D(2500)})["capital"]
    assert cap["capital_required"] is None
    assert cap["reason"] == "REPOSICION_NO_RENTABLE"


def test_replenishment_not_counted_twice():
    lots, prods = one_product_stock(1000, 2000, 1000)
    cheap = compute(weekend_plan(), lots, prods, replacement={1: D(900)})
    expensive = compute(weekend_plan(), lots, prods, replacement={1: D(1800)})
    # La reposición cambia solo el bloque informativo, nunca la venta necesaria ni el veredicto.
    assert cheap["scenarios"] == expensive["scenarios"]
    assert cheap["economic"] == expensive["economic"]
    assert cheap["targets"] == expensive["targets"]
    rep = expensive["capital"]["replenishment"]
    assert rep["informative"] is True
    # Venta proporcional 1.000.000 = 50% del stock: COGS recuperado 500.000; reponer 500 kg × 1800 = 900.000
    assert rep["cogs_recovered_by_sales"] == 500000
    assert rep["replacement_cost_of_sold"] == 900000
    assert rep["replacement_increase_vs_cogs"] == 400000


def test_result_blocks_present():
    lots, prods = one_product_stock(1000, 2000, 1000)
    result = compute(lots=lots, products=prods)
    for block in ("economic", "commercial", "scenarios", "data_quality", "stock_summary", "capital",
                  "operating_costs", "targets", "warnings"):
        assert block in result
    assert result["calc_version"] == 2


@pytest.mark.parametrize("value", [0.1, 0.2, 1234.56])
def test_money_rounding_is_decimal_exact(value):
    assert fp.money(D(value) * 3) == float(Decimal(str(value)) * 3)


# ---------- Margen de reposición fuera de rango ----------

def _rep(margin):
    return {"available": True, "margin": D(margin), "basis": "STOCK_ACTUAL",
            "products_without_replacement_cost": []}


@pytest.mark.parametrize("margin,reason", [
    ("1", "MARGEN_REPOSICION_INVALIDO"),
    ("1.5", "MARGEN_REPOSICION_INVALIDO"),
    ("0", "REPOSICION_NO_RENTABLE"),
    ("-0.2", "REPOSICION_NO_RENTABLE"),
])
def test_capital_with_replacement_margin_out_of_range(margin, reason):
    # Falta margen (G_obj 700.000 > MB 600.000): no debe romper ni calcular capital.
    cap = fp.capital_analysis(D(700000), D(500000), D(600000), D(1000000), _rep(margin))
    assert cap["purchase_needed"] is True
    assert cap["capital_required"] is None and cap["capital_difference"] is None
    assert cap["reason"] == reason
    assert cap["margin_reachable_with_capital"] == 0
    assert "capital_required_for_equilibrium" not in cap


@pytest.mark.parametrize("margin", ["1", "1.5"])
def test_invalid_replacement_margin_without_purchase_is_flagged(margin):
    cap = fp.capital_analysis(D(500000), D(500000), D(600000), D(1000000), _rep(margin))
    assert cap["purchase_needed"] is False
    assert cap["replacement_margin_valid"] is False
    assert cap["margin_reachable_with_capital"] == 0


def test_invalid_replacement_margin_cannot_rescue_equilibrium():
    # MB 400.000 < OPEX 500.000: un margen de reposición inválido no puede "salvar" la jornada.
    stock = {"margin": D("0.5"), "total_gross_margin": D(400000), "incomplete_pct": D(0), "unvalued_excluded": 0}
    tg = fp.targets(D(500000), D(0), D(0))
    cap = fp.capital_analysis(tg["g_obj"], tg["g_eq"], D(400000), D(10 ** 9), _rep("1"))
    cons = {"reachable_with_stock": False, "stock_pct": None}
    eco = fp.economic_verdict(stock, tg, THRESHOLDS, cons, cap)
    assert eco["verdict"] == "NO_VIABLE"


def test_valid_replacement_margin_boundaries():
    cap = fp.capital_analysis(D(700000), D(500000), D(600000), D(0), _rep("0.9999"))
    assert cap["capital_required"] is not None and cap["capital_required"] > 0


def test_to_cents_rounds_half_up():
    assert fp.to_cents("1234.565") == Decimal("1234.57")
    assert fp.to_cents(0.1) == Decimal("0.10")
    assert fp.to_cents(Decimal("0.1") + Decimal("0.2")) == Decimal("0.30")
