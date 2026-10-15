"""Parche de seguridad U1: los endpoints de usuarios no exponen credenciales y un pedido
anónimo no puede crear, activar, elevar, degradar ni deshabilitar administradores.

Todavía no existe autenticación real (Fase 2.1): estos tests verifican que las operaciones
peligrosas estén BLOQUEADAS y que las que usa la APK actual sigan funcionando.
Usan la base SQLite temporal de test_day_plans_api.
"""
import sqlite3

import pytest

from test_day_plans_api import _DB_PATH, client, main  # noqa: E402

SECRET = "Sup3r-Secreta-U1-xyz"
FORBIDDEN_KEYS = {"hashed_password", "password", "password_hash", "token", "access_token",
                  "secret", "salt", "session", "session_token"}
SAFE_KEYS = {"id", "name", "email", "role", "is_active", "branch_id", "can_preventa", "can_caja",
             "can_stock", "can_ingreso", "can_alertas", "can_rrhh", "can_mrp", "can_verificacion",
             "can_kpis", "can_carteleria", "can_edit_records"}
MASTER = "admin@fiambreria.com"


@pytest.fixture(autouse=True)
def clean_users():
    assert main.SQLALCHEMY_DATABASE_URL.endswith(_DB_PATH)

    def wipe():
        conn = sqlite3.connect(_DB_PATH)
        conn.execute("DELETE FROM users WHERE email LIKE '%@sec.test' "
                     "OR (LOWER(email) = ? AND email <> ?)", (MASTER, MASTER))
        conn.execute("DELETE FROM work_logs WHERE user_email LIKE '%@sec.test'")
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


def assert_no_credentials(payload):
    """Ningún objeto de usuario trae claves de credenciales ni el texto de la contraseña."""
    users = payload if isinstance(payload, list) else [payload]
    for u in users:
        assert not (set(u) & FORBIDDEN_KEYS), set(u) & FORBIDDEN_KEYS
        assert set(u) <= SAFE_KEYS, set(u) - SAFE_KEYS


def assert_blocked(r):
    assert r.status_code == 403, r.text
    assert r.headers["X-Error-Code"] == "ADMIN_OPERATION_BLOCKED_UNTIL_AUTH"
    assert isinstance(r.json()["detail"], str)


# --- Credenciales nunca expuestas ----------------------------------------------------------------

def test_get_users_never_exposes_passwords():
    add_user("vende@sec.test")
    add_user("super@sec.test", role="superadmin")
    r = client.get("/users")
    assert r.status_code == 200
    users = r.json()
    assert len(users) >= 3
    assert_no_credentials(users)
    assert all(set(u) == SAFE_KEYS for u in users)       # mismo contrato que usa la APK actual
    assert SECRET not in r.text
    assert "admin123" not in r.text


def test_serialize_user_is_a_whitelist():
    db = main.SessionLocal()
    u = db.query(main.UserDB).filter_by(email=MASTER).first()
    out = main.serialize_user(u)
    db.close()
    assert set(out) == SAFE_KEYS
    assert set(main.USER_PUBLIC_FIELDS) == SAFE_KEYS
    assert "hashed_password" not in out
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
        assert SECRET not in r.text, path
        assert "admin123" not in r.text, path
        assert "hashed_password" not in r.text, path
    r = client.post("/login", data={"username": "turno@sec.test", "password": SECRET})
    assert r.status_code == 200
    assert SECRET not in r.text and "hashed_password" not in r.text


# --- No se puede crear un administrador anónimamente --------------------------------------------

@pytest.mark.parametrize("role", ["masteradmin", "MASTERADMIN", "superadmin", "dueno", "otro"])
def test_anonymous_cannot_create_privileged_users(role):
    r = client.post("/users", json={"name": "X", "email": "x@sec.test", "password": SECRET, "role": role})
    assert_blocked(r)
    assert all(u["email"] != "x@sec.test" for u in client.get("/users").json())


@pytest.mark.parametrize("email", ["Admin@Fiambreria.com", "ADMIN@FIAMBRERIA.COM", " admin@fiambreria.com "])
def test_anonymous_cannot_create_master_email_variants(email):
    r = client.post("/users", json={"name": "X", "email": email, "password": SECRET})
    assert r.status_code == 409
    conn = sqlite3.connect(_DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM users WHERE LOWER(TRIM(email)) = ?", (MASTER,)).fetchone()[0]
    conn.close()
    assert count == 1
    # Tampoco puede convertirse en masteradmin vía login con esa variante.
    r = client.post("/login", data={"username": email.strip(), "password": SECRET})
    assert r.status_code == 400


def test_duplicate_email_is_rejected_case_insensitively():
    add_user("dup@sec.test")
    r = client.post("/users", json={"name": "X", "email": "DUP@sec.test", "password": SECRET})
    assert r.status_code == 409


def test_create_requires_basic_fields():
    assert client.post("/users", json={"name": "X", "email": "y@sec.test"}).status_code == 422


# --- No se puede elevar a MASTERADMIN/SUPERADMIN ni tocar administradores -----------------------

@pytest.mark.parametrize("role", ["masteradmin", "superadmin"])
def test_anonymous_cannot_elevate_existing_user(role):
    uid = add_user("vende@sec.test")
    assert_blocked(client.patch(f"/users/{uid}/permissions", json={"role": role}))
    assert user_row(uid)["role"] == "vendedor"


def test_anonymous_cannot_create_then_activate_an_admin():
    # El camino previo: POST con rol masteradmin + PATCH status. El primer paso ya se rechaza.
    assert_blocked(client.post("/users", json={"name": "X", "email": "x@sec.test", "password": SECRET,
                                               "role": "masteradmin"}))
    conn = sqlite3.connect(_DB_PATH)
    assert conn.execute("SELECT COUNT(*) FROM users WHERE email='x@sec.test'").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("payload", [{"role": "vendedor"}, {"role": "dueno"}, {"is_active": False},
                                     {"can_caja": False}, {"branch_id": 2}])
def test_anonymous_cannot_modify_master_admin(payload):
    mid = master_id()
    before = user_row(mid)
    assert_blocked(client.patch(f"/users/{mid}/permissions", json=payload))
    assert user_row(mid) == before


@pytest.mark.parametrize("is_active", [False, True])
def test_anonymous_cannot_change_status_of_master_admin(is_active):
    mid = master_id()
    before = user_row(mid)
    assert_blocked(client.patch(f"/users/{mid}/status", json={"is_active": is_active}))
    assert user_row(mid) == before


def test_protection_is_by_role_not_only_by_master_email():
    second_master = add_user("master2@sec.test", role="masteradmin")
    superadmin = add_user("super@sec.test", role="superadmin")
    for uid in (second_master, superadmin):
        before = user_row(uid)
        assert_blocked(client.patch(f"/users/{uid}/permissions", json={"role": "vendedor"}))
        assert_blocked(client.patch(f"/users/{uid}/permissions", json={"is_active": False}))
        assert_blocked(client.patch(f"/users/{uid}/status", json={"is_active": False}))
        assert user_row(uid) == before


def test_master_login_still_works_and_returns_masteradmin():
    r = client.post("/login", data={"username": MASTER, "password": "admin123"})
    assert r.status_code == 200
    assert r.json()["role"] == "masteradmin"


# --- Operaciones normales de la APK actual --------------------------------------------------------

def test_current_app_user_flows_keep_working():
    # Alta desde Permisos: la APK envía solo nombre, email y contraseña.
    r = client.post("/users", json={"name": "Empleado", "email": "emp@sec.test", "password": SECRET})
    assert r.status_code == 200
    created = r.json()
    assert created["role"] == "vendedor" and created["is_active"] is False
    uid = created["id"]

    # Habilitar / inhabilitar cuenta.
    assert client.patch(f"/users/{uid}/status", json={"is_active": True}).json()["is_active"] is True
    # Permisos por módulo, sucursal y roles no administrativos.
    assert client.patch(f"/users/{uid}/permissions", json={"can_caja": True}).json()["can_caja"] is True
    assert client.patch(f"/users/{uid}/permissions", json={"branch_id": 2}).json()["branch_id"] == 2
    assert client.patch(f"/users/{uid}/permissions", json={"role": "dueno"}).json()["role"] == "dueno"
    assert client.patch(f"/users/{uid}/permissions", json={"role": "vendedor"}).json()["role"] == "vendedor"
    assert client.patch(f"/users/{uid}/permissions", json={"role": "x"}).status_code == 400
    assert client.patch(f"/users/{uid}/permissions", json={"branch_id": 9}).status_code == 400

    # Login y refresco periódico (la APK busca su propio email en GET /users).
    r = client.post("/login", data={"username": "emp@sec.test", "password": SECRET})
    assert r.status_code == 200 and r.json()["can_caja"] is True
    me = next(u for u in client.get("/users").json() if u["email"] == "emp@sec.test")
    assert me["is_active"] is True and me["branch_id"] == 2 and me["can_caja"] is True

    # Inhabilitar.
    assert client.patch(f"/users/{uid}/status", json={"is_active": False}).json()["is_active"] is False
    assert user_row(uid)["is_active"] is False
