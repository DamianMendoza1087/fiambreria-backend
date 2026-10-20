"""Fase 2.2 — autorización estricta en todos los endpoints.

- Inventario: cada ruta tiene exactamente UNA política declarada; si aparece una ruta nueva sin
  política, test_every_route_declares_exactly_one_policy falla. No existe ningún modo legacy.
- Sin sesión: 401 en todo salvo GET /health y POST /login. Con cambio de contraseña pendiente:
  403 en todo salvo /auth/*.
- Rol, capacidad y sucursal se validan en backend; el actor sale de la sesión.
Usan la base SQLite temporal de test_day_plans_api; los usuarios usan el dominio @authz.test.
"""
import re
import sqlite3

import pytest

from test_day_plans_api import _DB_PATH, anon, client, main  # noqa: E402
import auth_core  # noqa: E402
import authkit  # noqa: E402

DOMAIN = "@authz.test"
PUBLIC_ROUTES = {("GET", "/health"), ("POST", "/login")}
PENDING_OK_ROUTES = {("GET", "/auth/me"), ("POST", "/auth/logout"), ("POST", "/auth/change-password")}


def wipe():
    conn = sqlite3.connect(_DB_PATH)
    ids = [r[0] for r in conn.execute("SELECT id FROM users WHERE email LIKE ?", ("%" + DOMAIN,))]
    for uid in ids:
        for table in ("user_credentials", "auth_sessions", "user_branches", "work_logs"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM audit_events WHERE entity_type = 'user' AND entity_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE email LIKE ?", ("%" + DOMAIN,))
    conn.execute("DELETE FROM cash_movements WHERE actor LIKE ? OR concept LIKE 'authz-%'", ("%" + DOMAIN,))
    conn.execute("DELETE FROM day_plans WHERE notes = 'authz-test'")
    conn.execute("DELETE FROM auth_login_failures")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    assert main.SQLALCHEMY_DATABASE_URL.endswith(_DB_PATH)
    monkeypatch.setattr(auth_core, "PBKDF2_ITERATIONS", 1000)
    wipe()
    yield
    wipe()


def who(name, role, branches=(1,), **caps):
    """Headers de una sesión real para un usuario de prueba con rol, sucursales y capacidades."""
    return authkit.headers_for(main, name + DOMAIN, role=role, branch_ids=list(branches), caps=caps)


def uid_of(name):
    return authkit.ensure_user(main, name + DOMAIN)


def code(r):
    return r.headers.get("X-Error-Code")


def query(sql, params=()):
    conn = sqlite3.connect(_DB_PATH)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def concrete(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def call(c, method, path, headers=None):
    return c.request(method, concrete(path), headers=headers or {}, json={})


# --- Inventario de políticas -----------------------------------------------------------------------

def test_every_route_declares_exactly_one_policy():
    routes = main.route_policies()
    assert len(routes) >= 76
    missing = sorted(k for k, v in routes.items() if len(v) == 0)
    duplicated = sorted(k for k, v in routes.items() if len(v) > 1)
    assert missing == [], f"Rutas sin política: {missing}"
    assert duplicated == [], f"Rutas con más de una política: {duplicated}"


def test_only_health_and_login_are_public_and_there_is_no_legacy_mode():
    routes = main.route_policies()
    public = {k for k, (p,) in routes.items() if p.public}
    assert public == PUBLIC_ROUTES
    pending_ok = {k for k, (p,) in routes.items() if p.allow_password_change}
    assert pending_ok == PENDING_OK_ROUTES
    for key, (policy,) in routes.items():
        described = policy.describe()
        assert "LEGACY" not in described["category"].upper(), key
        if not policy.public:
            assert described["min_role"] is not None or key in PENDING_OK_ROUTES, key
    source = open(main.__file__, encoding="utf-8").read().upper()
    for marker in ("LEGACY_TEMPORARY", "AUTH_LEGACY", "ALLOW_ANONYMOUS", "AUTH_BYPASS", "EMERGENCY_BYPASS"):
        assert marker not in source, marker


def test_policy_matrix_is_serializable_for_documentation():
    rows = [{"method": m, "path": p, **pol[0].describe()} for (m, p), pol in sorted(main.route_policies().items())]
    assert all(set(r) >= {"method", "path", "category", "min_role", "capabilities", "branch"} for r in rows)


# --- Sin sesión / sesión inválida / cambio pendiente ------------------------------------------------

def test_every_protected_route_rejects_requests_without_session():
    failures = []
    for (method, path), _ in sorted(main.route_policies().items()):
        if (method, path) in PUBLIC_ROUTES:
            continue
        r = call(anon, method, path)
        if r.status_code != 401 or code(r) != "AUTH_REQUIRED":
            failures.append((method, path, r.status_code, code(r)))
    assert failures == []


def test_every_protected_route_rejects_an_invalid_token():
    failures = []
    for (method, path), _ in sorted(main.route_policies().items()):
        if (method, path) in PUBLIC_ROUTES:
            continue
        r = call(anon, method, path, {"Authorization": "Bearer token-inventado-123"})
        if r.status_code != 401 or code(r) != "SESSION_INVALID":
            failures.append((method, path, r.status_code))
    assert failures == []


def test_pending_password_change_only_allows_auth_routes():
    headers = who("pendiente", "masteradmin")
    db = main.SessionLocal()
    cred = db.query(main.UserCredentialDB).filter_by(user_id=uid_of("pendiente")).first()
    cred.must_change_password = True
    cred.change_reason = auth_core.REASON_TEMPORARY
    db.commit()
    db.close()
    failures = []
    for (method, path), _ in sorted(main.route_policies().items()):
        if (method, path) in PUBLIC_ROUTES | PENDING_OK_ROUTES:
            continue
        r = call(anon, method, path, headers)
        if r.status_code != 403 or code(r) != "PASSWORD_CHANGE_REQUIRED":
            failures.append((method, path, r.status_code))
    assert failures == []
    assert anon.get("/auth/me", headers=headers).status_code == 200


# --- Rol y capacidad --------------------------------------------------------------------------------

def test_role_hierarchy_and_capabilities_on_cash():
    assert code(anon.get("/cash/status", headers=who("vende", "vendedor", can_caja=True))) == "FORBIDDEN_ROLE"
    assert code(anon.get("/cash/status", headers=who("caje", "cajero"))) == "FORBIDDEN_CAPABILITY"
    assert anon.get("/cash/status", headers=who("caje2", "cajero", can_caja=True)).status_code == 200
    assert anon.get("/cash/status", headers=who("super", "superadmin")).status_code == 200


def test_owner_only_and_master_only_routes():
    assert code(anon.get("/stock/movements", headers=who("enc", "encargado", can_stock=True))) == "FORBIDDEN_ROLE"
    assert anon.get("/stock/movements", headers=who("dueno", "dueno")).status_code == 200
    body = {"name": "x", "price_per_unit": 1}
    assert code(anon.post("/products", json=body, headers=who("super", "superadmin"))) == "FORBIDDEN_ROLE"
    assert code(anon.post("/promotions", json={}, headers=who("dueno2", "dueno"))) == "FORBIDDEN_ROLE"
    assert code(anon.delete("/products/1", headers=who("dueno3", "dueno"))) == "FORBIDDEN_ROLE"


def test_capability_is_waived_from_the_declared_role():
    # /kpis/dashboard: can_kpis, o Dueño o superior sin capacidad.
    assert code(anon.get("/kpis/dashboard", headers=who("enc", "encargado"))) == "FORBIDDEN_CAPABILITY"
    assert anon.get("/kpis/dashboard", headers=who("enc2", "encargado", can_kpis=True)).status_code == 200
    assert anon.get("/kpis/dashboard", headers=who("dueno", "dueno")).status_code == 200


def test_user_administration_requires_superadmin():
    target = authkit.ensure_user(main, "vende" + DOMAIN, role="vendedor")
    h = who("dueno", "dueno")
    for r in (anon.post("/users", json={"name": "x", "email": "x" + DOMAIN, "password": "clave larga 2026"}, headers=h),
              anon.patch(f"/users/{target}/permissions", json={"can_caja": True}, headers=h),
              anon.patch(f"/users/{target}/status", json={"is_active": False}, headers=h),
              anon.post(f"/users/{target}/reset-password", headers=h)):
        assert r.status_code == 403 and code(r) == "FORBIDDEN_ROLE"


# --- Sucursales -------------------------------------------------------------------------------------

def test_branch_is_validated_from_query_path_and_default():
    h = who("dueno", "dueno", branches=(1,))
    assert anon.get("/products?branch_id=1", headers=h).status_code == 200
    assert anon.get("/products", headers=h).status_code == 200                       # default 1
    r = anon.get("/products?branch_id=2", headers=h)
    assert r.status_code == 403 and code(r) == "FORBIDDEN_BRANCH"
    assert code(anon.get("/stock/movements?branch_id=2", headers=h)) == "FORBIDDEN_BRANCH"
    assert code(anon.get("/branches/2/fair-config", headers=h)) == "FORBIDDEN_BRANCH"
    # Listado de jornadas: por defecto es la Feria (2).
    assert code(anon.get("/day-plans", headers=h)) == "FORBIDDEN_BRANCH"
    r = anon.get("/day-plans?branch_id=1", headers=h)       # autorizado; el endpoint dice que no es Feria
    assert r.status_code == 422 and code(r) is None
    assert code(anon.get("/products?branch_id=abc", headers=h)) is None                # 422, no 500


def test_admins_reach_every_branch_and_multi_branch_employees_their_own():
    for role in ("masteradmin", "superadmin"):
        h = who(role, role, branches=(1,))
        assert anon.get("/products?branch_id=2", headers=h).status_code == 200
        assert anon.get("/stock/movements?branch_id=2", headers=h).status_code == 200
    h = who("multi", "dueno", branches=(1, 2))
    assert anon.get("/products?branch_id=1", headers=h).status_code == 200
    assert anon.get("/products?branch_id=2", headers=h).status_code == 200
    assert anon.get("/auth/me", headers=h).json()["allowed_branch_ids"] == [1, 2]
    v = who("vende", "vendedor", branches=(2,))
    assert anon.get("/products?branch_id=2", headers=v).status_code == 200
    assert code(anon.get("/products", headers=v)) == "FORBIDDEN_BRANCH"


def test_day_plan_branch_comes_from_the_stored_plan():
    db = main.SessionLocal()
    plan = main.DayPlanDB(branch_id=2, plan_type="DIA", start_date="2026-10-01", end_date="2026-10-01",
                          days_count=1, stall_total=0, staff_total=0, transport_outbound=0, transport_return=0,
                          transport_total=0, green_max_stock_pct=1, yellow_max_stock_pct=1, safety_margin_pct=0,
                          incomplete_data_tolerance_pct=0, comparable_days_count=0, minimum_comparable_days=0,
                          status="BORRADOR", notes="authz-test")
    db.add(plan)
    db.commit()
    plan_id = plan.id
    db.close()
    # Feria en vivo: Dueño o superior con acceso a la sucursal del plan; Encargado no automáticamente.
    assert code(anon.get(f"/day-plans/{plan_id}/live", headers=who("enc", "encargado", branches=(2,), can_rrhh=True,
                                                                   can_caja=True, can_kpis=True))) == "FORBIDDEN_ROLE"
    assert code(anon.get(f"/day-plans/{plan_id}/live", headers=who("dueno1", "dueno", branches=(1,)))) == "FORBIDDEN_BRANCH"
    assert anon.get(f"/day-plans/{plan_id}/live", headers=who("dueno2", "dueno", branches=(2,))).status_code == 409
    assert anon.get(f"/day-plans/{plan_id}", headers=who("dueno3", "dueno", branches=(2,))).status_code == 200
    r = anon.post(f"/day-plans/{plan_id}/open", json={"actor": "dueno1" + DOMAIN},
                  headers=who("dueno4", "dueno", branches=(1,)))
    assert code(r) == "FORBIDDEN_BRANCH"
    # Plan inexistente: el endpoint responde su 404.
    assert anon.get("/day-plans/99999999", headers=who("dueno5", "dueno", branches=(2,))).status_code == 404


def test_creating_a_day_plan_validates_the_body_branch():
    body = {"branch_id": 2, "plan_type": "DIA", "start_date": "2026-10-01", "notes": "authz-test"}
    h = who("dueno", "dueno", branches=(1,))
    for path in ("/day-plans", "/day-plans/preview"):
        r = anon.post(path, json=body, headers=h)
        assert r.status_code == 403 and code(r) == "FORBIDDEN_BRANCH", (path, r.text)
    assert query("SELECT COUNT(*) FROM day_plans WHERE notes = 'authz-test'") == [(0,)]


# --- Actor desde la sesión ----------------------------------------------------------------------------

def test_cash_movement_actor_comes_from_session_and_fake_actor_is_ignored():
    h = who("caje", "cajero", can_caja=True)
    r = anon.post("/cash/movements", headers=h, json={"movement_type": "OUT", "category": "GASTO_VARIO", "amount": 10,
                                                      "concept": "authz-actor", "actor": "admin@fiambreria.com"})
    assert r.status_code == 200, r.text
    assert r.json()["actor"] == "caje" + DOMAIN
    assert query("SELECT actor FROM cash_movements WHERE id = ?", (r.json()["id"],)) == [("caje" + DOMAIN,)]


def test_user_admin_audit_uses_session_identity():
    target = authkit.ensure_user(main, "vende" + DOMAIN, role="vendedor")
    h = who("super", "superadmin")
    assert anon.patch(f"/users/{target}/permissions", json={"can_caja": True}, headers=h).status_code == 200
    rows = query("SELECT actor, actor_user_id, actor_source, session_id FROM audit_events "
                 "WHERE entity_type='user' AND entity_id=? AND field_name='can_caja'", (target,))
    assert len(rows) == 1
    actor, actor_user_id, source, session_id = rows[0]
    assert (actor, actor_user_id, source) == ("super" + DOMAIN, uid_of("super"), "session")
    assert session_id is not None


# --- Administración de usuarios ---------------------------------------------------------------------

def test_masteradmin_administers_superadmins():
    r = client.post("/users", json={"name": "S", "email": "s2" + DOMAIN, "password": "clave larga 2026",
                                    "role": "superadmin"})
    assert r.status_code == 200 and r.json()["role"] == "superadmin"
    sid = r.json()["id"]
    assert client.patch(f"/users/{sid}/permissions", json={"role": "dueno"}).json()["role"] == "dueno"
    assert client.patch(f"/users/{sid}/status", json={"is_active": True}).status_code == 200


def test_superadmin_rules():
    h = who("super", "superadmin")
    other_super = authkit.ensure_user(main, "super2" + DOMAIN, role="superadmin")
    master = authkit.ensure_user(main, "master2" + DOMAIN, role="masteradmin")
    dueno = authkit.ensure_user(main, "dueno" + DOMAIN, role="dueno")
    vende = authkit.ensure_user(main, "vende" + DOMAIN, role="vendedor")
    for r in (anon.patch(f"/users/{vende}/permissions", json={"role": "superadmin"}, headers=h),
              anon.patch(f"/users/{vende}/permissions", json={"role": "masteradmin"}, headers=h),
              anon.patch(f"/users/{other_super}/permissions", json={"can_caja": True}, headers=h),
              anon.patch(f"/users/{master}/status", json={"is_active": False}, headers=h),
              anon.post(f"/users/{master}/reset-password", headers=h),
              anon.post(f"/users/{other_super}/reset-password", headers=h),
              anon.patch(f"/users/{uid_of('super')}/permissions", json={"role": "masteradmin"}, headers=h)):
        assert r.status_code == 403 and code(r) == "FORBIDDEN_USER_ADMIN", r.text
    assert anon.patch(f"/users/{vende}/permissions", json={"role": "dueno"}, headers=h).json()["role"] == "dueno"
    assert anon.patch(f"/users/{dueno}/permissions", json={"can_cash_privileged_outflows": True},
                      headers=h).status_code == 200


def test_branch_assignment_is_additive_and_validated():
    vende = authkit.ensure_user(main, "vende" + DOMAIN, role="vendedor", branch_ids=[1])
    r = client.patch(f"/users/{vende}/permissions", json={"branch_ids": [1, 2]})
    assert r.status_code == 200 and r.json()["branch_id"] == 1
    assert query("SELECT branch_id FROM user_branches WHERE user_id = ? ORDER BY branch_id", (vende,)) == [(1,), (2,)]
    assert client.patch(f"/users/{vende}/permissions", json={"branch_ids": []}).status_code == 400
    assert client.patch(f"/users/{vende}/permissions", json={"branch_ids": [1, 99]}).status_code == 400
    r = client.patch(f"/users/{vende}/permissions", json={"branch_id": 2})
    assert r.status_code == 200
    assert query("SELECT branch_id FROM user_branches WHERE user_id = ?", (vende,)) == [(2,)]


def test_last_masteradmin_rule():
    assert auth_core.violates_last_masteradmin({5}, 5, "superadmin", True)
    assert auth_core.violates_last_masteradmin({5}, 5, "masteradmin", False)
    assert not auth_core.violates_last_masteradmin({5, 6}, 5, "vendedor", True)
    assert not auth_core.violates_last_masteradmin({5}, 7, "vendedor", True)
    assert not auth_core.violates_last_masteradmin(set(), 5, "masteradmin", True)
    # La guarda del backend usa la lista real de MASTERADMIN activos.
    db = main.SessionLocal()
    try:
        user = db.query(main.UserDB).filter_by(email="admin@fiambreria.com").first()
        others = main.active_masteradmin_ids(db) - {user.id}
        db.query(main.UserDB).filter(main.UserDB.id.in_(others)).update({"is_active": False},
                                                                          synchronize_session=False)
        with pytest.raises(main.HTTPException) as exc:
            main.guard_last_masteradmin(db, user, "superadmin", True)
        assert exc.value.status_code == 409 and exc.value.headers["X-Error-Code"] == "LAST_MASTERADMIN"
        with pytest.raises(main.HTTPException):
            main.guard_last_masteradmin(db, user, "masteradmin", False)
        main.guard_last_masteradmin(db, user, "masteradmin", True)
    finally:
        db.rollback()
        db.close()


def test_masteradmin_can_demote_another_masteradmin_while_one_remains():
    m2 = authkit.ensure_user(main, "master2" + DOMAIN, role="masteradmin")
    r = client.patch(f"/users/{m2}/permissions", json={"role": "superadmin"})
    assert r.status_code == 200 and r.json()["role"] == "superadmin"


def test_employee_reset_by_superadmin_gives_one_use_temporary_password():
    vende = authkit.ensure_user(main, "vende" + DOMAIN, role="vendedor")
    old = authkit.token_for(main, vende)
    r = anon.post(f"/users/{vende}/reset-password", headers=who("super", "superadmin"))
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    temporary = r.json()["temporary_password"]
    assert auth_core.check_password_policy(temporary) is None
    assert r.json()["sessions_revoked"] >= 1
    assert code(anon.get("/auth/me", headers={"Authorization": f"Bearer {old}"})) == "SESSION_REVOKED"
    stored = query("SELECT password_hash, change_reason FROM user_credentials WHERE user_id = ?", (vende,))[0]
    assert temporary not in stored[0] and stored[1] == "TEMPORARY"
    audit = query("SELECT old_value, new_value FROM audit_events WHERE entity_type='user' AND entity_id=? "
                  "AND field_name='password'", (vende,))
    assert audit == [(None, None)]
    assert temporary not in " ".join(str(x) for row in query("SELECT * FROM audit_events") for x in row)
    login = anon.post("/login", data={"username": "vende" + DOMAIN, "password": temporary})
    assert login.status_code == 200 and login.json()["password_change_reason"] == "TEMPORARY"


# --- U17: egresos privilegiados ----------------------------------------------------------------------

def movement(headers, category, movement_type="OUT"):
    return anon.post("/cash/movements", headers=headers,
                     json={"movement_type": movement_type, "category": category, "amount": 5, "concept": "authz-u17"})


@pytest.mark.parametrize("category", ["PROVEEDOR", "pago proveedor", "Mercadería", "COMPRA-MERCADERIA",
                                      "Retiro Dueño", "retiro_dueno", " owner withdrawal "])
def test_privileged_outflows_need_owner_or_authorized_manager(category):
    cajero = who("caje", "cajero", can_caja=True, can_cash_privileged_outflows=True)   # guardado True: no alcanza
    r = movement(cajero, category)
    assert r.status_code == 403 and code(r) == "FORBIDDEN_CAPABILITY"
    enc = who("enc", "encargado", can_caja=True)
    assert code(movement(enc, category)) == "FORBIDDEN_CAPABILITY"
    enc_ok = who("enc2", "encargado", can_caja=True, can_cash_privileged_outflows=True)
    assert movement(enc_ok, category).status_code == 200
    dueno = who("dueno", "dueno", can_caja=True)
    assert movement(dueno, category).status_code == 200


def test_regular_outflows_and_inflows_only_need_cash_permission():
    cajero = who("caje", "cajero", can_caja=True)
    assert movement(cajero, "GASTO_VARIO").status_code == 200
    assert movement(cajero, "PROVEEDOR", "IN").status_code == 200
    assert movement(cajero, "Retiro Dueño", "in").status_code == 200


def test_privileged_capability_rule():
    allowed = auth_core.capability_allowed
    cap = "can_cash_privileged_outflows"
    assert allowed("masteradmin", cap, False) and allowed("superadmin", cap, False) and allowed("dueno", cap, False)
    assert allowed("encargado", cap, True) and not allowed("encargado", cap, False)
    assert not allowed("cajero", cap, True) and not allowed("vendedor", cap, True)
    assert not allowed("vendedor", "can_inventada", True)


def test_stock_losses_need_stock_capability():
    body = {"product_id": 99999999, "quantity": 1, "reason": "ROTURA"}
    r = anon.post("/stock/losses", json=body, headers=who("vende", "vendedor"))
    assert r.status_code == 403 and code(r) == "FORBIDDEN_CAPABILITY"
    assert anon.post("/stock/losses", json=body, headers=who("vende2", "vendedor", can_stock=True)).status_code == 404
    assert code(anon.post("/stock/losses?branch_id=2", json=body,
                          headers=who("vende3", "vendedor", can_stock=True))) == "FORBIDDEN_BRANCH"


# --- U16: turnos -------------------------------------------------------------------------------------

def test_own_shift_is_always_allowed():
    h = who("vende", "vendedor")
    me = uid_of("vende")
    r = anon.post("/hr/shifts/start", json={"user_id": me, "role_worked": "masteradmin"}, headers=h)
    assert r.status_code == 200 and r.json()["role_worked"] == "vendedor"          # no supera su rol real
    assert anon.post("/hr/shifts/end", json={"user_id": me}, headers=h).status_code == 200


def test_other_shifts_need_manager_with_rrhh_and_shared_branch():
    target = authkit.ensure_user(main, "vende" + DOMAIN, role="vendedor", branch_ids=[1])
    body = {"user_id": target}
    assert code(anon.post("/hr/shifts/start", json=body, headers=who("caje", "cajero", can_rrhh=True))) == "FORBIDDEN_ROLE"
    assert code(anon.post("/hr/shifts/start", json=body, headers=who("enc", "encargado"))) == "FORBIDDEN_ROLE"
    assert code(anon.post("/hr/shifts/start", json=body,
                          headers=who("enc2", "encargado", branches=(2,), can_rrhh=True))) == "FORBIDDEN_BRANCH"
    h = who("enc3", "encargado", branches=(1,), can_rrhh=True)
    assert anon.post("/hr/shifts/start", json=body, headers=h).status_code == 200
    assert anon.post("/hr/shifts/end", json=body, headers=h).status_code == 200
    master = query("SELECT id FROM users WHERE email = 'admin@fiambreria.com'")[0][0]
    assert code(anon.post("/hr/shifts/start", json={"user_id": master}, headers=h)) == "FORBIDDEN_BRANCH"


def test_hr_lists_only_show_visible_employees():
    authkit.ensure_user(main, "uno" + DOMAIN, role="vendedor", branch_ids=[1])
    authkit.ensure_user(main, "dos" + DOMAIN, role="vendedor", branch_ids=[2])
    emails = {u["email"] for u in anon.get("/users", headers=who("dueno", "dueno", branches=(2,))).json()}
    assert "dos" + DOMAIN in emails and "uno" + DOMAIN not in emails
    assert "admin@fiambreria.com" not in emails
    assert code(anon.get("/hr/performance?email=uno" + DOMAIN,
                         headers=who("dueno2", "dueno", branches=(2,)))) == "FORBIDDEN_BRANCH"
    assert code(anon.get("/users", headers=who("enc", "encargado"))) == "FORBIDDEN_CAPABILITY"


# --- DEFAULT_PASSWORD ---------------------------------------------------------------------------------

def test_default_password_cannot_take_over_the_seed_master_in_test_db():
    r = anon.post("/login", data={"username": "admin@fiambreria.com", "password": "admin123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r = anon.post("/auth/change-password", headers=h,
                  json={"current_password": "admin123", "new_password": "frase elegida por intruso"})
    assert r.status_code == 403 and code(r) == "DEFAULT_PASSWORD_RESET_REQUIRED"
    assert code(anon.get("/users", headers=h)) == "PASSWORD_CHANGE_REQUIRED"
    assert anon.post("/auth/logout", headers=h).status_code == 200
    reason = query("SELECT c.change_reason FROM user_credentials c JOIN users u ON u.id = c.user_id "
                   "WHERE u.email = 'admin@fiambreria.com'")
    assert reason == [("DEFAULT_PASSWORD",)]


# --- Sin secretos --------------------------------------------------------------------------------------

def test_authorization_errors_do_not_leak_details():
    r = anon.get("/stock/movements?branch_id=2", headers=who("dueno", "dueno"))
    assert r.status_code == 403 and set(r.json()) == {"detail"}
    assert "token" not in r.text.lower() and "hash" not in r.text.lower()
