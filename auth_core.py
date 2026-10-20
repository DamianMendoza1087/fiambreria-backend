"""Núcleo de credenciales y sesiones (Fase 2.1). Solo biblioteca estándar; no importa main.py.

- Contraseñas: PBKDF2-HMAC-SHA256, 600.000 iteraciones, sal aleatoria de 16 bytes.
  Formato: pbkdf2_sha256$<iteraciones>$<sal b64>$<hash b64>.
- Tokens de sesión: aleatorios (secrets); en la base se guarda solo su SHA-256.
- Política de contraseñas (U20): 10 a 128 caracteres, sin admin123, sin el email ni su parte
  local, sin contraseñas comunes. Sin reglas de mayúscula/número/símbolo.
- Roles (U2=A) y reglas de asignación (U4=A) para las fases siguientes.

Nada en este módulo escribe en logs ni devuelve secretos en mensajes de error.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional

PBKDF2_ALGORITHM = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
DEFAULT_MASTER_PASSWORD = "admin123"

# Valor que se guarda en la columna heredada users.hashed_password cuando la contraseña NO debe
# guardarse en texto plano. Es aleatorio por usuario: nadie puede escribirlo para iniciar sesión.
UNUSABLE_LEGACY_PREFIX = "!unusable:"

# Motivos por los que una credencial exige cambio.
REASON_DEFAULT_PASSWORD = "DEFAULT_PASSWORD"      # admin123
REASON_LEGACY_PLAINTEXT = "LEGACY_PLAINTEXT"      # clave heredada en texto plano (expuesta antes de U1)
REASON_TEMPORARY = "TEMPORARY"                    # clave temporal de un reset
REASON_INITIAL = "INITIAL"                        # clave asignada al crear el usuario

# ---------------------------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------------------------

ROLE_LEVELS = {
    "masteradmin": 100,
    "superadmin": 80,
    "dueno": 60,
    "encargado": 40,
    "cajero": 30,
    "vendedor": 20,   # se muestra como EMPLEADO_MOSTRADOR
}
ADMIN_ROLES = ("masteradmin", "superadmin")
EMPLOYEE_SESSION_HOURS = 12
ADMIN_SESSION_HOURS = 7 * 24


def role_level(role: Optional[str]) -> int:
    return ROLE_LEVELS.get((role or "").strip().lower(), 0)


def session_max_hours(role: Optional[str]) -> int:
    """U9=B: MASTERADMIN/SUPERADMIN 7 días; el resto 12 horas (también roles desconocidos)."""
    return ADMIN_SESSION_HOURS if (role or "").strip().lower() in ADMIN_ROLES else EMPLOYEE_SESSION_HOURS


def can_assign_role(actor_role: str, actor_id: int, target_id: Optional[int],
                    target_current_role: Optional[str], new_role: str) -> Optional[str]:
    """Devuelve None si el cambio está permitido o un texto con el motivo del rechazo (U4=A).

    - Nadie cambia su propio rol.
    - Solo MASTERADMIN asigna MASTERADMIN o SUPERADMIN, o modifica usuarios con esos roles.
    - El resto asigna únicamente roles menores al propio y sobre usuarios de nivel menor.
    """
    actor_role = (actor_role or "").strip().lower()
    new_role = (new_role or "").strip().lower()
    current = (target_current_role or "").strip().lower()
    if new_role not in ROLE_LEVELS:
        return "Rol inválido"
    if target_id is not None and target_id == actor_id:
        return "Nadie puede cambiar su propio rol"
    if actor_role == "masteradmin":
        return None
    if new_role in ADMIN_ROLES or current in ADMIN_ROLES:
        return "Solo un MASTERADMIN puede asignar o modificar roles MASTERADMIN y SUPERADMIN"
    if role_level(new_role) >= role_level(actor_role):
        return "No se puede asignar un rol igual o superior al propio"
    if current and role_level(current) >= role_level(actor_role):
        return "No se puede modificar a un usuario de nivel igual o superior"
    return None


def violates_last_masteradmin(active_masteradmin_ids: Iterable[int], target_id: int,
                              target_role_after: Optional[str], target_active_after: bool) -> bool:
    """True si el cambio dejaría el sistema sin ningún MASTERADMIN activo (U5=A, mínimo 1)."""
    remaining = {i for i in active_masteradmin_ids if i != target_id}
    if (target_role_after or "").strip().lower() == "masteradmin" and target_active_after:
        remaining.add(target_id)
    return len(remaining) < 1


# ---------------------------------------------------------------------------------------------
# Hash de contraseñas
# ---------------------------------------------------------------------------------------------

def _normalize(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, iterations: Optional[int] = None) -> str:
    if not isinstance(password, str) or password == "":
        raise ValueError("La contraseña no puede estar vacía")
    iterations = PBKDF2_ITERATIONS if iterations is None else int(iterations)
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", _normalize(password), salt, iterations)
    return f"{PBKDF2_ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def _parse(encoded: str):
    try:
        algorithm, iterations, salt, digest = encoded.split("$")
        if algorithm != PBKDF2_ALGORITHM:
            return None
        iterations = int(iterations)
        if iterations < 1:
            return None
        return iterations, base64.b64decode(salt), base64.b64decode(digest)
    except (ValueError, TypeError, AttributeError):
        return None


def verify_password(password: str, encoded: Optional[str]) -> bool:
    if not isinstance(password, str) or not encoded:
        return False
    parsed = _parse(encoded)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", _normalize(password), salt, iterations)
    return hmac.compare_digest(actual, expected)


def needs_rehash(encoded: Optional[str]) -> bool:
    parsed = _parse(encoded or "")
    return parsed is None or parsed[0] < PBKDF2_ITERATIONS


_DUMMY_HASHES = {}


def dummy_verify(password: str) -> None:
    """Gasta el mismo costo que una verificación real (usuario inexistente o clave heredada)."""
    key = PBKDF2_ITERATIONS
    if key not in _DUMMY_HASHES:
        _DUMMY_HASHES[key] = hash_password(secrets.token_urlsafe(16))
    verify_password(password if isinstance(password, str) else "", _DUMMY_HASHES[key])


# ---------------------------------------------------------------------------------------------
# Columna heredada en texto plano
# ---------------------------------------------------------------------------------------------

def unusable_legacy_value() -> str:
    return UNUSABLE_LEGACY_PREFIX + secrets.token_urlsafe(32)


def is_unusable_legacy(value: Optional[str]) -> bool:
    return not value or str(value).startswith(UNUSABLE_LEGACY_PREFIX)


def verify_legacy_plaintext(password: str, stored: Optional[str]) -> bool:
    """Comparación en tiempo constante contra la contraseña heredada (solo usuarios sin credencial)."""
    if not isinstance(password, str) or is_unusable_legacy(stored):
        return False
    return hmac.compare_digest(password.encode("utf-8"), str(stored).encode("utf-8"))


def legacy_change_reason(plaintext: Optional[str]) -> str:
    return REASON_DEFAULT_PASSWORD if plaintext == DEFAULT_MASTER_PASSWORD else REASON_LEGACY_PLAINTEXT


# ---------------------------------------------------------------------------------------------
# Política de contraseñas (U20=A)
# ---------------------------------------------------------------------------------------------

# Claves comunes de 10 o más caracteres (las más cortas ya las rechaza el mínimo), más variantes
# obvias del negocio. Se comparan en minúsculas y sin espacios en los extremos.
COMMON_PASSWORDS = frozenset(p.lower() for p in (
    "1234567890", "12345678910", "123456789012", "0123456789", "0987654321", "1234554321",
    "1111111111", "0000000000", "9999999999", "1212121212", "1234512345", "1122334455",
    "qwertyuiop", "qwerty1234", "qwerty12345", "qwerty123456", "asdfghjkl1", "asdfghjklñ",
    "zxcvbnm123", "1q2w3e4r5t", "1qaz2wsx3edc", "qazwsxedc1", "abcdefghij", "abc1234567",
    "password12", "password123", "password1234", "passw0rd12", "contraseña", "contrasena",
    "contraseña1", "contrasena1", "contraseña123", "contrasena123", "micontraseña", "micontrasena",
    "administrador", "administrator", "admin12345", "admin123456", "adminadmin", "admin1234567",
    "superadmin", "superadmin1", "masteradmin", "masteradmin1", "iloveyou12", "iloveyou123",
    "teamo12345", "teamo123456", "bienvenido", "bienvenido1", "bienvenido123", "welcome123",
    "argentina1", "argentina10", "argentina123", "bocajuniors", "riverplate", "maradona10",
    "messi10messi", "fiambreria", "fiambreria1", "fiambreria123", "fiambrerialocal",
    "feriadamyale", "damyale123", "damyale2026", "mercadopago", "cambiarclave", "cambiame123",
    "changeme123", "letmein123", "football123", "baseball123", "sunshine123", "princess123",
    "dragon1234", "monkey12345", "trustno1234", "aaaaaaaaaa", "abcabcabca",
))


@dataclass
class PolicyViolation:
    code: str
    message: str


def check_password_policy(password, email: Optional[str] = None,
                          current_password: Optional[str] = None) -> Optional[PolicyViolation]:
    """None si la contraseña nueva es aceptable; si no, el motivo (sin repetir la contraseña)."""
    if not isinstance(password, str):
        return PolicyViolation("PASSWORD_INVALID", "La contraseña es obligatoria.")
    normalized = unicodedata.normalize("NFKC", password)
    folded = normalized.strip().casefold()
    # admin123 y variantes cortas que la contienen (admin1234, admin123admin, etc.).
    compact = "".join(folded.split())
    if DEFAULT_MASTER_PASSWORD in compact and len(compact) < 16:
        return PolicyViolation("PASSWORD_DEFAULT", "No se puede usar la contraseña por defecto.")
    if len(normalized) < MIN_PASSWORD_LENGTH:
        return PolicyViolation("PASSWORD_TOO_SHORT",
                               f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    if len(normalized) > MAX_PASSWORD_LENGTH:
        return PolicyViolation("PASSWORD_TOO_LONG",
                               f"La contraseña no puede superar {MAX_PASSWORD_LENGTH} caracteres.")
    if email:
        email_folded = email.strip().casefold()
        local = email_folded.split("@", 1)[0]
        if folded in (email_folded, local):
            return PolicyViolation("PASSWORD_EQUALS_EMAIL", "La contraseña no puede ser igual al email.")
    if folded in COMMON_PASSWORDS or len(set(folded)) == 1:
        return PolicyViolation("PASSWORD_COMMON", "Esa contraseña es demasiado común. Elegí otra.")
    if current_password is not None and normalized == unicodedata.normalize("NFKC", current_password):
        return PolicyViolation("PASSWORD_UNCHANGED", "La contraseña nueva debe ser distinta de la actual.")
    return None


def generate_temporary_password() -> str:
    """Clave temporal de 20 caracteres, aleatoria; siempre cumple la política."""
    while True:
        candidate = secrets.token_urlsafe(15)
        if check_password_policy(candidate) is None:
            return candidate


# ---------------------------------------------------------------------------------------------
# Tokens de sesión
# ---------------------------------------------------------------------------------------------

def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------
# IP del cliente detrás de proxies (U18=B)
# ---------------------------------------------------------------------------------------------

MAX_TRUSTED_PROXY_HOPS = 5
MAX_FORWARDED_ENTRIES = 10


def _valid_ip(value: str) -> Optional[str]:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def parse_forwarded_for(header_value: Optional[str]) -> List[str]:
    """Entradas de X-Forwarded-For validadas como IP (las inválidas quedan como 'invalid')."""
    if not header_value:
        return []
    entries = [e for e in header_value.split(",") if e.strip()][-MAX_FORWARDED_ENTRIES:]
    return [_valid_ip(e) or "invalid" for e in entries]


@dataclass
class ClientAddress:
    ip: Optional[str]
    peer: Optional[str]
    forwarded_chain: List[str]
    source: str           # peer | forwarded | peer_fallback


def resolve_client_address(peer: Optional[str], forwarded_header: Optional[str],
                           trusted_hops: int) -> ClientAddress:
    """IP del cliente considerando solo proxies explícitamente confiables.

    Cada proxy confiable agrega al final de X-Forwarded-For la dirección desde la que recibió la
    conexión. Con N proxies confiables, la IP del cliente es la entrada N-ésima desde el final;
    todo lo que está a su izquierda lo pudo escribir el cliente y se ignora. Con N=0 (por
    defecto) X-Forwarded-For se ignora por completo.
    """
    chain = parse_forwarded_for(forwarded_header)
    if trusted_hops <= 0:
        return ClientAddress(peer, peer, chain, "peer")
    if len(chain) >= trusted_hops and chain[-trusted_hops] != "invalid":
        return ClientAddress(chain[-trusted_hops], peer, chain, "forwarded")
    return ClientAddress(peer, peer, chain, "peer_fallback")


def chain_json(chain: List[str]) -> Optional[str]:
    return json.dumps(chain[-5:]) if chain else None
