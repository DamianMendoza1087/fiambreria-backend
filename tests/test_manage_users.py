"""Fase 2.1 — script de Render Shell (manage_users.py) sobre la base temporal de los tests."""
import datetime
import sqlite3

import pytest

from test_day_plans_api import _DB_PATH, client, main  # noqa: E402
import auth_core  # noqa: E402
import manage_users  # noqa: E402

STRONG = "salame tandilero 2026"


def wipe():
    conn = sqlite3.connect(_DB_PATH)
    ids = [r[0] for r in conn.execute("SELECT id FROM users WHERE email LIKE '%@mu.test'")]
    conn.execute("DELETE FROM auth_login_failures")
    for uid in ids:
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM user_branches WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM user_credentials WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM audit_events WHERE entity_type = 'user' AND entity_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE email LIKE '%@mu.test'")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(auth_core, "PBKDF2_ITERATIONS", 1000)
    wipe()
    yield
    wipe()


def add_user(email, role="vendedor", active=True):
    db = main.SessionLocal()
    u = main.UserDB(name="x", email=email, role=role, is_active=active,
                    hashed_password=auth_core.unusable_legacy_value())
    db.add(u)
    db.flush()
    main.set_user_password(db, u, STRONG, False, None, datetime.datetime.utcnow())
    db.commit()
    uid = u.id
    db.close()
    return uid


def login(email, password):
    return client.post("/login", data={"username": email, "password": password})


def reset(email, activate=False):
    db = main.SessionLocal()
    try:
        user, temporary = manage_users.reset_password(db, main, email, activate=activate)
        return user.id, temporary
    finally:
        db.close()


def test_reset_password_revokes_sessions_clears_lock_and_forces_change():
    uid = add_user("emp@mu.test")
    token = login("emp@mu.test", STRONG).json()["access_token"]
    for _ in range(5):
        login("emp@mu.test", "incorrecta incorrecta")
    assert login("emp@mu.test", STRONG).status_code == 429

    _, temporary = reset("emp@mu.test")
    assert auth_core.check_password_policy(temporary) is None
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.headers["X-Error-Code"] == "SESSION_REVOKED"
    assert login("emp@mu.test", STRONG).status_code == 400
    r = login("emp@mu.test", temporary)
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True and r.json()["password_change_reason"] == "TEMPORARY"
    conn = sqlite3.connect(_DB_PATH)
    stored = conn.execute("SELECT password_hash FROM user_credentials WHERE user_id = ?", (uid,)).fetchone()[0]
    audit = conn.execute("SELECT actor, actor_source, old_value, new_value FROM audit_events "
                         "WHERE entity_type='user' AND entity_id=? AND field_name='password'", (uid,)).fetchall()
    conn.close()
    assert temporary not in stored and audit == [("render-shell", "cli", None, None)]


def test_activate_only_for_masteradmin():
    add_user("baja@mu.test", active=False)
    with pytest.raises(SystemExit):
        reset("baja@mu.test", activate=True)
    uid = add_user("maestro@mu.test", role="masteradmin", active=False)
    reset("maestro@mu.test", activate=True)
    conn = sqlite3.connect(_DB_PATH)
    assert conn.execute("SELECT is_active FROM users WHERE id = ?", (uid,)).fetchone()[0] == 1
    conn.close()


def test_unknown_email_is_rejected():
    with pytest.raises(SystemExit):
        reset("nadie@mu.test")


def test_unlock_removes_failures():
    add_user("emp@mu.test")
    for _ in range(5):
        login("emp@mu.test", "incorrecta incorrecta")
    db = main.SessionLocal()
    try:
        assert manage_users.unlock(db, main, "EMP@mu.test") == 5
    finally:
        db.close()
    assert login("emp@mu.test", STRONG).status_code == 200


def test_list_admins_has_no_secrets():
    add_user("super@mu.test", role="superadmin")
    db = main.SessionLocal()
    try:
        rows = manage_users.list_admins(db, main)
    finally:
        db.close()
    assert any(r["email"] == "super@mu.test" and r["has_credential"] for r in rows)
    assert all(set(r) == {"id", "email", "role", "is_active", "has_credential", "must_change_password"}
               for r in rows)


def test_cli_cancels_without_confirmation(monkeypatch, capsys):
    add_user("emp@mu.test")
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    assert manage_users.main_cli(["reset-password", "--email", "emp@mu.test"]) == 1
    assert "Cancelado" in capsys.readouterr().out
    assert login("emp@mu.test", STRONG).status_code == 200
