"""Ayuda para tests (Fase 2.2): sesiones reales emitidas directamente en la base TEMPORAL de tests.

No es un mecanismo de la aplicación: vive solo en tests/ y escribe en la base que cada test crea.
Las sesiones se guardan igual que las del login (solo el SHA-256 del token), y el usuario queda con
una credencial sin cambio pendiente para poder operar.
"""
import datetime
import secrets

import auth_core

ADMIN_EMAIL = "ci-master@tests.local"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def ensure_user(main, email, role="masteradmin", active=True, branch_ids=None, caps=None):
    """Crea el usuario si no existe (sin tocar uno existente salvo lo pedido) y le deja una
    credencial operativa. Devuelve el id."""
    db = main.SessionLocal()
    try:
        user = db.query(main.UserDB).filter(main.func.lower(main.UserDB.email) == email.strip().lower()).first()
        if user is None:
            user = main.UserDB(name=email.split("@")[0], email=email, role=role, is_active=active,
                               hashed_password=auth_core.unusable_legacy_value(),
                               branch_id=(branch_ids or [1])[0])
            db.add(user)
            db.flush()
        for cap, value in (caps or {}).items():
            setattr(user, cap, value)
        if branch_ids is not None:
            db.query(main.UserBranchDB).filter(main.UserBranchDB.user_id == user.id).delete()
            for branch_id in branch_ids:
                db.add(main.UserBranchDB(user_id=user.id, branch_id=branch_id))
            user.branch_id = branch_ids[0]
        cred = db.query(main.UserCredentialDB).filter(main.UserCredentialDB.user_id == user.id).first()
        if cred is None:
            db.add(main.UserCredentialDB(user_id=user.id,
                                         password_hash=auth_core.hash_password(secrets.token_urlsafe(16), 1000),
                                         must_change_password=False, created_at=_now(), updated_at=_now()))
        else:
            cred.must_change_password = False
            cred.change_reason = None
        db.commit()
        return user.id
    finally:
        db.close()


def token_for(main, user_id, created_at=None):
    token = auth_core.new_session_token()
    created = created_at or _now()
    db = main.SessionLocal()
    try:
        db.add(main.AuthSessionDB(token_hash=auth_core.token_digest(token), user_id=user_id, created_at=created,
                                  expires_at=created + datetime.timedelta(days=7), last_seen_at=created))
        db.commit()
    finally:
        db.close()
    return token


def headers_for(main, email, **user_kwargs):
    return {"Authorization": f"Bearer {token_for(main, ensure_user(main, email, **user_kwargs))}"}
