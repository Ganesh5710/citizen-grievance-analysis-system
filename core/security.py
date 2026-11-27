"""
Cryptographic, temporary-password, and input sanitization helpers (no database dependencies).
"""

import hashlib
import hmac
import os
import re
import secrets
import string

PBKDF2_ITERATIONS = 260_000
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def hash_password(password: str) -> tuple[str, str]:
    """Return (password_hash_hex, salt_hex) using PBKDF2-HMAC-SHA256."""
    salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    """Constant-time verification of a password against stored hex hash and salt."""
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), password_hash)


def generate_temp_password(length: int = 12) -> str:
    """Generate a cryptographically random temporary password containing upper, lower, digit, and symbol."""
    if length < 8:
        length = 8
    upper = secrets.choice(string.ascii_uppercase)
    lower = secrets.choice(string.ascii_lowercase)
    digit = secrets.choice(string.digits)
    symbol = secrets.choice("!@#$%^&*")
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    rest = [secrets.choice(alphabet) for _ in range(length - 4)]
    chars = [upper, lower, digit, symbol, *rest]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def sanitize_text(value: str | None, max_length: int = 2000, field_name: str = "Input") -> str:
    """Strip surrounding whitespace and unsafe ASCII control characters, enforcing max_length."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text.")
    cleaned = _CONTROL_CHARS_RE.sub("", value).strip()
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} must not exceed {max_length} characters.")
    return cleaned
