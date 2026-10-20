"""Fase 2.1 — login, sesiones, revocación, bloqueo por intentos y ausencia de secretos.

Usan la base SQLite temporal de test_day_plans_api. Los usuarios de estos tests usan el dominio
@auth.test y se borran al terminar cada test, junto con sesiones e intentos fallidos.
Para que la suite sea rápida, el costo de PBKDF2 se baja a 1000 iteraciones en este módulo
(test_auth_core verifica el costo real de 600.000).
"""
import datetime
import sqlite3

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from test_day_plans_api import _DB_PATH, client, main  # noqa: E402
import auth_core  # noqa: E402

STRONG = "salame tandilero 2026"
OTHER = "queso azul con nueces"
MASTER = "admin@fiambreria.com"

# App mínima para probar la dependencia estricta que usarán los endpoints en 2.2.
probe_app = FastAPI()


@probe_app.get("/probe")
def _probe(ctx: main.AuthContext = Depends(main.require_session)):
    fields = ctx.audit_fields()
    return {"actor": fields["actor"], "actor_user_id": fields["actor_user_id"],
            "actor_source": fields["actor_source"]}


probe = TestClient(probe_app)


def wipe():
    conn = sqlite3.connect(_DB_PATH)
    ids = [r[0] for r in conn.execute("SELECT id FROM users WHERE email LIKE '%@auth.test'")]
    conn.execute("DELETE FROM auth_login_failures")
    conn.execute("DELETE FROM auth_sessions")
    for uid in ids:
        conn.execute("DELETE FROM user_credentials WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM audit_events WHERE entity_type = 'user' AND entity_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE email LIKE '%@auth.test'")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    assert main.SQLALCHEMY_DATABASE_URL.endswith(_DB_PATH)
    monkeypatch.setattr(auth_core, "PBKDF2_ITERATIONS", 1000)
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    monkeypatch.delenv("AUTH_IP_LIMIT_MODE", raising=False)
    wipe()
    yield
    wipe()


@pytest.fixture
def clock(monkeypatch):
    state = {"now": datetime.datetime.utcnow().replace(microsecond=0)}
    monkeypatch.setattr(main, "_auth_utcnow", lambda: state["now"])
    return state


def add_user(email, role="vendedor", active=True, legacy=None, password=None, must_change=False, reason=None):
    db = main.SessionLocal()
    u = main.UserDB(name=email.split("@")[0], email=email, role=role, is_active=active,
                    hashed_password=legacy if legacy is not None else auth_core.unusable_legacy_value())
    db.add(u)
    db.flush()
    if password is not None:
        main.set_user_password(db, u, password, must_change, reason, datetime.datetime.utcnow())
    db.commit()
    uid = u.id
    db.close()
    return uid


def login(email, password, headers=None):
    return client.post("/login", data={"username": email, "password": password}, headers=headers or {})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def token_for(email, password=STRONG, headers=None):
    r = login(email, password, headers)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def code(r):
    return r.headers.get("X-Error-Code")


def query(sql, params=()):
    conn = sqlite3.connect(_DB_PATH)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def set_user(uid, **values):
    db = main.SessionLocal()
    db.query(main.UserDB).filter_by(id=uid).update(values)
    db.commit()
    db.close()


# --- Login --------------------------------------------------------------------------------------

def test_login_success_returns_real_session_and_legacy_fields():
    uid = add_user("ok@auth.test", password=STRONG)
    r = login("ok@auth.test", STRONG)
    assert r.status_code == 200
    body = r.json()
    for field in ("role", "is_active", "can_preventa", "can_caja", "can_stock", "can_ingreso", "can_alertas",
                  "can_rrhh", "can_mrp", "can_verificacion", "can_kpis", "can_carteleria",
                  "can_edit_records", "branch_id", "token_type"):
        assert field in body                                    # contrato que usa la APK instalada
    token = body["access_token"]
    assert len(token) >= 40 and not token.startswith("token-")
    assert body["must_change_password"] is False and body["allowed_branch_ids"] == [1]
    # En la base solo queda el SHA-256 del token.
    stored = query("SELECT token_hash FROM auth_sessions WHERE user_id = ?", (uid,))
    assert stored == [(auth_core.token_digest(token),)]
    me = client.get("/auth/me", headers=bearer(token))
    assert me.status_code == 200 and me.json()["user"]["email"] == "ok@auth.test"


def test_login_email_is_case_insensitive():
    add_user("mixto@auth.test", password=STRONG)
    assert login("MIXTO@Auth.Test", STRONG).status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_answer():
    add_user("ok@auth.test", password=STRONG)
    wrong = login("ok@auth.test", OTHER)
    unknown = login("nadie@auth.test", OTHER)
    for r in (wrong, unknown):
        assert r.status_code == 400 and r.json()["detail"] == "Credenciales incorrectas"
        assert code(r) == "INVALID_CREDENTIALS"
    assert len(query("SELECT 1 FROM auth_login_failures")) == 2


def test_legacy_user_logs_in_without_writing_credentials_and_must_change():
    uid = add_user("legado@auth.test", legacy="clave heredada uno")
    r = login("legado@auth.test", "clave heredada uno")
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True
    assert r.json()["password_change_reason"] == "LEGACY_PLAINTEXT"
    # No se escribió ninguna credencial ni se tocó el texto heredado (decisión: migración controlada).
    assert query("SELECT 1 FROM user_credentials WHERE user_id = ?", (uid,)) == []
    assert query("SELECT hashed_password FROM users WHERE id = ?", (uid,)) == [("clave heredada uno",)]
    assert login("legado@auth.test", "otra cosa").status_code == 400


def test_unusable_legacy_value_cannot_be_used_to_log_in():
    uid = add_user("sinclave@auth.test")
    value = query("SELECT hashed_password FROM users WHERE id = ?", (uid,))[0][0]
    assert value.startswith("!unusable:")
    assert login("sinclave@auth.test", value).status_code == 400


def test_master_seed_in_test_db_is_hashed_and_flagged():
    master = query("SELECT id, hashed_password FROM users WHERE email = ?", (MASTER,))[0]
    assert master[1].startswith("!unusable:")                  # sin admin123 en texto plano
    cred = query("SELECT password_hash, must_change_password, change_reason FROM user_credentials "
                 "WHERE user_id = ?", (master[0],))[0]
    assert cred[0].startswith("pbkdf2_sha256$600000$") and cred[1] == 1 and cred[2] == "DEFAULT_PASSWORD"
    r = login(MASTER, "admin123")
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True and r.json()["password_change_reason"] == "DEFAULT_PASSWORD"
    assert r.json()["role"] == "masteradmin"


# --- admin123 y cambio obligatorio ----------------------------------------------------------------

def test_admin123_requires_change_and_cannot_operate_until_changed():
    uid = add_user("maestro@auth.test", role="masteradmin", legacy="admin123")
    r = login("maestro@auth.test", "admin123")
    assert r.status_code == 200 and r.json()["password_change_reason"] == "DEFAULT_PASSWORD"
    token = r.json()["access_token"]

    # /auth/me funciona para poder mostrar la pantalla de cambio, pero no se puede operar.
    assert client.get("/auth/me", headers=bearer(token)).json()["must_change_password"] is True
    blocked = probe.get("/probe", headers=bearer(token))
    assert blocked.status_code == 403 and code(blocked) == "PASSWORD_CHANGE_REQUIRED"

    # No se puede elegir admin123 de nuevo.
    r = client.post("/auth/change-password", headers=bearer(token),
                    json={"current_password": "admin123", "new_password": "admin123"})
    assert r.status_code == 422 and code(r) == "PASSWORD_POLICY"

    r = client.post("/auth/change-password", headers=bearer(token),
                    json={"current_password": "admin123", "new_password": STRONG})
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    ok = probe.get("/probe", headers=bearer(token))
    assert ok.status_code == 200
    assert ok.json() == {"actor": "maestro@auth.test", "actor_user_id": uid, "actor_source": "session"}

    assert login("maestro@auth.test", "admin123").status_code == 400
    assert login("maestro@auth.test", STRONG).json()["must_change_password"] is False
    # U8=A: el texto heredado sigue en la base para rollback, pero ya no permite entrar.
    assert query("SELECT hashed_password FROM users WHERE id = ?", (uid,)) == [("admin123",)]


# --- Usuario deshabilitado ------------------------------------------------------------------------

def test_disabled_user_cannot_authenticate():
    add_user("baja@auth.test", password=STRONG, active=False)
    r = login("baja@auth.test", STRONG)
    assert r.status_code == 403 and code(r) == "USER_DISABLED"
    assert login("baja@auth.test", OTHER).status_code == 400


def test_disabling_a_user_invalidates_existing_sessions():
    uid = add_user("activo@auth.test", password=STRONG)
    token = token_for("activo@auth.test")
    assert client.patch(f"/users/{uid}/status", json={"is_active": False}).status_code == 200
    r = client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 401 and code(r) in ("SESSION_REVOKED", "USER_DISABLED")

    # Aunque la sesión no se hubiera revocado, un usuario inactivo no pasa.
    uid2 = add_user("activo2@auth.test", password=STRONG)
    token2 = token_for("activo2@auth.test")
    set_user(uid2, is_active=False)
    r = client.get("/auth/me", headers=bearer(token2))
    assert r.status_code == 401 and code(r) == "USER_DISABLED"


# --- Bloqueo por cuenta (siempre activo) ----------------------------------------------------------

def test_account_lock_after_five_failures_for_fifteen_minutes(clock):
    add_user("lock@auth.test", password=STRONG)
    t0 = clock["now"]
    for _ in range(5):
        assert login("lock@auth.test", OTHER).status_code == 400
    r = login("lock@auth.test", STRONG)                            # ni la clave correcta pasa
    assert r.status_code == 429 and code(r) == "ACCOUNT_LOCKED"
    assert 0 < int(r.headers["Retry-After"]) <= 15 * 60

    clock["now"] = t0 + datetime.timedelta(minutes=14, seconds=59)
    assert login("lock@auth.test", STRONG).status_code == 429
    clock["now"] = t0 + datetime.timedelta(minutes=15, seconds=1)
    assert login("lock@auth.test", STRONG).status_code == 200
    assert query("SELECT 1 FROM auth_login_failures WHERE email_key = 'lock@auth.test'") == []


def test_attempts_while_locked_do_not_extend_the_lock(clock):
    add_user("lock@auth.test", password=STRONG)
    t0 = clock["now"]
    for _ in range(5):
        login("lock@auth.test", OTHER)
    clock["now"] = t0 + datetime.timedelta(minutes=10)
    for _ in range(3):
        assert login("lock@auth.test", OTHER).status_code == 429
    assert len(query("SELECT 1 FROM auth_login_failures")) == 5
    clock["now"] = t0 + datetime.timedelta(minutes=15, seconds=1)
    assert login("lock@auth.test", STRONG).status_code == 200


def test_unknown_emails_are_locked_the_same_way():
    for _ in range(5):
        login("fantasma@auth.test", OTHER)
    r = login("fantasma@auth.test", OTHER)
    assert r.status_code == 429 and code(r) == "ACCOUNT_LOCKED"


def test_lock_is_stored_in_the_database():
    add_user("lock@auth.test", password=STRONG)
    for _ in range(5):
        login("lock@auth.test", OTHER)
    rows = query("SELECT email_key, ip FROM auth_login_failures")
    assert len(rows) == 5 and all(r == ("lock@auth.test", "testclient") for r in rows)
    # Una sesión de base nueva (como tras reiniciar el proceso) ve el mismo bloqueo.
    db = main.SessionLocal()
    try:
        with pytest.raises(main.HTTPException) as exc:
            main._check_login_limits(db, "lock@auth.test",
                                     auth_core.resolve_client_address("testclient", None, 0),
                                     main._auth_utcnow())
        assert exc.value.status_code == 429
    finally:
        db.close()


# --- Límite por IP (U18=B) -----------------------------------------------------------------------

def xff(ip):
    return {"X-Forwarded-For": ip}


def test_ip_limit_enforced_uses_only_trusted_proxy_entry(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setenv("AUTH_IP_LIMIT_MODE", "enforce")
    add_user("victima@auth.test", password=STRONG)
    for i in range(20):                                     # emails distintos: no bloquea ninguna cuenta
        assert login(f"x{i}@auth.test", OTHER, xff("203.0.113.7")).status_code == 400
    r = login("victima@auth.test", STRONG, xff("203.0.113.7"))
    assert r.status_code == 429 and code(r) == "TOO_MANY_ATTEMPTS_IP"
    # Un valor inventado a la izquierda no cambia la IP considerada.
    assert login("victima@auth.test", STRONG, xff("1.1.1.1, 203.0.113.7")).status_code == 429
    # Otra IP real sigue entrando.
    assert login("victima@auth.test", STRONG, xff("198.51.100.4")).status_code == 200


def test_ip_limit_in_observe_mode_records_but_never_blocks(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setenv("AUTH_IP_LIMIT_MODE", "observe")
    uid = add_user("victima@auth.test", password=STRONG)
    for i in range(25):
        login(f"x{i}@auth.test", OTHER, xff("9.9.9.9, 203.0.113.7"))
    assert len(query("SELECT 1 FROM auth_login_failures WHERE ip = '203.0.113.7'")) == 25
    r = login("victima@auth.test", STRONG, xff("9.9.9.9, 203.0.113.7"))
    assert r.status_code == 200
    # Datos mínimos para verificar la IP real: IP elegida, conexión y cadena (sin secretos).
    row = query("SELECT ip, peer_ip, forwarded_chain, ip_source FROM auth_sessions WHERE user_id = ?", (uid,))[0]
    assert row == ("203.0.113.7", "testclient", '["9.9.9.9", "203.0.113.7"]', "forwarded")


def test_forwarded_chain_is_not_stored_when_enforcing(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    monkeypatch.setenv("AUTH_IP_LIMIT_MODE", "enforce")
    uid = add_user("ok@auth.test", password=STRONG)
    token_for("ok@auth.test", headers=xff("203.0.113.7"))
    assert query("SELECT forwarded_chain FROM auth_sessions WHERE user_id = ?", (uid,)) == [(None,)]


def test_forwarded_for_is_ignored_without_trusted_proxies():
    for i in range(20):
        login(f"x{i}@auth.test", OTHER, xff(f"203.0.113.{i}"))
    assert {r[0] for r in query("SELECT ip FROM auth_login_failures")} == {"testclient"}


def test_auth_settings_defaults_and_invalid_values(monkeypatch):
    assert main.auth_settings() == {"trusted_proxy_hops": 0, "ip_limit_mode": "enforce"}  # fuera de producción
    for bad in ("abc", "-1", "9"):
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", bad)
        assert main.auth_settings()["trusted_proxy_hops"] == 0
    monkeypatch.setenv("AUTH_IP_LIMIT_MODE", "cualquiera")
    assert main.auth_settings()["ip_limit_mode"] == "enforce"
    monkeypatch.setenv("AUTH_IP_LIMIT_MODE", "OBSERVE")
    assert main.auth_settings()["ip_limit_mode"] == "observe"


# --- Sesiones -------------------------------------------------------------------------------------

def test_missing_invalid_and_revoked_tokens():
    add_user("ok@auth.test", password=STRONG)
    r = client.get("/auth/me")
    assert r.status_code == 401 and code(r) == "AUTH_REQUIRED" and r.headers["WWW-Authenticate"] == "Bearer"
    assert code(client.get("/auth/me", headers={"Authorization": "Basic abc"})) == "AUTH_REQUIRED"
    assert code(client.get("/auth/me", headers=bearer("inventado"))) == "SESSION_INVALID"
    token = token_for("ok@auth.test")
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 200
    r = client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 401 and code(r) == "SESSION_REVOKED"


def test_employee_session_lasts_at_most_12_hours(clock):
    add_user("emp@auth.test", password=STRONG)
    token = token_for("emp@auth.test")
    t0 = clock["now"]
    clock["now"] = t0 + datetime.timedelta(hours=11, minutes=59)
    assert client.get("/auth/me", headers=bearer(token)).status_code == 200
    clock["now"] = t0 + datetime.timedelta(hours=12, seconds=1)
    r = client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 401 and code(r) == "SESSION_EXPIRED"


def test_admin_session_lasts_at_most_7_days(clock):
    add_user("super@auth.test", role="superadmin", password=STRONG)
    r = login("super@auth.test", STRONG)
    token, t0 = r.json()["access_token"], clock["now"]
    assert r.json()["expires_at"].startswith((t0 + datetime.timedelta(days=7)).isoformat())
    clock["now"] = t0 + datetime.timedelta(days=6, hours=23)
    assert client.get("/auth/me", headers=bearer(token)).status_code == 200
    clock["now"] = t0 + datetime.timedelta(days=7, seconds=1)
    assert code(client.get("/auth/me", headers=bearer(token))) == "SESSION_EXPIRED"


def test_role_downgrade_takes_effect_on_existing_session(clock):
    uid = add_user("super@auth.test", role="superadmin", password=STRONG)
    token, t0 = token_for("super@auth.test"), clock["now"]
    set_user(uid, role="vendedor")
    me = client.get("/auth/me", headers=bearer(token)).json()
    assert me["user"]["role"] == "vendedor" and me["allowed_branch_ids"] == [1]
    clock["now"] = t0 + datetime.timedelta(hours=13)               # ya no tiene 7 días: tiene 12 h
    assert code(client.get("/auth/me", headers=bearer(token))) == "SESSION_EXPIRED"


def test_permission_changes_are_read_on_every_request():
    uid = add_user("perm@auth.test", password=STRONG)
    token = token_for("perm@auth.test")
    assert client.get("/auth/me", headers=bearer(token)).json()["user"]["can_caja"] is False
    assert client.patch(f"/users/{uid}/permissions", json={"can_caja": True, "branch_id": 2}).status_code == 200
    me = client.get("/auth/me", headers=bearer(token)).json()
    assert me["user"]["can_caja"] is True and me["allowed_branch_ids"] == [2]


def test_admins_see_all_active_branches():
    add_user("super@auth.test", role="superadmin", password=STRONG)
    assert login("super@auth.test", STRONG).json()["allowed_branch_ids"] == [1, 2]


# --- Cambio de contraseña --------------------------------------------------------------------------

def test_password_change_revokes_other_sessions_and_is_audited():
    uid = add_user("cambio@auth.test", password=STRONG)
    s1, s2 = token_for("cambio@auth.test"), token_for("cambio@auth.test")
    r = client.post("/auth/change-password", headers=bearer(s1),
                    json={"current_password": STRONG, "new_password": OTHER})
    assert r.status_code == 200 and r.json()["other_sessions_revoked"] == 1
    assert client.get("/auth/me", headers=bearer(s1)).status_code == 200
    assert code(client.get("/auth/me", headers=bearer(s2))) == "SESSION_REVOKED"
    assert login("cambio@auth.test", STRONG).status_code == 400
    assert login("cambio@auth.test", OTHER).status_code == 200
    audit = query("SELECT actor, actor_user_id, actor_source, session_id, old_value, new_value FROM audit_events "
                  "WHERE entity_type = 'user' AND entity_id = ? AND field_name = 'password'", (uid,))
    assert len(audit) == 1
    actor, actor_user_id, source, session_id, old, new = audit[0]
    assert (actor, actor_user_id, source, old, new) == ("cambio@auth.test", uid, "session", None, None)
    assert session_id is not None


def test_password_change_validations():
    add_user("cambio@auth.test", password=STRONG)
    token = token_for("cambio@auth.test")
    r = client.post("/auth/change-password", headers=bearer(token),
                    json={"current_password": OTHER, "new_password": "otra frase larga"})
    assert r.status_code == 400 and code(r) == "INVALID_CREDENTIALS"
    assert len(query("SELECT 1 FROM auth_login_failures")) == 1
    for new, policy in (("cambio@auth.test", "PASSWORD_EQUALS_EMAIL"), ("corta", "PASSWORD_TOO_SHORT"),
                        ("1234567890", "PASSWORD_COMMON"), (STRONG, "PASSWORD_UNCHANGED")):
        r = client.post("/auth/change-password", headers=bearer(token),
                        json={"current_password": STRONG, "new_password": new})
        assert r.status_code == 422 and r.headers["X-Policy-Code"] == policy
    assert client.post("/auth/change-password", json={"current_password": STRONG,
                                                      "new_password": OTHER}).status_code == 401


def test_rehash_on_login_when_iterations_increase(monkeypatch):
    uid = add_user("rehash@auth.test", password=STRONG)                 # 1000 iteraciones (fixture)
    monkeypatch.setattr(auth_core, "PBKDF2_ITERATIONS", 600_000)
    assert login("rehash@auth.test", STRONG).status_code == 200
    stored = query("SELECT password_hash FROM user_credentials WHERE user_id = ?", (uid,))[0][0]
    assert stored.startswith("pbkdf2_sha256$600000$")


# --- Alta de usuarios con hash ---------------------------------------------------------------------

def test_new_users_are_stored_with_hash_only_and_must_change():
    r = client.post("/users", json={"name": "Nuevo", "email": "nuevo@auth.test", "password": STRONG})
    assert r.status_code == 200
    uid = r.json()["id"]
    legacy = query("SELECT hashed_password FROM users WHERE id = ?", (uid,))[0][0]
    assert legacy.startswith("!unusable:") and STRONG not in legacy
    cred = query("SELECT password_hash, must_change_password, change_reason FROM user_credentials "
                 "WHERE user_id = ?", (uid,))[0]
    assert auth_core.verify_password(STRONG, cred[0]) and cred[1:] == (1, "INITIAL")
    r = client.post("/users", json={"name": "Corta", "email": "corta@auth.test", "password": "corta"})
    assert r.status_code == 422 and r.headers["X-Policy-Code"] == "PASSWORD_TOO_SHORT"


# --- Ningún secreto en respuestas -------------------------------------------------------------------

def test_no_secrets_in_any_auth_or_user_response():
    uid = add_user("secreto@auth.test", password=STRONG)
    responses = []
    r = login("secreto@auth.test", STRONG)
    responses.append(r)
    token = r.json()["access_token"]
    responses += [
        client.get("/auth/me", headers=bearer(token)),
        client.post("/auth/change-password", headers=bearer(token),
                    json={"current_password": STRONG, "new_password": OTHER}),
        client.get("/users"),
        client.post("/users", json={"name": "Otro", "email": "otro@auth.test", "password": "clave del alta 01"}),
        client.patch(f"/users/{uid}/permissions", json={"can_kpis": True}),
        login("secreto@auth.test", "incorrecta incorrecta"),
    ]
    secrets_in_db = [row[0] for row in query("SELECT password_hash FROM user_credentials")]
    secrets_in_db += [row[0] for row in query("SELECT token_hash FROM auth_sessions")]
    for resp in responses:
        text = resp.text
        for needle in (STRONG, OTHER, "clave del alta 01", "pbkdf2_sha256$", "!unusable:",
                       "password_hash", "token_hash", "hashed_password", "salt"):
            assert needle not in text, (resp.request.url, needle)
        for secret in secrets_in_db:
            assert secret not in text
    me = responses[1]
    assert token not in me.text and "access_token" not in me.json()


def test_audit_events_have_verified_actor_columns():
    cols = {r[1] for r in query("PRAGMA table_info(audit_events)")}
    assert {"actor_user_id", "actor_source", "session_id"} <= cols


def test_docs_are_available_outside_production():
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
