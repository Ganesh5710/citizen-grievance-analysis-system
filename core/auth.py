"""
Authentication & user management: register, login, forced password change,
and super_admin user lifecycle operations (including employee department binding).
"""

from contextlib import closing
import re
import sqlite3

from core.database import get_connection
from core.permissions import VALID_ROLES, require
from core.security import (
    generate_temp_password,
    hash_password,
    sanitize_text,
    verify_password,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

_USER_SELECT = """
SELECT u.id,
       u.username,
       u.full_name,
       u.password_hash,
       u.salt,
       u.role,
       u.department_id,
       d.name AS department_name,
       u.is_active,
       u.must_change_password,
       u.created_at
FROM users u
LEFT JOIN departments d ON d.id = u.department_id
"""


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
        "id":                   row["id"],
        "username":             row["username"],
        "full_name":            row["full_name"],
        "role":                 row["role"],
        "department_id":        row["department_id"],
        "department":           row["department_name"] if "department_name" in row.keys() else None,
        "is_active":            bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
    }


def _count_other_active_super_admins(conn: sqlite3.Connection, exclude_user_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users "
        "WHERE role = 'super_admin' AND is_active = 1 AND id != ?",
        (exclude_user_id,),
    ).fetchone()[0]


def _validate_department_for_role(
    conn: sqlite3.Connection,
    role: str,
    department_id: int | None,
) -> int | None:
    if role == "employee":
        if department_id is None:
            raise ValueError("An employee account must be assigned to a department.")
        dept = conn.execute(
            "SELECT id, is_active FROM departments WHERE id = ?",
            (department_id,),
        ).fetchone()
        if dept is None:
            raise ValueError("Selected department does not exist.")
        return dept["id"]
    if department_id is not None:
        dept = conn.execute(
            "SELECT id FROM departments WHERE id = ?",
            (department_id,),
        ).fetchone()
        if dept is None:
            raise ValueError("Selected department does not exist.")
    return None


# ---------------------------------------------------------------------------
# Public Auth API
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
                "INSERT INTO users "
                "(username, full_name, password_hash, salt, role, department_id, is_active, must_change_password) "
                "VALUES (?, ?, ?, ?, 'user', NULL, 1, 0)",
                (clean_username, clean_full_name, pw_hash, salt),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{clean_username}' is already taken.")

    return {
        "id":                   user_id,
        "username":             clean_username,
        "full_name":            clean_full_name,
        "role":                 "user",
        "department_id":        None,
        "department":           None,
        "is_active":            True,
        "must_change_password": False,
    }


def login(username: str, password: str) -> dict | None:
    """
    Authenticate user. Returns user dict on success, None on failure or if deactivated.
    Never reveals whether username or password was wrong.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    clean_username = username.strip()
    if not clean_username or not password:
        return None

    with closing(get_connection()) as conn:
        row = conn.execute(
            _USER_SELECT + " WHERE u.username = ?",
            (clean_username,),
        ).fetchone()

    if row is None:
        verify_password(password, "0" * 64, "00" * 32)
        return None

    if not verify_password(password, row["password_hash"], row["salt"]):
        return None

    if not row["is_active"]:
        return None

    return _row_to_user(row)


def change_own_password(user: dict | None, current_password: str, new_password: str) -> dict:
    """Allow an authenticated user (e.g., after temporary password reset) to set a new password."""
    if not isinstance(user, dict) or "id" not in user:
        raise PermissionError("Authentication required to change password.")
    if not isinstance(new_password, str) or len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters long.")
    if len(new_password) > 128:
        raise ValueError("New password must not exceed 128 characters.")
    if new_password == current_password:
        raise ValueError("New password must be different from your temporary/current password.")

    with closing(get_connection()) as conn, conn:
        row = conn.execute(
            _USER_SELECT + " WHERE u.id = ?",
            (user["id"],),
        ).fetchone()
        if row is None or not row["is_active"]:
            raise PermissionError("Account is unavailable.")
        if not verify_password(current_password, row["password_hash"], row["salt"]):
            raise ValueError("Current password is incorrect.")

        pw_hash, salt = hash_password(new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 0 WHERE id = ?",
            (pw_hash, salt, user["id"]),
        )
        updated = conn.execute(
            _USER_SELECT + " WHERE u.id = ?",
            (user["id"],),
        ).fetchone()

    return _row_to_user(updated)


# ---------------------------------------------------------------------------
# Super Admin User Management API
# ---------------------------------------------------------------------------


def create_user_account(
    actor: dict | None,
    username: str,
    full_name: str,
    password: str,
    role: str,
    department_id: int | None = None,
) -> dict:
    """Create a user with any valid role. Employees require department_id. super_admin only."""
    require(actor, "manage_users")

    if role not in VALID_ROLES:
        raise ValueError(
            f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}."
        )

    clean_username, clean_full_name = _validate_registration_inputs(username, full_name, password)
    pw_hash, salt = hash_password(password)

    try:
        with closing(get_connection()) as conn, conn:
            resolved_dept_id = _validate_department_for_role(conn, role, department_id)
            cursor = conn.execute(
                "INSERT INTO users "
                "(username, full_name, password_hash, salt, role, department_id, is_active, must_change_password) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, 0)",
                (clean_username, clean_full_name, pw_hash, salt, role, resolved_dept_id),
            )
            user_id = cursor.lastrowid
            row = conn.execute(_USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{clean_username}' is already taken.")

    return _row_to_user(row)


def create_staff_account(
    actor: dict | None,
    username: str,
    full_name: str,
    password: str,
    role: str,
    department_id: int | None = None,
) -> dict:
    """Alias for create_user_account."""
    return create_user_account(actor, username, full_name, password, role, department_id=department_id)


def set_user_active(actor: dict | None, target_user_id: int, is_active: bool) -> dict:
    """Soft-deactivate or reactivate a user account (super_admin only)."""
    require(actor, "manage_users")

    with closing(get_connection()) as conn, conn:
        target = conn.execute(
            "SELECT * FROM users WHERE id = ?", (target_user_id,)
        ).fetchone()
        if target is None:
            raise ValueError(f"User with id {target_user_id} not found.")

        if not is_active:
            if target["id"] == actor.get("id"):
                raise ValueError("You cannot deactivate your own account.")
            if target["role"] == "super_admin" and target["is_active"]:
                if _count_other_active_super_admins(conn, target["id"]) == 0:
                    raise ValueError(
                        "Cannot deactivate the last remaining active Super Admin."
                    )

        conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, target_user_id),
        )
        updated = conn.execute(
            _USER_SELECT + " WHERE u.id = ?", (target_user_id,)
        ).fetchone()

    return _row_to_user(updated)


def change_user_role(
    actor: dict | None,
    target_user_id: int,
    new_role: str,
    department_id: int | None = None,
) -> dict:
    """Change a user's role (super_admin only), protecting self and last super_admin."""
    require(actor, "manage_users")

    if new_role not in VALID_ROLES:
        raise ValueError(
            f"Invalid role '{new_role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}."
        )

    with closing(get_connection()) as conn, conn:
        target = conn.execute(
            "SELECT * FROM users WHERE id = ?", (target_user_id,)
        ).fetchone()
        if target is None:
            raise ValueError(f"User with id {target_user_id} not found.")

        if target["id"] == actor.get("id") and new_role != "super_admin":
            raise ValueError("You cannot demote your own Super Admin account.")

        if (
            target["role"] == "super_admin"
            and target["is_active"]
            and new_role != "super_admin"
            and _count_other_active_super_admins(conn, target["id"]) == 0
        ):
            raise ValueError("Cannot demote the last remaining active Super Admin.")

        effective_dept_id = department_id if department_id is not None else target["department_id"]
        resolved_dept_id = _validate_department_for_role(conn, new_role, effective_dept_id)

        conn.execute(
            "UPDATE users SET role = ?, department_id = ? WHERE id = ?",
            (new_role, resolved_dept_id, target_user_id),
        )
        updated = conn.execute(
            _USER_SELECT + " WHERE u.id = ?", (target_user_id,)
        ).fetchone()

    return _row_to_user(updated)


def set_user_department(actor: dict | None, target_user_id: int, department_id: int | None) -> dict:
    """Assign or update an employee's department (super_admin only)."""
    require(actor, "manage_users")

    with closing(get_connection()) as conn, conn:
        target = conn.execute(
            "SELECT * FROM users WHERE id = ?", (target_user_id,)
        ).fetchone()
        if target is None:
            raise ValueError(f"User with id {target_user_id} not found.")

        resolved_dept_id = _validate_department_for_role(conn, target["role"], department_id)
        conn.execute(
            "UPDATE users SET department_id = ? WHERE id = ?",
            (resolved_dept_id, target_user_id),
        )
        updated = conn.execute(
            _USER_SELECT + " WHERE u.id = ?", (target_user_id,)
        ).fetchone()

    return _row_to_user(updated)


def reset_user_password(actor: dict | None, target_user_id: int) -> str:
    """
    Generate a temporary password for target_user_id and require a password change on next login.
    Returns the generated temporary password (super_admin only).
    """
    require(actor, "manage_users")

    temp_password = generate_temp_password(12)
    pw_hash, salt = hash_password(temp_password)

    with closing(get_connection()) as conn, conn:
        target = conn.execute(
            "SELECT id FROM users WHERE id = ?", (target_user_id,)
        ).fetchone()
        if target is None:
            raise ValueError(f"User with id {target_user_id} not found.")

        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 1 WHERE id = ?",
            (pw_hash, salt, target_user_id),
        )

    return temp_password


def get_all_users(actor: dict | None) -> list[dict]:
    """
    Requires 'manage_users' (super_admin only).
    Returns list of all users (no password fields).
    """
    require(actor, "manage_users")

    with closing(get_connection()) as conn:
        rows = conn.execute(
            _USER_SELECT + " ORDER BY u.id"
        ).fetchall()

    return [
        {
            "id":                   row["id"],
            "username":             row["username"],
            "full_name":            row["full_name"],
            "role":                 row["role"],
            "department_id":        row["department_id"],
            "department":           row["department_name"],
            "is_active":            bool(row["is_active"]),
            "must_change_password": bool(row["must_change_password"]),
            "created_at":           row["created_at"],
        }
        for row in rows
    ]
