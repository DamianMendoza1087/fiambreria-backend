"""Administración de credenciales desde el Shell de Render (Fase 2.1).

Quien accede al Shell del servicio en Render es la raíz de confianza: con este script se
recupera el acceso del MASTERADMIN sin email ni servicios externos.

    python manage_users.py list-admins
    python manage_users.py reset-password --email admin@fiambreria.com
    python manage_users.py reset-password --email admin@fiambreria.com --activate
    python manage_users.py unlock --email admin@fiambreria.com
    python manage_users.py migrate-credentials --dry-run
    python manage_users.py migrate-credentials

- reset-password genera una contraseña temporal aleatoria, la muestra UNA sola vez en esta
  terminal, guarda solo su hash, obliga a cambiarla en el próximo ingreso, cierra todas las
  sesiones del usuario y limpia su bloqueo por intentos. Nunca escribe la contraseña en logs ni
  en la base en texto plano. No toca la columna heredada users.hashed_password (U8=A).
- migrate-credentials convierte en hash la contraseña heredada de los usuarios que todavía no
  tienen credencial (exigiendo cambio). Es idempotente y no borra el texto heredado.

Hacer un backup antes (python backup_db.py --label pre-credenciales).
"""
import argparse
import sys

import auth_core


def _main_module():
    # Importar main aplica las mismas migraciones idempotentes que el arranque del servicio.
    import main
    return main


def list_admins(db, main):
    rows = db.query(main.UserDB).filter(main.UserDB.role.in_(auth_core.ADMIN_ROLES)).order_by(main.UserDB.id).all()
    out = []
    for u in rows:
        cred = main._credential(db, u.id)
        out.append({"id": u.id, "email": u.email, "role": u.role, "is_active": bool(u.is_active),
                    "has_credential": cred is not None,
                    "must_change_password": main._password_change_state(u, cred)[0]})
    return out


def reset_password(db, main, email: str, activate: bool = False, actor_label: str = "render-shell"):
    """Devuelve (usuario, contraseña temporal). La contraseña no se guarda en ningún lado."""
    user = main._find_user_by_email(db, email)
    if user is None:
        raise SystemExit("No existe un usuario con ese email.")
    if activate and user.role != "masteradmin":
        raise SystemExit("--activate solo se permite para recuperar una cuenta MASTERADMIN.")
    now = main._auth_utcnow()
    temporary = auth_core.generate_temporary_password()
    main.set_user_password(db, user, temporary, must_change=True, reason=auth_core.REASON_TEMPORARY, now=now)
    revoked = main.revoke_user_sessions(db, user.id, "PASSWORD_RESET", now)
    main._clear_login_failures(db, main._email_key(user.email))
    if activate and not user.is_active:
        user.is_active = True
        db.add(main.AuditEventDB(entity_type="user", entity_id=user.id, field_name="is_active",
                                 old_value="False", new_value="True", actor=actor_label,
                                 actor_source="cli", reason="Reactivación de MASTERADMIN desde Render Shell",
                                 created_at=now))
    db.add(main.AuditEventDB(entity_type="user", entity_id=user.id, field_name="password",
                             old_value=None, new_value=None, actor=actor_label, actor_source="cli",
                             reason=f"Contraseña temporal desde Render Shell; sesiones cerradas: {revoked}",
                             created_at=now))
    db.commit()
    return user, temporary


def unlock(db, main, email: str) -> int:
    removed = db.query(main.AuthLoginFailureDB).filter(
        main.AuthLoginFailureDB.email_key == main._email_key(email)).delete(synchronize_session=False)
    db.commit()
    return removed


def migrate_credentials(db, main, dry_run: bool = False) -> dict:
    """Crea credencial (hash) para usuarios sin credencial que tienen contraseña heredada utilizable."""
    now = main._auth_utcnow()
    summary = {"users": 0, "already_migrated": 0, "migrated": 0, "without_usable_password": 0}
    for user in db.query(main.UserDB).order_by(main.UserDB.id).all():
        summary["users"] += 1
        if main._credential(db, user.id) is not None:
            summary["already_migrated"] += 1
            continue
        if auth_core.is_unusable_legacy(user.hashed_password):
            summary["without_usable_password"] += 1
            continue
        summary["migrated"] += 1
        if dry_run:
            continue
        db.add(main.UserCredentialDB(
            user_id=user.id, password_hash=auth_core.hash_password(user.hashed_password),
            must_change_password=True, change_reason=auth_core.legacy_change_reason(user.hashed_password),
            created_at=now, updated_at=now))
    if not dry_run:
        db.commit()
    summary["dry_run"] = dry_run
    return summary


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{prompt} Escribí SI para continuar: ").strip().upper() == "SI"
    except EOFError:
        return False


def main_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Credenciales de usuarios (Render Shell).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-admins", help="MASTERADMIN y SUPERADMIN, sin secretos")
    p_reset = sub.add_parser("reset-password", help="Contraseña temporal de un solo uso")
    p_reset.add_argument("--email", required=True)
    p_reset.add_argument("--activate", action="store_true", help="Reactivar un MASTERADMIN deshabilitado")
    p_reset.add_argument("--yes", action="store_true", help="No pedir confirmación")
    p_unlock = sub.add_parser("unlock", help="Quitar el bloqueo por intentos fallidos de un email")
    p_unlock.add_argument("--email", required=True)
    p_migrate = sub.add_parser("migrate-credentials", help="Convertir contraseñas heredadas en hash")
    p_migrate.add_argument("--dry-run", action="store_true")
    p_migrate.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    main = _main_module()
    db = main.SessionLocal()
    try:
        if args.command == "list-admins":
            for row in list_admins(db, main):
                print(row)
            return 0
        if args.command == "unlock":
            print(f"Intentos fallidos eliminados: {unlock(db, main, args.email)}")
            return 0
        if args.command == "migrate-credentials":
            if not args.dry_run and not _confirm("Se crearán hashes para usuarios sin credencial.", args.yes):
                print("Cancelado.")
                return 1
            print(migrate_credentials(db, main, dry_run=args.dry_run))
            return 0
        if args.command == "reset-password":
            if not _confirm(f"Se generará una contraseña temporal para {args.email} y se cerrarán sus sesiones.",
                            args.yes):
                print("Cancelado.")
                return 1
            user, temporary = reset_password(db, main, args.email, activate=args.activate)
            print(f"Usuario: {user.email} ({user.role})")
            print("Contraseña temporal (se muestra una sola vez; debe cambiarse en el próximo ingreso):")
            print(temporary)
            return 0
    finally:
        db.close()
    return 1


if __name__ == "__main__":
    sys.exit(main_cli())
