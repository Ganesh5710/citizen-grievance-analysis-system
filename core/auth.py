"""
Authentication module: register, login, staff account creation, and user listing.
"""

from contextlib import closing
import re
import sqlite3

from core.database import get_connection
from core.permissions import require
from core.security import hash_password, sanitize_text, verify_password

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_STAFF_ROLES = {"employee", "admin", "super_admin"}


def _validate_registration_inputs(username: str, full_name: str, password: str) -> tuple[str, str]:
    """Validate and sanitize username, full_name, and password."""
    clean_username = sanitize_text(username, max_length=50, field_name="Username")
    clean_full_name = sanitize_text(full_name, max_length=100, field_name="Full name")

    if len(clean_username) < 3:
        raise ValueError("Username must be at least 3 characters long.")
    if not _USERNAME_RE.match(clean_username):
        raise ValueError("Username may only contain letters, digits, and underscores.")
    if len(clean_full_name) < 2:
        raise ValueError("Full name must be at least 2 characters long.")
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if len(password) > 128:
        raise ValueError("Password must not exceed 128 characters.")

    return clean_username, clean_full_name


def _row_to_user(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict (no password fields)."""
    return {
        "id":        row["id"],
        "username":  row["username"],
        "full_name": row["full_name"],
        "role":      row["role"],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def register(username: str, full_name: str, password: str) -> dict:
    """
    Public sign-up — always creates role='user'.
    Raises ValueError on validation failure or duplicate username.
    """
    clean_username, clean_full_name = _validate_registration_inputs(username, full_name, password)
    pw_hash, salt = hash_password(password)

    try:
        with closing(get_connection()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO users (username, full_name, password_hash, salt, role) "
                "VALUES (?, ?, ?, ?, 'user')",
                (clean_username, clean_full_name, pw_hash, salt),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{clean_username}' is already taken.")

    return {
        "id": user_id,
        "username": clean_username,
        "full_name": clean_full_name,
        "role": "user",
    }


def login(username: str, password: str) -> dict | None:
    """
    Return user dict {id, username, full_name, role} on success, None on failure.
    Never reveals whether username or password was wrong.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    clean_username = username.strip()
    if not clean_username or not password:
        return None

    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT id, username, full_name, password_hash, salt, role "
            "FROM users WHERE username = ?",
            (clean_username,),
        ).fetchone()

    if row is None:
        # Run PBKDF2 anyway to keep response timing uniform across missing/valid users.
        verify_password(password, "0" * 64, "00" * 32)
        return None

    if not verify_password(password, row["password_hash"], row["salt"]):
        return None

    return _row_to_user(row)


def create_staff_account(
    actor: dict | None,
    username: str,
    full_name: str,
    password: str,
    role: str,
) -> dict:
    """
    Only super_admin can call this. Enforced via require(actor, 'create_staff_account').
    Role must be one of: employee, admin, super_admin (not 'user').
    """
    require(actor, "create_staff_account")

    if role not in _STAFF_ROLES:
        raise ValueError(
            f"Invalid staff role '{role}'. Must be one of: {', '.join(sorted(_STAFF_ROLES))}."
        )

    clean_username, clean_full_name = _validate_registration_inputs(username, full_name, password)
    pw_hash, salt = hash_password(password)

    try:
        with closing(get_connection()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO users (username, full_name, password_hash, salt, role) "
                "VALUES (?, ?, ?, ?, ?)",
                (clean_username, clean_full_name, pw_hash, salt, role),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{clean_username}' is already taken.")

    return {
        "id": user_id,
        "username": clean_username,
        "full_name": clean_full_name,
        "role": role,
    }


def get_all_users(actor: dict | None) -> list[dict]:
    """
    Requires 'view_users' (super_admin only).
    Returns list of all users (id, username, full_name, role, created_at) without password fields.
    """
    require(actor, "view_users")

    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT id, username, full_name, role, created_at FROM users ORDER BY id"
        ).fetchall()

    return [
        {
            "id":         row["id"],
            "username":   row["username"],
            "full_name":  row["full_name"],
            "role":       row["role"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
