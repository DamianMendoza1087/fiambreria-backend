"""Objetivo de Jornada — Feria: "¿Conviene abrir?" (cálculo puro, sin base de datos).

Toda la aritmética monetaria usa Decimal. Los datos de entrada llegan ya leídos de la
base (lotes de la sucursal, precios de Feria, historial de ventas, costos de reposición)
y este módulo solo calcula. main.py se encarga de leer y persistir.

Reglas clave:
- El costo de mercadería (COGS) se descuenta una sola vez, dentro del margen bruto.
- Deuda y ganancia deseada no son gastos: son objetivos que el margen debe cubrir.
- La reposición de lo vendido es informativa; nunca se descuenta del resultado.
- Lotes con costo <= 0 y productos sin precio válido de Feria se excluyen (no se
  completan con valores globales), y se informan como datos incompletos.
"""
import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

CALC_VERSION = 2

PLAN_TYPES = ("SABADO", "DOMINGO", "FIN_DE_SEMANA", "FERIADO", "EXTRAORDINARIA", "PERSONALIZADA")

DEFAULT_FAIR_CONFIG = {
    "stall_cost_per_day": Decimal("120000"),
    "staff_count": 2,
    "staff_cost_per_person_day": Decimal("50000"),
    "transport_outbound": Decimal("30000"),
    "transport_return": Decimal("30000"),
    "green_max_stock_pct": Decimal("0.60"),
    "yellow_max_stock_pct": Decimal("0.85"),
    "safety_margin_pct": Decimal("0.10"),
    "incomplete_data_tolerance_pct": Decimal("0.10"),
    "comparable_days_count": 8,
    "minimum_comparable_days": 3,
}

THRESHOLD_KEYS = (
    "green_max_stock_pct", "yellow_max_stock_pct", "safety_margin_pct",
    "incomplete_data_tolerance_pct", "comparable_days_count", "minimum_comparable_days",
)

ECONOMIC_LABELS = {
    "VIABLE": "🟢 ECONÓMICAMENTE VIABLE",
    "VIABLE_AJUSTADO": "🟡 VIABLE AJUSTADO",
    "NO_VIABLE": "🔴 NO CONVIENE ABRIR",
}
COMMERCIAL_LABELS = {
    "DEMANDA_PROBABLE": "🟢 DEMANDA PROBABLE",
    "DEMANDA_INCIERTA": "🟡 DEMANDA INCIERTA",
    "RIESGO_ALTO": "🟠 DEMANDA HISTÓRICA INSUFICIENTE / RIESGO ALTO",
    "SIN_HISTORIAL_SUFICIENTE": "⚪ SIN HISTORIAL SUFICIENTE",
    "SIN_REFERENCIA_COMPARABLE": "⚪ SIN REFERENCIA COMPARABLE",
}

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
RATIO_Q = Decimal("0.0001")


def D(value) -> Decimal:
    """Convierte a Decimal sin arrastrar errores binarios de float."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def to_cents(value) -> Decimal:
    """Importe redondeado a centavos (para persistir en columnas Numeric(18, 2))."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def money(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(D(value).quantize(CENT, rounding=ROUND_HALF_UP))


def ratio(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(D(value).quantize(RATIO_Q, rounding=ROUND_HALF_UP))


def qty(value) -> float:
    return float(D(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Costo operativo y objetivos
# ---------------------------------------------------------------------------

def operating_costs(plan: dict) -> dict:
    stall = D(plan["stall_total"])
    staff = D(plan["staff_total"])
    t_out = D(plan["transport_outbound"])
    t_ret = D(plan["transport_return"])
    transport = t_out + t_ret
    other = D(plan.get("other_expenses"))
    opex = stall + staff + transport + other
    return {"stall": stall, "staff": staff, "transport_outbound": t_out, "transport_return": t_ret,
            "transport": transport, "other": other, "opex": opex}


def targets(opex: Decimal, debt: Decimal, profit: Decimal) -> dict:
    # Deuda y ganancia se suman una sola vez sobre el costo operativo.
    return {"g_eq": opex, "g_debt": opex + debt, "g_obj": opex + debt + profit}


# ---------------------------------------------------------------------------
# Calidad de datos y stock
# ---------------------------------------------------------------------------

def analyze_stock(lots: List[dict], products: Dict[int, dict]) -> dict:
    """Clasifica lotes/productos y calcula el stock válido por producto.

    lots: [{lot_id, product_id, qty, cost_price}] solo de la sucursal analizada, qty > 0.
    products: {product_id: {name, unit_type, branch_price (None si no hay precio de Feria),
               branch_available (bool), product_active (bool), base_price}}
    """
    valid: Dict[int, dict] = {}
    excluded: List[dict] = []
    warnings: List[dict] = []
    excluded_value = ZERO
    unvalued_excluded = 0

    for lot in lots:
        pid = lot["product_id"]
        info = products.get(pid, {})
        name = info.get("name") or f"Producto #{pid}"
        q = D(lot["qty"])
        cost = D(lot.get("cost_price")) if lot.get("cost_price") is not None else None
        price = info.get("branch_price")
        price = D(price) if price is not None else None

        reason = None
        if not info.get("product_active", True) or not info.get("branch_available", False):
            reason = "PRODUCTO_NO_HABILITADO_EN_FERIA"
        elif price is None:
            reason = "PRECIO_FERIA_INCOMPLETO"
        elif price <= 0:
            reason = "PRECIO_FERIA_INVALIDO"
        elif cost is None or cost <= 0:
            reason = "COSTO_INCOMPLETO"

        if reason:
            # Valor estimado solo para medir cuánto stock queda afuera; nunca entra al cálculo.
            estimate_price = price if price is not None and price > 0 else D(info.get("base_price"))
            est_value = q * estimate_price if estimate_price > 0 else None
            if est_value is None:
                unvalued_excluded += 1
            else:
                excluded_value += est_value
            excluded.append({
                "product_id": pid, "product_name": name, "lot_id": lot.get("lot_id"),
                "reason": reason, "qty": qty(q), "unit_type": info.get("unit_type"),
                "estimated_sale_value": money(est_value),
            })
            continue

        p = valid.setdefault(pid, {
            "product_id": pid, "product_name": name, "unit_type": info.get("unit_type"),
            "price": price, "qty": ZERO, "cost": ZERO, "lots": 0, "below_cost_lots": 0,
        })
        p["qty"] += q
        p["cost"] += q * cost
        p["lots"] += 1
        if price < cost:
            p["below_cost_lots"] += 1

    rows = []
    for p in valid.values():
        value = p["qty"] * p["price"]
        gm = value - p["cost"]
        p["value"] = value
        p["gross_margin"] = gm
        p["margin_pct"] = gm / value
        rows.append(p)
        if p["below_cost_lots"]:
            warnings.append({"code": "VENTA_BAJO_COSTO", "product_id": p["product_id"],
                             "product_name": p["product_name"],
                             "detail": f"{p['below_cost_lots']} lote(s) con costo mayor al precio de Feria"})

    total_value = sum((p["value"] for p in rows), ZERO)
    total_cost = sum((p["cost"] for p in rows), ZERO)
    total_gm = total_value - total_cost
    margin = (total_gm / total_value) if total_value > 0 else None

    denominator = total_value + excluded_value
    incomplete_pct = (excluded_value / denominator) if denominator > 0 else (ONE if excluded else ZERO)
    if excluded:
        codes = sorted({e["reason"] for e in excluded})
        warnings.append({"code": "DATOS_INCOMPLETOS",
                         "detail": f"{len(excluded)} lote(s) excluidos del cálculo: {', '.join(codes)}"})
    if unvalued_excluded:
        warnings.append({"code": "DATOS_INCOMPLETOS_SIN_VALOR",
                         "detail": f"{unvalued_excluded} lote(s) excluidos sin ningún precio para estimar su valor; "
                                   "el porcentaje de datos incompletos es un mínimo"})

    rows.sort(key=lambda r: (r["margin_pct"], r["product_name"]))
    return {
        "products": rows, "excluded": excluded, "warnings": warnings,
        "total_value": total_value, "total_cost": total_cost, "total_gross_margin": total_gm,
        "margin": margin, "excluded_value": excluded_value,
        "incomplete_pct": incomplete_pct, "unvalued_excluded": unvalued_excluded,
    }


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------

def proportional_scenario(g: Decimal, total_value: Decimal, margin: Optional[Decimal]) -> dict:
    if margin is None or margin <= 0 or total_value <= 0:
        return {"available": False, "reason": "MARGEN_NO_POSITIVO"}
    sales = g / margin
    return {"available": True, "sales_needed": sales, "stock_pct": sales / total_value,
            "reachable_with_stock": sales <= total_value}


def conservative_scenario(g: Decimal, products: List[dict], total_value: Decimal) -> dict:
    """Peor mezcla: se venden primero los productos de menor margen % (incluidos negativos)."""
    if total_value <= 0:
        return {"available": False, "reason": "SIN_STOCK_VALIDO"}
    if g <= 0:
        return {"available": True, "sales_needed": ZERO, "stock_pct": ZERO,
                "reachable_with_stock": True, "order": []}
    sales = ZERO
    cum_margin = ZERO
    order = []
    for p in sorted(products, key=lambda r: (r["margin_pct"], r["product_name"])):
        gm = p["gross_margin"]
        if gm > 0 and cum_margin + gm >= g:
            fraction = (g - cum_margin) / gm
            sales += fraction * p["value"]
            order.append({"product_id": p["product_id"], "fraction_sold": ratio(fraction)})
            return {"available": True, "sales_needed": sales, "stock_pct": sales / total_value,
                    "reachable_with_stock": True, "order": order}
        cum_margin += gm
        sales += p["value"]
        order.append({"product_id": p["product_id"], "fraction_sold": 1.0})
    return {"available": True, "sales_needed": None, "stock_pct": None,
            "reachable_with_stock": False, "max_margin_with_stock": cum_margin, "order": order}


def mix_scenario(g: Decimal, products: List[dict], weights: Dict[int, Decimal]) -> dict:
    """Venta según la mezcla histórica, limitada al stock disponible de cada producto."""
    remaining = {p["product_id"]: p["value"] for p in products if weights.get(p["product_id"], ZERO) > 0}
    margin_pct = {p["product_id"]: p["margin_pct"] for p in products}
    if not remaining:
        return {"available": False, "reason": "SIN_PRODUCTOS_HISTORICOS_EN_STOCK"}
    total_w = sum((weights[pid] for pid in remaining), ZERO)
    initial_margin = sum((weights[pid] * margin_pct[pid] for pid in remaining), ZERO) / total_w
    if g <= 0:
        return {"available": True, "mix_margin": initial_margin, "sales_needed": ZERO,
                "reachable_with_stock": True}
    sales = ZERO
    needed = g
    while remaining:
        w_total = sum((weights[pid] for pid in remaining), ZERO)
        m_active = sum((weights[pid] * margin_pct[pid] for pid in remaining), ZERO) / w_total
        # pesos vendidos hasta agotar el primer producto de la mezcla activa
        exhaust = min(remaining[pid] * w_total / weights[pid] for pid in remaining)
        if m_active > 0 and needed / m_active <= exhaust:
            sales += needed / m_active
            return {"available": True, "mix_margin": initial_margin, "sales_needed": sales,
                    "reachable_with_stock": True}
        sales += exhaust
        needed -= m_active * exhaust
        for pid in list(remaining):
            remaining[pid] -= exhaust * weights[pid] / w_total
            if remaining[pid] <= Decimal("0.000001"):
                del remaining[pid]
    return {"available": True, "mix_margin": initial_margin, "sales_needed": None,
            "reachable_with_stock": False}


def historical_mix_weights(product_revenue: Dict[int, Decimal], valid_product_ids) -> Dict[int, Decimal]:
    valid_ids = set(valid_product_ids)
    filtered = {pid: D(v) for pid, v in product_revenue.items() if pid in valid_ids and D(v) > 0}
    total = sum(filtered.values(), ZERO)
    if total <= 0:
        return {}
    return {pid: v / total for pid, v in filtered.items()}


# ---------------------------------------------------------------------------
# Capital
# ---------------------------------------------------------------------------

def replacement_margin(products: List[dict], replacement_costs: Dict[int, Decimal],
                       weights: Optional[Dict[int, Decimal]]) -> dict:
    """Margen de reposición ponderado con el último costo de ingreso de la sucursal."""
    basis = "HISTORICO" if weights else "STOCK_ACTUAL"
    usable = [p for p in products if replacement_costs.get(p["product_id"]) is not None
              and D(replacement_costs[p["product_id"]]) > 0]
    if weights:
        w = {p["product_id"]: weights.get(p["product_id"], ZERO) for p in usable}
    else:
        w = {p["product_id"]: p["value"] for p in usable}
    w = {pid: v for pid, v in w.items() if v > 0}
    missing = [p["product_id"] for p in products if replacement_costs.get(p["product_id"]) is None]
    if not w:
        return {"available": False, "reason": "SIN_COSTO_DE_REPOSICION", "basis": basis,
                "products_without_replacement_cost": missing}
    total_w = sum(w.values(), ZERO)
    by_id = {p["product_id"]: p for p in usable}
    m_rep = sum((w[pid] * (by_id[pid]["price"] - D(replacement_costs[pid])) / by_id[pid]["price"]
                 for pid in w), ZERO) / total_w
    return {"available": True, "margin": m_rep, "basis": basis,
            "products_without_replacement_cost": missing}


def capital_analysis(g_obj: Decimal, g_eq: Decimal, total_gm: Decimal, capital_available: Decimal,
                     rep: dict) -> dict:
    delta = g_obj - total_gm
    result = {"capital_available": capital_available, "margin_gap": max(delta, ZERO),
              "replacement_margin": rep.get("margin"), "replacement_basis": rep.get("basis"),
              "products_without_replacement_cost": rep.get("products_without_replacement_cost", [])}
    m_rep = D(rep["margin"]) if rep.get("available") and rep.get("margin") is not None else None
    # Rango económico válido: 0 < m_rep < 1. Con m_rep >= 1 la reposición costaría 0 o menos
    # (dato imposible) y la fórmula dividiría por cero o daría resultados sin sentido.
    rentable = m_rep is not None and ZERO < m_rep < ONE
    invalid_margin = m_rep is not None and m_rep >= ONE
    # Margen adicional alcanzable comprando stock con el capital disponible.
    result["margin_reachable_with_capital"] = (capital_available * m_rep / (ONE - m_rep)) if rentable else ZERO
    if invalid_margin:
        result["replacement_margin_valid"] = False
    if delta <= 0:
        result.update({"purchase_needed": False, "capital_required": ZERO,
                       "capital_difference": capital_available})
        return result
    result["purchase_needed"] = True
    if not rep.get("available"):
        result.update({"capital_required": None, "capital_difference": None,
                       "reason": rep.get("reason")})
    elif invalid_margin:
        result.update({"capital_required": None, "capital_difference": None,
                       "reason": "MARGEN_REPOSICION_INVALIDO"})
    elif not rentable:
        result.update({"capital_required": None, "capital_difference": None,
                       "reason": "REPOSICION_NO_RENTABLE"})
    else:
        required = delta * (ONE - m_rep) / m_rep
        result.update({"capital_required": required, "capital_difference": capital_available - required})
    gap_eq = g_eq - total_gm
    if gap_eq > 0 and rentable:
        result["capital_required_for_equilibrium"] = gap_eq * (ONE - m_rep) / m_rep
    return result


def replenishment_info(products: List[dict], replacement_costs: Dict[int, Decimal],
                       proportional: dict, total_value: Decimal, total_cost: Decimal) -> dict:
    """Solo informativo: cuánto costaría reponer lo vendido. No se descuenta del resultado."""
    if not proportional.get("available") or total_value <= 0:
        return {"informative": True, "available": False}
    fraction = min(proportional["sales_needed"] / total_value, ONE)
    replacement = ZERO
    covered_cost = ZERO
    for p in products:
        rc = replacement_costs.get(p["product_id"])
        if rc is not None and D(rc) > 0:
            replacement += fraction * p["qty"] * D(rc)
            covered_cost += fraction * p["cost"]
    return {"informative": True, "available": True, "basis": "ESCENARIO_PROPORCIONAL_OBJETIVO",
            "cogs_recovered_by_sales": fraction * total_cost,
            "replacement_cost_of_sold": replacement,
            "replacement_increase_vs_cogs": replacement - covered_cost,
            "note": "La reposición se financia con el costo recuperado por la venta; no es un gasto adicional."}


# ---------------------------------------------------------------------------
# Pronóstico comercial
# ---------------------------------------------------------------------------

def percentile(values: List[Decimal], pct: Decimal) -> Decimal:
    """Percentil con interpolación lineal (mismo criterio que numpy 'linear')."""
    ordered = sorted(D(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * D(pct)
    lower = int(pos)
    frac = pos - lower
    if lower + 1 >= len(ordered):
        return ordered[-1]
    return ordered[lower] + (ordered[lower + 1] - ordered[lower]) * frac


def comparable_periods(plan_type: str, daily: List[dict], before: datetime.date, count: int) -> dict:
    """daily: [{date (date), revenue, gross_profit}] de la sucursal, agrupado por día argentino."""
    if plan_type in ("FERIADO", "EXTRAORDINARIA", "PERSONALIZADA"):
        return {"available": False, "reason": "SIN_REFERENCIA_COMPARABLE", "periods": []}
    by_date = {d["date"]: d for d in daily if d["date"] < before and D(d["revenue"]) > 0}
    periods = []
    if plan_type in ("SABADO", "DOMINGO"):
        weekday = 5 if plan_type == "SABADO" else 6
        for day in sorted(by_date, reverse=True):
            if day.weekday() == weekday:
                d = by_date[day]
                periods.append({"dates": [day], "revenue": D(d["revenue"]), "gross_profit": D(d["gross_profit"])})
    elif plan_type == "FIN_DE_SEMANA":
        for day in sorted(by_date, reverse=True):
            sunday = day + datetime.timedelta(days=1)
            if day.weekday() == 5 and sunday in by_date:
                sat, sun = by_date[day], by_date[sunday]
                periods.append({"dates": [day, sunday],
                                "revenue": D(sat["revenue"]) + D(sun["revenue"]),
                                "gross_profit": D(sat["gross_profit"]) + D(sun["gross_profit"])})
    return {"available": True, "periods": periods[:count]}


def commercial_forecast(plan_type: str, daily: List[dict], before: datetime.date, thresholds: dict,
                        g_eq: Decimal, g_obj: Decimal, manual_expected_sales: Optional[Decimal],
                        stock_margin: Optional[Decimal]) -> dict:
    comp = comparable_periods(plan_type, daily, before, int(thresholds["comparable_days_count"]))
    result = {"plan_type": plan_type}
    m_hist = None
    s_eq_h = s_obj_h = None

    if not comp["available"]:
        result.update({"verdict": "SIN_REFERENCIA_COMPARABLE", "comparables_count": 0,
                       "reason": "No existe clasificación histórica fiable para este tipo de jornada."})
    else:
        periods = comp["periods"]
        result["comparables_count"] = len(periods)
        result["comparables"] = [{"dates": [d.isoformat() for d in p["dates"]], "revenue": money(p["revenue"])}
                                 for p in periods]
        if len(periods) < int(thresholds["minimum_comparable_days"]):
            result.update({"verdict": "SIN_HISTORIAL_SUFICIENTE",
                           "reason": f"Se necesitan al menos {thresholds['minimum_comparable_days']} "
                                     f"comparables; hay {len(periods)}."})
        else:
            revenues = [p["revenue"] for p in periods]
            expected = percentile(revenues, Decimal("0.5"))
            p25 = percentile(revenues, Decimal("0.25"))
            p75 = percentile(revenues, Decimal("0.75"))
            total_rev = sum(revenues, ZERO)
            total_gp = sum((p["gross_profit"] for p in periods), ZERO)
            m_hist = total_gp / total_rev if total_rev > 0 else None
            result.update({"expected_sales": money(expected), "p25": money(p25), "p75": money(p75),
                           "historical_margin": ratio(m_hist)})
            if m_hist is None or m_hist <= 0:
                result.update({"verdict": "RIESGO_ALTO", "reason": "MARGEN_HISTORICO_NO_POSITIVO"})
            else:
                s_eq_h = g_eq / m_hist
                s_obj_h = g_obj / m_hist
                result.update({"sales_needed_equilibrium": money(s_eq_h), "sales_needed_objective": money(s_obj_h)})
                if expected >= s_obj_h:
                    result["verdict"] = "DEMANDA_PROBABLE"
                elif expected >= s_eq_h or p75 >= s_obj_h:
                    result["verdict"] = "DEMANDA_INCIERTA"
                else:
                    result["verdict"] = "RIESGO_ALTO"

    if manual_expected_sales is not None:
        use_hist = m_hist is not None and m_hist > 0
        basis_margin = m_hist if use_hist else stock_margin
        manual = {"expected_sales": money(manual_expected_sales),
                  "margin_basis": "HISTORICO" if use_hist else "STOCK_ACTUAL",
                  "note": "Escenario manual informado por el usuario; no reemplaza al histórico."}
        if basis_margin is not None and basis_margin > 0:
            eq, obj = g_eq / basis_margin, g_obj / basis_margin
            manual["sales_needed_equilibrium"] = money(eq)
            manual["sales_needed_objective"] = money(obj)
            manual["verdict"] = ("DEMANDA_PROBABLE" if manual_expected_sales >= obj
                                 else "DEMANDA_INCIERTA" if manual_expected_sales >= eq else "RIESGO_ALTO")
        else:
            manual["verdict"] = None
            manual["reason"] = "MARGEN_NO_POSITIVO"
        result["manual_scenario"] = manual

    result["label"] = COMMERCIAL_LABELS[result["verdict"]]
    return result


# ---------------------------------------------------------------------------
# Viabilidad económica
# ---------------------------------------------------------------------------

def economic_verdict(stock: dict, tg: dict, thresholds: dict, conservative_safety: dict,
                     capital: dict) -> dict:
    reasons = []
    margin = stock["margin"]
    mb = stock["total_gross_margin"]
    cap_margin = capital["margin_reachable_with_capital"]

    if margin is None or margin <= 0:
        reasons.append("MARGEN_NO_POSITIVO" if margin is not None else "SIN_STOCK_VALIDO")
        return {"verdict": "NO_VIABLE", "very_tight": False, "reasons": reasons,
                "label": ECONOMIC_LABELS["NO_VIABLE"]}
    if mb + cap_margin < tg["g_eq"]:
        reasons.append("NO_CUBRE_EQUILIBRIO_CON_STOCK_Y_CAPITAL")
        return {"verdict": "NO_VIABLE", "very_tight": False, "reasons": reasons,
                "label": ECONOMIC_LABELS["NO_VIABLE"]}

    green = D(thresholds["green_max_stock_pct"])
    yellow = D(thresholds["yellow_max_stock_pct"])
    tolerance = D(thresholds["incomplete_data_tolerance_pct"])

    if mb + cap_margin < tg["g_obj"]:
        reasons.append("NO_ALCANZA_OBJETIVO")
    pct = conservative_safety.get("stock_pct")
    if not conservative_safety.get("reachable_with_stock") or pct is None or pct > green:
        reasons.append("OBJETIVO_CON_SEGURIDAD_SUPERA_UMBRAL_VERDE")
    if capital["purchase_needed"]:
        reasons.append("REQUIERE_COMPRAR_STOCK")
    if stock["incomplete_pct"] > tolerance or stock["unvalued_excluded"]:
        reasons.append("DATOS_INCOMPLETOS_SUPERAN_TOLERANCIA")

    very_tight = (not conservative_safety.get("reachable_with_stock")) or (pct is not None and pct > yellow)
    if reasons:
        label = ECONOMIC_LABELS["VIABLE_AJUSTADO"] + (" — MUY AJUSTADO" if very_tight else "")
        return {"verdict": "VIABLE_AJUSTADO", "very_tight": very_tight, "reasons": reasons, "label": label}
    return {"verdict": "VIABLE", "very_tight": False, "reasons": [], "label": ECONOMIC_LABELS["VIABLE"]}


# ---------------------------------------------------------------------------
# Cálculo completo
# ---------------------------------------------------------------------------

def _scenario_out(s: dict) -> dict:
    out = dict(s)
    for k in ("sales_needed", "max_margin_with_stock"):
        if k in out:
            out[k] = money(out[k])
    if "stock_pct" in out:
        out["stock_pct"] = ratio(out["stock_pct"])
    if "mix_margin" in out:
        out["mix_margin"] = ratio(out["mix_margin"])
    return out


def compute_day_plan(plan: dict, thresholds: dict, lots: List[dict], products: Dict[int, dict],
                     daily_sales: List[dict], product_revenue: Dict[int, Decimal],
                     history_days_count: int, replacement_costs: Dict[int, Decimal]) -> dict:
    """Calcula el resultado completo de un plan. Devuelve un dict serializable a JSON."""
    costs = operating_costs(plan)
    debt = D(plan.get("debt_target"))
    profit = D(plan.get("profit_target"))
    capital_available = D(plan.get("capital_available"))
    tg = targets(costs["opex"], debt, profit)
    safety = D(thresholds["safety_margin_pct"])
    g_obj_safety = tg["g_obj"] * (ONE + safety)

    stock = analyze_stock(lots, products)
    V, m = stock["total_value"], stock["margin"]
    prods = stock["products"]

    proportional = {name: proportional_scenario(g, V, m) for name, g in
                    (("equilibrium", tg["g_eq"]), ("debt", tg["g_debt"]), ("objective", tg["g_obj"]),
                     ("objective_with_safety", g_obj_safety))}
    conservative = {name: conservative_scenario(g, prods, V) for name, g in
                    (("equilibrium", tg["g_eq"]), ("debt", tg["g_debt"]), ("objective", tg["g_obj"]),
                     ("objective_with_safety", g_obj_safety))}

    min_days = int(thresholds["minimum_comparable_days"])
    weights = {}
    if history_days_count < min_days:
        historical = {"available": False, "reason": "SIN_HISTORIAL_SUFICIENTE",
                      "history_days": history_days_count}
    else:
        weights = historical_mix_weights(product_revenue, [p["product_id"] for p in prods])
        if not weights:
            historical = {"available": False, "reason": "SIN_PRODUCTOS_HISTORICOS_EN_STOCK",
                          "history_days": history_days_count}
        else:
            historical = {"available": True, "history_days": history_days_count}
            for name, g in (("equilibrium", tg["g_eq"]), ("objective", tg["g_obj"]),
                            ("objective_with_safety", g_obj_safety)):
                s = mix_scenario(g, prods, weights)
                if s.get("sales_needed") is not None and V > 0:
                    s["stock_pct"] = s["sales_needed"] / V
                historical[name] = _scenario_out(s)

    rep = replacement_margin(prods, replacement_costs, weights or None)
    capital = capital_analysis(tg["g_obj"], tg["g_eq"], stock["total_gross_margin"], capital_available, rep)
    economic = economic_verdict(stock, tg, thresholds, conservative["objective_with_safety"], capital)

    start = plan["start_date"]
    if isinstance(start, str):
        start = datetime.date.fromisoformat(start)
    manual = plan.get("manual_expected_sales")
    commercial = commercial_forecast(plan["plan_type"], daily_sales, start, thresholds, tg["g_eq"], tg["g_obj"],
                                     D(manual) if manual is not None else None, m)

    warnings = list(stock["warnings"])
    if capital.get("products_without_replacement_cost"):
        warnings.append({"code": "SIN_COSTO_DE_REPOSICION",
                         "detail": f"{len(capital['products_without_replacement_cost'])} producto(s) sin ingreso "
                                   "válido en Feria para estimar reposición"})
    if capital.get("replacement_margin_valid") is False:
        warnings.append({"code": "MARGEN_REPOSICION_INVALIDO",
                         "detail": "El margen de reposición calculado está fuera del rango válido (0 a 1)"})
    if capital["purchase_needed"] and capital.get("capital_required") is None:
        warnings.append({"code": capital.get("reason") or "CAPITAL_NO_CALCULABLE",
                         "detail": "No puede calcularse una reposición rentable con los datos actuales"})

    headline = economic["label"] if economic["verdict"] == "NO_VIABLE" \
        else f"{economic['label']} · {commercial['label']}"

    return {
        "calc_version": CALC_VERSION,
        "headline": headline,
        "economic": economic,
        "commercial": commercial,
        "scenarios": {
            "proportional": {k: _scenario_out(v) for k, v in proportional.items()},
            "conservative": {k: _scenario_out(v) for k, v in conservative.items()},
            "historical": historical,
        },
        "data_quality": {
            "incomplete_pct": ratio(stock["incomplete_pct"]),
            "incomplete_pct_is_lower_bound": bool(stock["unvalued_excluded"]),
            "tolerance_pct": ratio(D(thresholds["incomplete_data_tolerance_pct"])),
            "excluded_estimated_value": money(stock["excluded_value"]),
            "excluded": stock["excluded"],
        },
        "stock_summary": {
            "sale_value": money(V), "cost": money(stock["total_cost"]),
            "gross_margin": money(stock["total_gross_margin"]), "margin_pct": ratio(m),
            "products": [{
                "product_id": p["product_id"], "product_name": p["product_name"], "unit_type": p["unit_type"],
                "qty": qty(p["qty"]), "price": money(p["price"]), "sale_value": money(p["value"]),
                "cost": money(p["cost"]), "gross_margin": money(p["gross_margin"]),
                "margin_pct": ratio(p["margin_pct"]), "lots": p["lots"],
                "below_cost": p["below_cost_lots"] > 0,
            } for p in prods],
        },
        "capital": {
            "capital_available": money(capital["capital_available"]),
            "purchase_needed": capital["purchase_needed"],
            "margin_gap": money(capital["margin_gap"]),
            "replacement_margin": ratio(capital["replacement_margin"]),
            "replacement_basis": capital["replacement_basis"],
            "capital_required": money(capital.get("capital_required")),
            "capital_difference": money(capital.get("capital_difference")),
            "capital_required_for_equilibrium": money(capital.get("capital_required_for_equilibrium")),
            "margin_reachable_with_capital": money(capital["margin_reachable_with_capital"]),
            "reason": capital.get("reason"),
            "replenishment": {k: (money(v) if isinstance(v, Decimal) else v) for k, v in
                              replenishment_info(prods, replacement_costs, proportional["objective"],
                                                 V, stock["total_cost"]).items()},
        },
        "operating_costs": {k: money(v) for k, v in costs.items()},
        "targets": {
            "equilibrium": money(tg["g_eq"]), "with_debt": money(tg["g_debt"]),
            "objective": money(tg["g_obj"]), "objective_with_safety": money(g_obj_safety),
            "debt_target": money(debt), "profit_target": money(profit),
        },
        "warnings": warnings,
    }


# ===========================================================================
# Jornada de Feria en vivo (durante y después de la Feria)
# ===========================================================================
#
# Observa las ventas reales ya registradas (no crea ni modifica ventas). main.py lee las
# filas de la ventana de la jornada y las pasa ya con su fecha argentina; acá solo se calcula.
#
# Resultado operativo real =
#     ventas (sales.total_amount)
#   - costo de mercadería vendida (sale_items.cogs, histórico FEFO)
#   = margen bruto
#   - costos operativos planificados (puesto + personal + transporte + otros del plan)
#   - gastos extraordinarios reales (caja: OPERATIVO, SERVICIOS/SERVICIO, COMPRA_MENOR, OTRO, CAJA_CHICA)
#   - mermas reales a costo (stock_movements LOSS: |cantidad| x costo histórico)
#
# Deuda y ganancia deseada son OBJETIVOS: nunca se restan como gasto.

LIVE_CALC_VERSION = 1

# Categorías de caja. Solo estas restan como gasto extraordinario (SERVICIO y CAJA_CHICA son
# alias históricos). Cualquier otra categoría de salida que no figure en estos conjuntos queda
# "no clasificada": se informa con su monto, pero no modifica el resultado hasta tener una regla.
CASH_EXTRA_EXPENSE_CATEGORIES = {"OPERATIVO", "SERVICIOS", "SERVICIO", "COMPRA_MENOR", "OTRO", "CAJA_CHICA"}
CASH_EMPLOYEE_CATEGORIES = {"EMPLEADO", "EMPLEADOS", "PAGO_EMPLEADO", "SUELDO", "SUELDOS", "SALARIO"}
CASH_SUPPLIER_CATEGORIES = {"PROVEEDOR", "PROVEEDORES", "PAGO_PROVEEDOR", "MERCADERIA", "COMPRA_MERCADERIA"}
CASH_OWNER_CATEGORIES = {"RETIRO_DUENO", "RETIRO_DUEÑO", "OWNER_WITHDRAWAL"}

# Diferencia entre sales.total_amount y la suma de sus ítems que se informa como advertencia.
SALE_TOTAL_MISMATCH_TOLERANCE = Decimal("1.00")

LIVE_INDICATOR_LABELS = {
    "FALTA_EQUILIBRIO": "🔴 FALTA PARA EQUILIBRIO",
    "EQUILIBRIO_ALCANZADO": "🟡 EQUILIBRIO ALCANZADO",
    "OBJETIVO_ALCANZADO": "🟢 OBJETIVO ALCANZADO",
}


def ar_money(value) -> str:
    """Importe con formato argentino para textos de advertencia: $1.234,56."""
    q = D(value).quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if q < 0 else ""
    integer, cents = f"{abs(q):.2f}".split(".")
    return f"{sign}${int(integer):,}".replace(",", ".") + f",{cents}"


def cash_category_bucket(category: Optional[str]) -> str:
    c = (category or "").strip().upper()
    if c in CASH_EXTRA_EXPENSE_CATEGORIES:
        return "extra"
    if c in CASH_EMPLOYEE_CATEGORIES:
        return "employee"
    if c in CASH_SUPPLIER_CATEGORIES:
        return "supplier"
    if c in CASH_OWNER_CATEGORIES:
        return "owner"
    # Categoría desconocida: no se asume gasto operativo (podría falsear la rentabilidad).
    return "unclassified"


def _empty_bucket() -> dict:
    return {"sales_count": 0, "revenue": ZERO, "cogs": ZERO, "promo_sales_count": 0, "promo_revenue": ZERO,
            "extra_expenses": ZERO, "unclassified_expenses": ZERO, "unclassified_count": 0, "employee_payments": ZERO,
            "supplier_payments": ZERO, "owner_withdrawals": ZERO, "losses_at_cost": ZERO, "losses_count": 0}


def _add_sale(b: dict, sale: dict):
    total = D(sale["total_amount"])
    b["sales_count"] += 1
    b["revenue"] += total
    b["cogs"] += sum((D(i["cogs"]) for i in sale["items"]), ZERO)
    if any(i.get("is_promotion") for i in sale["items"]):
        b["promo_sales_count"] += 1
        b["promo_revenue"] += total


def _add_cash(b: dict, mov: dict):
    if (mov.get("movement_type") or "").upper() != "OUT":
        return  # los ingresos de caja no son ventas ni gastos de la jornada
    amount = D(mov["amount"])
    bucket = cash_category_bucket(mov.get("category"))
    if bucket == "extra":
        b["extra_expenses"] += amount
    elif bucket == "unclassified":
        # Visible e informado, pero NO se resta del resultado operativo.
        b["unclassified_expenses"] += amount
        b["unclassified_count"] += 1
    elif bucket == "employee":
        b["employee_payments"] += amount
    elif bucket == "supplier":
        b["supplier_payments"] += amount
    else:
        b["owner_withdrawals"] += amount


def _add_loss(b: dict, loss: dict):
    b["losses_count"] += 1
    b["losses_at_cost"] += abs(D(loss["quantity"])) * max(D(loss.get("unit_cost")), ZERO)


def _bucket_out(b: dict) -> dict:
    gross = b["revenue"] - b["cogs"]
    return {
        "sales_count": b["sales_count"],
        "revenue": money(b["revenue"]),
        "cogs": money(b["cogs"]),
        "gross_margin": money(gross),
        "gross_margin_pct": ratio(gross / b["revenue"]) if b["revenue"] > 0 else None,
        "promo_sales_count": b["promo_sales_count"],
        "promo_revenue": money(b["promo_revenue"]),
        "extra_expenses": money(b["extra_expenses"]),
        "unclassified_expenses": money(b["unclassified_expenses"]),
        "unclassified_count": b["unclassified_count"],
        "losses_at_cost": money(b["losses_at_cost"]),
        "losses_count": b["losses_count"],
        "employee_payments_info": money(b["employee_payments"]),
        "supplier_payments_excluded": money(b["supplier_payments"]),
        "owner_withdrawals_excluded": money(b["owner_withdrawals"]),
    }


def _objective(required: Decimal, plan_required: Decimal, gross: Decimal, margin_pct: Optional[Decimal]) -> dict:
    missing = max(required - gross, ZERO)
    surplus = max(gross - required, ZERO)
    reached = gross >= required
    if required > 0:
        progress = gross / required if gross > 0 else ZERO
    else:
        progress = ONE if reached else ZERO
    est_sales = None
    if missing > 0 and margin_pct is not None and margin_pct > 0:
        est_sales = missing / margin_pct
    return {
        "required_margin": money(required),
        "plan_required_margin": money(plan_required),
        "gross_margin": money(gross),
        "missing": money(missing),
        "surplus": money(surplus),
        "reached": reached,
        "progress_pct": ratio(progress),
        "estimated_sales_missing": money(est_sales),
    }


def compute_live_session(plan: dict, sales: List[dict], cash_movements: List[dict], losses: List[dict],
                         first_day: datetime.date, last_day: datetime.date,
                         today: Optional[datetime.date]) -> dict:
    """Resultado de una jornada (en vivo o al cierre).

    plan: montos congelados del Day Plan (stall_total, staff_total, transport_*, other_expenses,
          debt_target, profit_target, capital_available) y start_date/end_date ISO.
    sales: [{sale_id, date (fecha AR), total_amount, items: [{revenue, cogs, is_promotion}]}]
    cash_movements: [{date, movement_type, category, amount}]
    losses: [{date, quantity, unit_cost}]
    first_day/last_day: fechas argentinas de apertura y de corte (ahora o cierre).
    today: fecha argentina actual para el bloque HOY (None al cerrar).
    """
    costs = operating_costs(plan)
    opex = costs["opex"]
    debt = D(plan.get("debt_target"))
    profit = D(plan.get("profit_target"))

    total = _empty_bucket()
    days: Dict[datetime.date, dict] = {}
    d = first_day
    while d <= last_day:
        days[d] = _empty_bucket()
        d += datetime.timedelta(days=1)

    def day_bucket(day):
        return days.setdefault(day, _empty_bucket())

    items_without_cost = 0
    items_without_cost_revenue = ZERO
    mismatches = []
    for sale in sales:
        _add_sale(total, sale)
        _add_sale(day_bucket(sale["date"]), sale)
        items_revenue = ZERO
        for item in sale["items"]:
            items_revenue += D(item["revenue"])
            if D(item["cogs"]) <= 0:
                items_without_cost += 1
                items_without_cost_revenue += D(item["revenue"])
        diff = D(sale["total_amount"]) - items_revenue
        if abs(diff) > SALE_TOTAL_MISMATCH_TOLERANCE:
            mismatches.append({"sale_id": sale["sale_id"], "total_amount": money(sale["total_amount"]),
                               "items_revenue": money(items_revenue), "difference": money(diff)})
    unclassified = []
    for mov in cash_movements:
        _add_cash(total, mov)
        _add_cash(day_bucket(mov["date"]), mov)
        if (mov.get("movement_type") or "").upper() == "OUT" and cash_category_bucket(mov.get("category")) == "unclassified":
            day = mov["date"]
            unclassified.append({"date": day.isoformat() if isinstance(day, datetime.date) else day,
                                 "category": mov.get("category"), "amount": money(D(mov["amount"]))})
    losses_without_cost = 0
    for loss in losses:
        _add_loss(total, loss)
        _add_loss(day_bucket(loss["date"]), loss)
        if D(loss.get("unit_cost")) <= 0:
            losses_without_cost += 1

    revenue, cogs = total["revenue"], total["cogs"]
    gross = revenue - cogs
    extra, loss_cost = total["extra_expenses"], total["losses_at_cost"]
    real_costs = opex + extra + loss_cost
    operating_result = gross - real_costs
    margin_pct = gross / revenue if revenue > 0 else None

    objectives = {
        "equilibrium": _objective(real_costs, opex, gross, margin_pct),
        "debt": {**_objective(real_costs + debt, opex + debt, gross, margin_pct),
                 "objective_amount": money(debt), "applies": debt > 0},
        "total": {**_objective(real_costs + debt + profit, opex + debt + profit, gross, margin_pct),
                  "objective_amount": money(debt + profit), "applies": (debt + profit) > 0},
    }
    objectives["profit"] = {"objective_amount": money(profit), "applies": profit > 0}

    if operating_result < 0:
        indicator = "FALTA_EQUILIBRIO"
    elif operating_result >= debt + profit:
        indicator = "OBJETIVO_ALCANZADO"
    else:
        indicator = "EQUILIBRIO_ALCANZADO"

    # Clasificación: pueden coexistir varios conceptos.
    outcomes = []
    if operating_result < 0:
        outcomes.append({"code": "PERDIDA_OPERATIVA", "label": "🔴 Pérdida operativa", "amount": money(operating_result)})
    elif operating_result == 0:
        outcomes.append({"code": "EQUILIBRIO", "label": "🟡 Equilibrio", "amount": money(operating_result)})
    else:
        outcomes.append({"code": "GANANCIA_OPERATIVA", "label": "🟢 Ganancia operativa", "amount": money(operating_result)})
    if debt > 0:
        if operating_result >= debt:
            outcomes.append({"code": "OBJETIVO_DEUDA_ALCANZADO", "label": "🟢 Objetivo de deuda alcanzado",
                             "amount": money(operating_result - debt)})
        else:
            outcomes.append({"code": "OBJETIVO_DEUDA_NO_ALCANZADO", "label": "🟡 Objetivo de deuda: faltan",
                             "amount": money(debt - max(operating_result, ZERO))})
    if debt + profit > 0:
        if operating_result >= debt + profit:
            outcomes.append({"code": "OBJETIVO_TOTAL_ALCANZADO", "label": "🟢 Objetivo total alcanzado",
                             "amount": money(operating_result - debt - profit)})
        else:
            outcomes.append({"code": "OBJETIVO_TOTAL_NO_ALCANZADO", "label": "🟡 Objetivo total: faltan",
                             "amount": money(debt + profit - max(operating_result, ZERO))})

    warnings = []
    if items_without_cost:
        warnings.append({"code": "COSTO_INCOMPLETO",
                         "detail": f"{items_without_cost} ítem(s) vendido(s) sin costo registrado: "
                                   "el costo y el margen pueden estar incompletos"})
    if losses_without_cost:
        warnings.append({"code": "MERMA_SIN_COSTO",
                         "detail": f"{losses_without_cost} merma(s) sin costo registrado: no se valorizaron"})
    if mismatches:
        warnings.append({"code": "DIFERENCIA_TOTAL_VENTA",
                         "detail": f"{len(mismatches)} venta(s) con total distinto a la suma de sus ítems; "
                                   "se usa el total cobrado"})
    if unclassified:
        categories = ", ".join(sorted({str(m["category"] or "(sin categoría)") for m in unclassified}))
        warnings.append({"code": "CATEGORIA_GASTO_NO_RECONOCIDA",
                         "detail": f"{len(unclassified)} movimiento(s) de caja sin clasificar ({categories}) por "
                                   f"{ar_money(total['unclassified_expenses'])}: NO se restaron del resultado "
                                   "operativo hasta definir una regla para esa categoría",
                         "amount": money(total["unclassified_expenses"])})
    end = plan.get("end_date")
    if today is not None and end and today > (datetime.date.fromisoformat(end) if isinstance(end, str) else end):
        warnings.append({"code": "ABIERTA_FUERA_DE_PERIODO",
                         "detail": "⚠️ Esta jornada continúa abierta fuera del período planificado."})

    return {
        "live_calc_version": LIVE_CALC_VERSION,
        "indicator": {"code": indicator, "label": LIVE_INDICATOR_LABELS[indicator]},
        "result": {
            "revenue": money(revenue),
            "cogs": money(cogs),
            "gross_margin": money(gross),
            "gross_margin_pct": ratio(margin_pct),
            "planned_operating_costs": money(opex),
            "extra_expenses": money(extra),
            "losses_at_cost": money(loss_cost),
            "real_costs_total": money(real_costs),
            "operating_result": money(operating_result),
        },
        "planned_operating_costs": {k: money(v) for k, v in costs.items()},
        "targets": {"debt_target": money(debt), "profit_target": money(profit),
                    "capital_available": money(D(plan.get("capital_available")))},
        "objectives": objectives,
        "outcomes": outcomes,
        "accumulated": _bucket_out(total),
        "today": ({"date": today.isoformat(), **_bucket_out(days.get(today) or _empty_bucket())}
                  if today is not None else None),
        "days": [{"date": day.isoformat(), **_bucket_out(b)} for day, b in sorted(days.items())],
        "data_quality": {
            "complete": not (items_without_cost or losses_without_cost or unclassified),
            "items_without_cost": items_without_cost,
            "items_without_cost_revenue": money(items_without_cost_revenue),
            "losses_without_cost": losses_without_cost,
            "sales_total_mismatches": mismatches,
            "unclassified_cash_movements": unclassified,
            "unclassified_expenses": money(total["unclassified_expenses"]),
        },
        "warnings": warnings,
    }
