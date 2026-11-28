"""
Authentication & user management: register, login with rate-limit lockout,
30-minute session timeout helper, forced password change, preferences,
and super_admin user lifecycle operations.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import re
import sqlite3

from core.config import get_env_int
from core.database import get_connection
from core.logging_setup import get_logger
from core.permissions import VALID_ROLES, require
from core.security import (
    generate_temp_password,
    hash_password,
    sanitize_text,
    verify_password,
)

_logger = get_logger("auth")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_PHONE_DIGITS_RE = re.compile(r"\d")
_VALID_LANGS = {"en", "te", "hi"}

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
       u.failed_login_attempts,
       u.locked_until,
       u.preferred_language,
       u.phone,
       u.email,
       u.email_notifications,
       u.created_at
FROM users u
LEFT JOIN departments d ON d.id = u.department_id
"""


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_session_expired(
    last_activity: datetime | str | None,
    now: datetime | None = None,
    timeout_minutes: int | None = None,
) -> bool:
    """Return True if last_activity is older than the configured session timeout (default 30 min)."""
    if last_activity is None:
        return False
    timeout = timeout_minutes if timeout_minutes is not None else get_env_int("CGS_SESSION_TIMEOUT_MINUTES", 30)
    if timeout <= 0:
        return False
    last_dt = _parse_dt(last_activity) if isinstance(last_activity, str) else _utc_now(last_activity)
    if last_dt is None:
        return True
    now_dt = _utc_now(now)
    return (now_dt - last_dt).total_seconds() > timeout * 60


def _validate_registration_inputs(username: str, full_name: str, password: str) -> tuple[str, str]:
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


def _validate_contact_fields(phone: str | None, email: str | None) -> tuple[str, str]:
    clean_phone = sanitize_text(phone or "", max_length=30, field_name="Phone")
    clean_email = sanitize_text(email or "", max_length=120, field_name="Email")
    if clean_phone:
        digits = _PHONE_DIGITS_RE.findall(clean_phone)
        if len(digits) < 7 or len(digits) > 15:
            raise ValueError("Phone number must contain between 7 and 15 digits.")
    if clean_email and not _EMAIL_RE.match(clean_email):
        raise ValueError("Email address format is invalid.")
    return clean_phone, clean_email


def _row_to_user(row: sqlite3.Row) -> dict:
    return {
        "id":                   row["id"],
        "username":             row["username"],
        "full_name":            row["full_name"],
        "role":                 row["role"],
        "department_id":        row["department_id"],
        "department":           row["department_name"] if "department_name" in row.keys() else None,
        "is_active":            bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "preferred_language":   row["preferred_language"] if "preferred_language" in row.keys() else "en",
        "phone":                row["phone"] if "phone" in row.keys() else "",
        "email":                row["email"] if "email" in row.keys() else "",
        "email_notifications":  bool(row["email_notifications"]) if "email_notifications" in row.keys() else True,
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
            "SELECT id FROM departments WHERE id = ?",
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


def _record_failed_attempt(conn: sqlite3.Connection, clean_username: str, now_dt: datetime) -> None:
    max_attempts = get_env_int("CGS_LOCKOUT_MAX_ATTEMPTS", 5)
    lock_minutes = get_env_int("CGS_LOCKOUT_MINUTES", 5)
    now_str = _fmt_dt(now_dt)

    attempt_row = conn.execute(
        "SELECT failed_count, locked_until FROM login_attempts WHERE username = ?",
        (clean_username,),
    ).fetchone()
    prev_locked = _parse_dt(attempt_row["locked_until"]) if attempt_row else None
    if attempt_row and prev_locked is not None and now_dt >= prev_locked:
        # Lock window expired; start fresh count from 1
        new_count = 1
    else:
        new_count = (int(attempt_row["failed_count"]) if attempt_row else 0) + 1

    locked_until_str = None
    if new_count >= max_attempts:
        locked_until_str = _fmt_dt(now_dt + timedelta(minutes=lock_minutes))
        _logger.warning("Login locked for username '%s' after %d failed attempts.", clean_username, new_count)

    conn.execute(
        """
        INSERT INTO login_attempts (username, failed_count, locked_until, last_attempt_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            failed_count = excluded.failed_count,
            locked_until = excluded.locked_until,
            last_attempt_at = excluded.last_attempt_at
        """,
        (clean_username, new_count, locked_until_str, now_str),
    )
    conn.execute(
        "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE username = ?",
        (new_count, locked_until_str, clean_username),
    )


def _clear_failed_attempts(conn: sqlite3.Connection, clean_username: str) -> None:
    conn.execute("DELETE FROM login_attempts WHERE username = ?", (clean_username,))
    conn.execute(
        "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE username = ?",
        (clean_username,),
    )


def is_login_locked(username: str, now: datetime | None = None) -> bool:
    """Return True if username is currently inside a login lockout window."""
    if not isinstance(username, str) or not username.strip():
        return False
    clean_username = username.strip()
    now_dt = _utc_now(now)
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT locked_until FROM login_attempts WHERE username = ?",
            (clean_username,),
        ).fetchone()
    if row is None or not row["locked_until"]:
        return False
    locked_dt = _parse_dt(row["locked_until"])
    return locked_dt is not None and now_dt < locked_dt


# ---------------------------------------------------------------------------
# Public Auth API
# ---------------------------------------------------------------------------


def register(
    username: str,
    full_name: str,
    password: str,
    phone: str = "",
    email: str = "",
    preferred_language: str = "en",
) -> dict:
    """Public sign-up — always creates role='user'."""
    clean_username, clean_full_name = _validate_registration_inputs(username, full_name, password)
    clean_phone, clean_email = _validate_contact_fields(phone, email)
    lang = preferred_language if preferred_language in _VALID_LANGS else "en"
    pw_hash, salt = hash_password(password)

    try:
        with closing(get_connection()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO users
                    (username, full_name, password_hash, salt, role, department_id,
                     is_active, must_change_password, preferred_language, phone, email, email_notifications)
                VALUES (?, ?, ?, ?, 'user', NULL, 1, 0, ?, ?, ?, 1)
                """,
                (clean_username, clean_full_name, pw_hash, salt, lang, clean_phone, clean_email),
            )
            user_id = cursor.lastrowid
            row = conn.execute(_USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{clean_username}' is already taken.")

    return _row_to_user(row)


def login(username: str, password: str, now: datetime | None = None) -> dict | None:
    """
    Authenticate user with constant-time check and 5-failure/5-minute rate limiting.
    Never reveals whether username or password was wrong.
    """
    if not isinstance(username, str) or not isinstance(password, str):
        return None
    clean_username = username.strip()
    if not clean_username or not password:
        return None

    now_dt = _utc_now(now)

    with closing(get_connection()) as conn, conn:
        attempt = conn.execute(
            "SELECT locked_until FROM login_attempts WHERE username = ?",
            (clean_username,),
        ).fetchone()
        if attempt and attempt["locked_until"]:
            locked_dt = _parse_dt(attempt["locked_until"])
            if locked_dt is not None and now_dt < locked_dt:
                # Constant-time dummy work even when locked
                verify_password(password, "0" * 64, "00" * 32)
                return None

        row = conn.execute(
            _USER_SELECT + " WHERE u.username = ?",
            (clean_username,),
        ).fetchone()

        if row is None:
            verify_password(password, "0" * 64, "00" * 32)
            _record_failed_attempt(conn, clean_username, now_dt)
            return None

        if not verify_password(password, row["password_hash"], row["salt"]):
            _record_failed_attempt(conn, clean_username, now_dt)
            return None

        if not row["is_active"]:
            return None

        _clear_failed_attempts(conn, clean_username)
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
        row = conn.execute(_USER_SELECT + " WHERE u.id = ?", (user["id"],)).fetchone()
        if row is None or not row["is_active"]:
            raise PermissionError("Account is unavailable.")
        if not verify_password(current_password, row["password_hash"], row["salt"]):
            raise ValueError("Current password is incorrect.")

        pw_hash, salt = hash_password(new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 0 WHERE id = ?",
            (pw_hash, salt, user["id"]),
        )
        updated = conn.execute(_USER_SELECT + " WHERE u.id = ?", (user["id"],)).fetchone()

    return _row_to_user(updated)


def update_own_preferences(
    user: dict | None,
    preferred_language: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    email_notifications: bool | None = None,
) -> dict:
    """Allow an authenticated user to update language, phone, email, and notification preferences."""
    if not isinstance(user, dict) or "id" not in user:
        raise PermissionError("Authentication required.")

    with closing(get_connection()) as conn, conn:
        row = conn.execute(_USER_SELECT + " WHERE u.id = ?", (user["id"],)).fetchone()
        if row is None or not row["is_active"]:
            raise PermissionError("Account is unavailable.")

        lang = preferred_language if preferred_language in _VALID_LANGS else row["preferred_language"]
        clean_phone, clean_email = _validate_contact_fields(
            row["phone"] if phone is None else phone,
            row["email"] if email is None else email,
        )
        email_notif = (
            (1 if email_notifications else 0)
            if email_notifications is not None
            else row["email_notifications"]
        )
        conn.execute(
            """
            UPDATE users
            SET preferred_language = ?, phone = ?, email = ?, email_notifications = ?
            WHERE id = ?
            """,
            (lang, clean_phone, clean_email, email_notif, user["id"]),
        )
        updated = conn.execute(_USER_SELECT + " WHERE u.id = ?", (user["id"],)).fetchone()

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
    phone: str = "",
    email: str = "",
) -> dict:
    """Create a user with any valid role. Employees require department_id. super_admin only."""
    require(actor, "manage_users")

    if role not in VALID_ROLES:
        raise ValueError(
            f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}."
        )

    clean_username, clean_full_name = _validate_registration_inputs(username, full_name, password)
    clean_phone, clean_email = _validate_contact_fields(phone, email)
    pw_hash, salt = hash_password(password)

    try:
        with closing(get_connection()) as conn, conn:
            resolved_dept_id = _validate_department_for_role(conn, role, department_id)
            cursor = conn.execute(
                """
                INSERT INTO users
                    (username, full_name, password_hash, salt, role, department_id,
                     is_active, must_change_password, phone, email, email_notifications)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, 1)
                """,
                (clean_username, clean_full_name, pw_hash, salt, role, resolved_dept_id, clean_phone, clean_email),
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
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target is None:
            raise ValueError(f"User with id {target_user_id} not found.")

        if not is_active:
            if target["id"] == actor.get("id"):
                raise ValueError("You cannot deactivate your own account.")
            if target["role"] == "super_admin" and target["is_active"]:
                if _count_other_active_super_admins(conn, target["id"]) == 0:
                    raise ValueError("Cannot deactivate the last remaining active Super Admin.")

        conn.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, target_user_id),
        )
        updated = conn.execute(_USER_SELECT + " WHERE u.id = ?", (target_user_id,)).fetchone()

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
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
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
        updated = conn.execute(_USER_SELECT + " WHERE u.id = ?", (target_user_id,)).fetchone()

    return _row_to_user(updated)


def set_user_department(actor: dict | None, target_user_id: int, department_id: int | None) -> dict:
    """Assign or update an employee's department (super_admin only)."""
    require(actor, "manage_users")

    with closing(get_connection()) as conn, conn:
        target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target is None:
            raise ValueError(f"User with id {target_user_id} not found.")

        resolved_dept_id = _validate_department_for_role(conn, target["role"], department_id)
        conn.execute(
            "UPDATE users SET department_id = ? WHERE id = ?",
            (resolved_dept_id, target_user_id),
        )
        updated = conn.execute(_USER_SELECT + " WHERE u.id = ?", (target_user_id,)).fetchone()

    return _row_to_user(updated)


def reset_user_password(actor: dict | None, target_user_id: int) -> str:
    """Generate a temporary password and require change on next login (super_admin only)."""
    require(actor, "manage_users")

    temp_password = generate_temp_password(12)
    pw_hash, salt = hash_password(temp_password)

    with closing(get_connection()) as conn, conn:
        target = conn.execute("SELECT id, username FROM users WHERE id = ?", (target_user_id,)).fetchone()
        if target is None:
            raise ValueError(f"User with id {target_user_id} not found.")

        conn.execute(
            "UPDATE users SET password_hash = ?, salt = ?, must_change_password = 1, "
            "failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
            (pw_hash, salt, target_user_id),
        )
        conn.execute("DELETE FROM login_attempts WHERE username = ?", (target["username"],))

    return temp_password


def get_all_users(actor: dict | None) -> list[dict]:
    """Return list of all users (super_admin only, no password fields)."""
    require(actor, "manage_users")

    with closing(get_connection()) as conn:
        rows = conn.execute(_USER_SELECT + " ORDER BY u.id").fetchall()

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
            "preferred_language":   row["preferred_language"],
            "phone":                row["phone"],
            "email":                row["email"],
            "email_notifications":  bool(row["email_notifications"]),
            "created_at":           row["created_at"],
        }
        for row in rows
    ]
