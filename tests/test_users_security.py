"""Usuarios: sin credenciales en respuestas y sin escalada a administrador.

Origen: parche U1 (pedidos anónimos bloqueados). Fase 2.2 reemplaza esos bloqueos por autorización
real; estos tests conservan la intención con la semántica nueva:
- anónimo: 401 en toda la administración de usuarios;
- SUPERADMIN: nunca crea, promueve ni modifica MASTERADMIN/SUPERADMIN;
- MASTERADMIN: administra todo salvo a sí mismo;
- ninguna respuesta incluye credenciales.
Usan la base SQLite temporal de test_day_plans_api.
"""
import sqlite3

import pytest

from test_day_plans_api import _DB_PATH, anon, client, main  # noqa: E402
import authkit  # noqa: E402

SECRET = "Sup3r-Secreta-U1-xyz"
FORBIDDEN_KEYS = {"hashed_password", "password", "password_hash", "token", "access_token",
                  "secret", "salt", "session", "session_token"}
SAFE_KEYS = {"id", "name", "email", "role", "is_active", "branch_id", "can_preventa", "can_caja",
             "can_stock", "can_ingreso", "can_alertas", "can_rrhh", "can_mrp", "can_verificacion",
             "can_kpis", "can_carteleria", "can_edit_records", "can_cash_privileged_outflows"}
MASTER = "admin@fiambreria.com"


@pytest.fixture(autouse=True)
def clean_users():
    assert main.SQLALCHEMY_DATABASE_URL.endswith(_DB_PATH)

    def wipe():
        conn = sqlite3.connect(_DB_PATH)
        ids = [r[0] for r in conn.execute("SELECT id FROM users WHERE email LIKE '%@sec.test' "
                                          "OR (LOWER(email) = ? AND email <> ?)", (MASTER, MASTER))]
        for uid in ids:
            for table in ("user_credentials", "auth_sessions", "user_branches"):
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE email LIKE '%@sec.test' "
                     "OR (LOWER(email) = ? AND email <> ?)", (MASTER, MASTER))
        conn.execute("DELETE FROM work_logs WHERE user_email LIKE '%@sec.test'")
        conn.execute("DELETE FROM auth_login_failures")
        conn.commit()
        conn.close()

    wipe()
    yield
    wipe()


def add_user(email, role="vendedor", active=True, password=SECRET):
    db = main.SessionLocal()
    u = main.UserDB(name=email.split("@")[0], email=email, hashed_password=password, role=role,
                    is_active=active)
    db.add(u)
    db.commit()
    uid = u.id
    db.close()
    return uid


def user_row(uid):
    db = main.SessionLocal()
    u = db.query(main.UserDB).filter_by(id=uid).first()
    data = None if u is None else {"role": u.role, "is_active": bool(u.is_active), "can_caja": bool(u.can_caja),
                                   "branch_id": u.branch_id}
    db.close()
    return data


def master_id():
    db = main.SessionLocal()
    uid = db.query(main.UserDB).filter_by(email=MASTER).first().id
    db.close()
    return uid


def superadmin_headers():
    return authkit.headers_for(main, "super@sec.test", role="superadmin")


def assert_no_credentials(payload):
    users = payload if isinstance(payload, list) else [payload]
    for u in users:
        assert not (set(u) & FORBIDDEN_KEYS), set(u) & FORBIDDEN_KEYS
        assert set(u) <= SAFE_KEYS, set(u) - SAFE_KEYS


def assert_user_admin_forbidden(r):
    assert r.status_code == 403, r.text
    assert r.headers["X-Error-Code"] == "FORBIDDEN_USER_ADMIN"
    assert isinstance(r.json()["detail"], str)


# --- Credenciales nunca expuestas ----------------------------------------------------------------

def test_get_users_never_exposes_passwords():
    add_user("vende@sec.test")
    add_user("super2@sec.test", role="superadmin")
    r = client.get("/users")
    assert r.status_code == 200
    users = r.json()
    assert len(users) >= 3
    assert_no_credentials(users)
    assert all(set(u) == SAFE_KEYS for u in users)
    assert SECRET not in r.text and "admin123" not in r.text


def test_serialize_user_is_a_whitelist():
    db = main.SessionLocal()
    u = db.query(main.UserDB).filter_by(email=MASTER).first()
    out = main.serialize_user(u)
    db.close()
    assert set(out) == SAFE_KEYS == set(main.USER_PUBLIC_FIELDS)
    # Cualquier columna nueva de users queda fuera hasta agregarla explícitamente a la lista.
    assert set(c.name for c in main.UserDB.__table__.columns) - SAFE_KEYS == {"hashed_password"}


def test_user_write_endpoints_never_echo_credentials():
    r = client.post("/users", json={"name": "Nuevo", "email": "nuevo@sec.test", "password": SECRET})
    assert r.status_code == 200, r.text
    assert_no_credentials(r.json())
    assert SECRET not in r.text
    uid = r.json()["id"]
    r = client.patch(f"/users/{uid}/permissions", json={"can_caja": True})
    assert r.status_code == 200 and SECRET not in r.text
    assert_no_credentials(r.json())
    r = client.patch(f"/users/{uid}/status", json={"is_active": True})
    assert r.status_code == 200 and SECRET not in r.text


def test_other_user_derived_endpoints_do_not_leak_passwords():
    uid = add_user("turno@sec.test")
    assert client.post("/hr/shifts/start", json={"user_id": uid}).status_code == 200
    for path in ("/hr/shifts/active", "/hr/worklogs", "/hr/performance?email=turno@sec.test",
                 "/hr/compare?emails=turno@sec.test", "/hr/compare"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert SECRET not in r.text and "admin123" not in r.text and "hashed_password" not in r.text, path
    r = anon.post("/login", data={"username": "turno@sec.test", "password": SECRET})
    assert r.status_code == 200
    assert SECRET not in r.text and "hashed_password" not in r.text


# --- Anónimo: nada ---------------------------------------------------------------------------------

def test_anonymous_cannot_use_user_administration():
    uid = add_user("vende@sec.test")
    for r in (anon.get("/users"),
              anon.post("/users", json={"name": "X", "email": "x@sec.test", "password": SECRET, "role": "masteradmin"}),
              anon.patch(f"/users/{uid}/permissions", json={"role": "masteradmin"}),
              anon.patch(f"/users/{master_id()}/status", json={"is_active": False}),
              anon.post(f"/users/{uid}/reset-password")):
        assert r.status_code == 401 and r.headers["X-Error-Code"] == "AUTH_REQUIRED"
    assert user_row(uid)["role"] == "vendedor"
    assert user_row(master_id())["is_active"] is True


# --- SUPERADMIN nunca llega a MASTERADMIN ---------------------------------------------------------

@pytest.mark.parametrize("role", ["masteradmin", "MASTERADMIN", "superadmin"])
def test_superadmin_cannot_create_admin_level_users(role):
    r = client.post("/users", json={"name": "X", "email": "x@sec.test", "password": SECRET, "role": role},
                    headers=superadmin_headers())
    assert_user_admin_forbidden(r)
    assert all(u["email"] != "x@sec.test" for u in client.get("/users").json())


def test_superadmin_creates_up_to_owner_and_invalid_role_is_400():
    h = superadmin_headers()
    r = client.post("/users", json={"name": "D", "email": "dueno@sec.test", "password": SECRET, "role": "dueno"}, headers=h)
    assert r.status_code == 200 and r.json()["role"] == "dueno" and r.json()["is_active"] is False
    r = client.post("/users", json={"name": "Y", "email": "y@sec.test", "password": SECRET, "role": "otro"}, headers=h)
    assert r.status_code == 400


@pytest.mark.parametrize("role", ["masteradmin", "superadmin"])
def test_superadmin_cannot_promote_existing_user(role):
    uid = add_user("vende@sec.test")
    assert_user_admin_forbidden(client.patch(f"/users/{uid}/permissions", json={"role": role},
                                             headers=superadmin_headers()))
    assert user_row(uid)["role"] == "vendedor"


@pytest.mark.parametrize("payload", [{"role": "vendedor"}, {"is_active": False}, {"can_caja": False},
                                     {"branch_id": 2}])
def test_superadmin_cannot_modify_master_admin(payload):
    mid = master_id()
    before = user_row(mid)
    assert_user_admin_forbidden(client.patch(f"/users/{mid}/permissions", json=payload, headers=superadmin_headers()))
    assert_user_admin_forbidden(client.patch(f"/users/{mid}/status", json={"is_active": False},
                                             headers=superadmin_headers()))
    assert user_row(mid) == before


def test_superadmin_cannot_modify_another_superadmin():
    other = add_user("super2@sec.test", role="superadmin")
    assert_user_admin_forbidden(client.patch(f"/users/{other}/permissions", json={"role": "vendedor"},
                                             headers=superadmin_headers()))


# --- Altas y emails --------------------------------------------------------------------------------

@pytest.mark.parametrize("email", ["Admin@Fiambreria.com", "ADMIN@FIAMBRERIA.COM", " admin@fiambreria.com "])
def test_master_email_variants_cannot_be_created(email):
    r = client.post("/users", json={"name": "X", "email": email, "password": SECRET})
    assert r.status_code == 409
    conn = sqlite3.connect(_DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM users WHERE LOWER(TRIM(email)) = ?", (MASTER,)).fetchone()[0]
    conn.close()
    assert count == 1


def test_duplicate_email_is_rejected_case_insensitively():
    add_user("dup@sec.test")
    assert client.post("/users", json={"name": "X", "email": "DUP@sec.test", "password": SECRET}).status_code == 409


def test_create_requires_basic_fields():
    assert client.post("/users", json={"name": "X", "email": "y@sec.test"}).status_code == 422


# --- Flujo normal de administración (MASTERADMIN) ----------------------------------------------------

def test_master_admin_user_flows():
    r = client.post("/users", json={"name": "Empleado", "email": "emp@sec.test", "password": SECRET})
    assert r.status_code == 200
    created = r.json()
    assert created["role"] == "vendedor" and created["is_active"] is False
    uid = created["id"]
    assert client.patch(f"/users/{uid}/status", json={"is_active": True}).json()["is_active"] is True
    assert client.patch(f"/users/{uid}/permissions", json={"can_caja": True}).json()["can_caja"] is True
    assert client.patch(f"/users/{uid}/permissions", json={"branch_id": 2}).json()["branch_id"] == 2
    for role in ("dueno", "encargado", "cajero", "vendedor", "superadmin", "vendedor"):
        assert client.patch(f"/users/{uid}/permissions", json={"role": role}).json()["role"] == role
    assert client.patch(f"/users/{uid}/permissions", json={"role": "x"}).status_code == 400
    assert client.patch(f"/users/{uid}/permissions", json={"branch_id": 9}).status_code == 400
    r = anon.post("/login", data={"username": "emp@sec.test", "password": SECRET})
    assert r.status_code == 200 and r.json()["can_caja"] is True and r.json()["allowed_branch_ids"] == [2]
    assert client.patch(f"/users/{uid}/status", json={"is_active": False}).json()["is_active"] is False
    assert user_row(uid)["is_active"] is False


def test_nobody_changes_their_own_user():
    me = authkit.ensure_user(main, authkit.ADMIN_EMAIL)
    for r in (client.patch(f"/users/{me}/permissions", json={"role": "vendedor"}),
              client.patch(f"/users/{me}/status", json={"is_active": False}),
              client.post(f"/users/{me}/reset-password")):
        assert_user_admin_forbidden(r)
    assert user_row(me)["role"] == "masteradmin" and user_row(me)["is_active"] is True


def test_master_login_with_admin123_cannot_take_over_the_account():
    r = anon.post("/login", data={"username": MASTER, "password": "admin123"})
    assert r.status_code == 200 and r.json()["password_change_reason"] == "DEFAULT_PASSWORD"
    token = r.json()["access_token"]
    r = anon.post("/auth/change-password", headers={"Authorization": f"Bearer {token}"},
                  json={"current_password": "admin123", "new_password": "una clave del intruso"})
    assert r.status_code == 403 and r.headers["X-Error-Code"] == "DEFAULT_PASSWORD_RESET_REQUIRED"
    assert anon.post("/login", data={"username": MASTER, "password": "una clave del intruso"}).status_code == 400
