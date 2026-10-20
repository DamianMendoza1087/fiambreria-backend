"""Fase 2.1 — núcleo de credenciales (auth_core): hash, política, tokens, IP detrás de proxies,
roles e invariante de MASTERADMIN. No importa main.py ni usa base de datos."""
import base64
import hashlib
import os
import sys

import pytest

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_DIR)

import auth_core  # noqa: E402

PASSWORD = "una frase bastante larga"


# --- Hash -----------------------------------------------------------------------------------------

def test_hash_format_uses_pbkdf2_sha256_600000_and_is_not_reversible():
    encoded = auth_core.hash_password(PASSWORD)
    algorithm, iterations, salt, digest = encoded.split("$")
    assert algorithm == "pbkdf2_sha256" and int(iterations) == 600_000
    assert len(base64.b64decode(salt)) == 16 and len(base64.b64decode(digest)) == 32
    assert PASSWORD not in encoded and PASSWORD.encode().hex() not in encoded
    # El hash es el PBKDF2 estándar: se puede recalcular con la sal, no invertir.
    expected = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), base64.b64decode(salt), 600_000)
    assert base64.b64decode(digest) == expected


def test_same_password_produces_different_salts_and_hashes():
    a, b = auth_core.hash_password(PASSWORD), auth_core.hash_password(PASSWORD)
    assert a != b
    assert a.split("$")[2] != b.split("$")[2]


def test_verify_correct_and_incorrect():
    encoded = auth_core.hash_password(PASSWORD)
    assert auth_core.verify_password(PASSWORD, encoded) is True
    assert auth_core.verify_password(PASSWORD + "x", encoded) is False
    assert auth_core.verify_password("", encoded) is False
    assert auth_core.verify_password(PASSWORD, None) is False
    assert auth_core.verify_password(PASSWORD, "texto-plano") is False
    assert auth_core.verify_password(PASSWORD, "md5$1$a$b") is False


def test_unicode_is_normalized():
    composed, decomposed = "contraseña larga ñandú", "contraseña larga ñandú"
    assert auth_core.verify_password(decomposed, auth_core.hash_password(composed))


def test_needs_rehash_when_iterations_are_lower():
    assert auth_core.needs_rehash(auth_core.hash_password(PASSWORD, iterations=1000)) is True
    assert auth_core.needs_rehash(auth_core.hash_password(PASSWORD)) is False
    assert auth_core.needs_rehash("basura") is True


def test_hash_rejects_empty_password():
    with pytest.raises(ValueError):
        auth_core.hash_password("")


# --- Columna heredada -----------------------------------------------------------------------------

def test_unusable_legacy_value_never_verifies():
    value = auth_core.unusable_legacy_value()
    assert value.startswith("!unusable:") and value != auth_core.unusable_legacy_value()
    assert auth_core.is_unusable_legacy(value)
    assert auth_core.verify_legacy_plaintext(value, value) is False


def test_legacy_plaintext_comparison():
    assert auth_core.verify_legacy_plaintext("admin123", "admin123") is True
    assert auth_core.verify_legacy_plaintext("admin1234", "admin123") is False
    assert auth_core.verify_legacy_plaintext("", "") is False
    assert auth_core.legacy_change_reason("admin123") == "DEFAULT_PASSWORD"
    assert auth_core.legacy_change_reason("otra-clave") == "LEGACY_PLAINTEXT"


# --- Política de contraseñas (U20=A) --------------------------------------------------------------

@pytest.mark.parametrize("password,code", [
    ("admin123", "PASSWORD_DEFAULT"),
    ("ADMIN123", "PASSWORD_DEFAULT"),
    ("admin1234", "PASSWORD_DEFAULT"),
    ("admin123admin", "PASSWORD_DEFAULT"),
    ("corta", "PASSWORD_TOO_SHORT"),
    ("123456789", "PASSWORD_TOO_SHORT"),
    ("x" * 129, "PASSWORD_TOO_LONG"),
    ("1234567890", "PASSWORD_COMMON"),
    ("Password123", "PASSWORD_COMMON"),
    ("contraseña123", "PASSWORD_COMMON"),
    ("fiambreria123", "PASSWORD_COMMON"),
    ("zzzzzzzzzzzz", "PASSWORD_COMMON"),
    ("empleado@fiambreria.com", "PASSWORD_EQUALS_EMAIL"),
    ("EMPLEADO@Fiambreria.com", "PASSWORD_EQUALS_EMAIL"),
    ("empleado.mostrador", "PASSWORD_EQUALS_EMAIL"),
    (None, "PASSWORD_INVALID"),
])
def test_policy_rejections(password, code):
    email = "empleado.mostrador@fiambreria.com" if password == "empleado.mostrador" else "empleado@fiambreria.com"
    violation = auth_core.check_password_policy(password, email)
    assert violation is not None and violation.code == code
    if isinstance(password, str) and len(password) > 3:
        assert password not in violation.message


@pytest.mark.parametrize("password", [
    "salame tandilero",          # sin mayúsculas, números ni símbolos: alcanza la longitud
    "quesoyvino2026",
    "Una frase con espacios",
    ("queso y vino " * 10)[:128],
])
def test_policy_accepts_long_passwords_without_composition_rules(password):
    assert auth_core.check_password_policy(password, "empleado@fiambreria.com") is None


def test_policy_rejects_unchanged_password():
    v = auth_core.check_password_policy("salame tandilero", "a@b.c", current_password="salame tandilero")
    assert v.code == "PASSWORD_UNCHANGED"


def test_temporary_password_is_random_and_meets_policy():
    values = {auth_core.generate_temporary_password() for _ in range(20)}
    assert len(values) == 20
    assert all(auth_core.check_password_policy(v) is None and len(v) >= 16 for v in values)


# --- Tokens ---------------------------------------------------------------------------------------

def test_session_tokens_are_random_and_only_digest_is_stored():
    t1, t2 = auth_core.new_session_token(), auth_core.new_session_token()
    assert t1 != t2 and len(t1) >= 40
    digest = auth_core.token_digest(t1)
    assert len(digest) == 64 and t1 not in digest and digest == hashlib.sha256(t1.encode()).hexdigest()


# --- IP del cliente (U18=B) -----------------------------------------------------------------------

def test_without_trusted_proxies_forwarded_for_is_ignored():
    addr = auth_core.resolve_client_address("10.0.0.5", "1.2.3.4, 5.6.7.8", trusted_hops=0)
    assert addr.ip == "10.0.0.5" and addr.source == "peer"
    assert addr.forwarded_chain == ["1.2.3.4", "5.6.7.8"]


def test_trusted_hops_take_entry_added_by_outermost_trusted_proxy():
    # El cliente inventa 9.9.9.9; el proxy de confianza agrega la IP real 200.1.1.1 al final.
    addr = auth_core.resolve_client_address("10.0.0.5", "9.9.9.9, 200.1.1.1", trusted_hops=1)
    assert addr.ip == "200.1.1.1" and addr.source == "forwarded"
    addr = auth_core.resolve_client_address("10.0.0.5", "9.9.9.9, 200.1.1.1, 172.16.0.1", trusted_hops=2)
    assert addr.ip == "200.1.1.1"


def test_spoofed_or_missing_forwarded_for_falls_back_to_peer():
    addr = auth_core.resolve_client_address("10.0.0.5", None, trusted_hops=1)
    assert addr.ip == "10.0.0.5" and addr.source == "peer_fallback"
    addr = auth_core.resolve_client_address("10.0.0.5", "no-es-una-ip", trusted_hops=1)
    assert addr.ip == "10.0.0.5" and addr.forwarded_chain == ["invalid"]
    addr = auth_core.resolve_client_address("10.0.0.5", "1.1.1.1", trusted_hops=2)
    assert addr.ip == "10.0.0.5" and addr.source == "peer_fallback"


def test_forwarded_chain_is_bounded():
    header = ", ".join(f"10.0.0.{i}" for i in range(50))
    assert len(auth_core.parse_forwarded_for(header)) == auth_core.MAX_FORWARDED_ENTRIES


# --- Roles y MASTERADMIN --------------------------------------------------------------------------

def test_session_duration_by_role():
    assert auth_core.session_max_hours("masteradmin") == 168
    assert auth_core.session_max_hours("superadmin") == 168
    for role in ("dueno", "encargado", "cajero", "vendedor", "desconocido", None):
        assert auth_core.session_max_hours(role) == 12


@pytest.mark.parametrize("actor,target,new_role,allowed", [
    ("masteradmin", "superadmin", "vendedor", True),       # MASTERADMIN degrada SUPERADMIN
    ("masteradmin", "vendedor", "masteradmin", True),
    ("superadmin", "vendedor", "masteradmin", False),       # SUPERADMIN nunca crea MASTERADMIN
    ("superadmin", "vendedor", "superadmin", False),
    ("superadmin", "masteradmin", "vendedor", False),
    ("superadmin", "superadmin", "dueno", False),
    ("superadmin", "vendedor", "dueno", True),
    ("dueno", "vendedor", "cajero", True),
    ("dueno", "vendedor", "dueno", False),
    ("dueno", "dueno", "vendedor", False),
    ("encargado", "vendedor", "cajero", True),
    ("cajero", "vendedor", "cajero", False),
    ("masteradmin", "vendedor", "inexistente", False),
])
def test_role_assignment_rules(actor, target, new_role, allowed):
    reason = auth_core.can_assign_role(actor, actor_id=1, target_id=2, target_current_role=target,
                                       new_role=new_role)
    assert (reason is None) is allowed


def test_nobody_changes_own_role():
    assert auth_core.can_assign_role("masteradmin", 1, 1, "masteradmin", "superadmin") is not None
    assert auth_core.can_assign_role("superadmin", 5, 5, "superadmin", "masteradmin") is not None


def test_last_masteradmin_invariant():
    assert auth_core.violates_last_masteradmin({1}, 1, "superadmin", True) is True
    assert auth_core.violates_last_masteradmin({1}, 1, "masteradmin", False) is True
    assert auth_core.violates_last_masteradmin({1, 2}, 1, "vendedor", True) is False
    assert auth_core.violates_last_masteradmin({1}, 3, "vendedor", True) is False
    assert auth_core.violates_last_masteradmin(set(), 3, "masteradmin", True) is False
