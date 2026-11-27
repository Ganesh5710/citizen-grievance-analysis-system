"""
Department management: citizen active-department lookup and super_admin CRUD.
"""

from contextlib import closing
import sqlite3

import core.database as _db
from core.permissions import require
from core.security import sanitize_text


def _row_to_department(row) -> dict:
    return {
        "id":          row["id"],
        "name":        row["name"],
        "description": row["description"],
        "is_active":   bool(row["is_active"]),
        "created_at":  row["created_at"],
    }


def get_active_departments() -> list[dict]:
    """Return all active departments ordered by ID (no special permission needed)."""
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM departments WHERE is_active = 1 ORDER BY id ASC"
        ).fetchall()
    return [_row_to_department(r) for r in rows]


def get_all_departments(actor: dict | None) -> list[dict]:
    """Return all departments including deactivated ones (for staff filters and super_admin)."""
    require(actor, "view_all")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM departments ORDER BY is_active DESC, id ASC"
        ).fetchall()
    return [_row_to_department(r) for r in rows]


def create_department(actor: dict | None, name: str, description: str = "") -> dict:
    """Create a new active department and seed default SLA policies (super_admin only)."""
    require(actor, "manage_departments")

    clean_name = sanitize_text(name, max_length=60, field_name="Department name")
    clean_desc = sanitize_text(description, max_length=200, field_name="Department description")
    if len(clean_name) < 2:
        raise ValueError("Department name must be at least 2 characters long.")
    if len(clean_desc) < 4:
        raise ValueError("Department description must be at least 4 characters long.")

    try:
        with closing(_db.get_connection()) as conn, conn:
            cursor = conn.execute(
                "INSERT INTO departments (name, description, is_active) VALUES (?, ?, 1)",
                (clean_name, clean_desc),
            )
            dept_id = cursor.lastrowid
            for priority, hours in _db.DEFAULT_SLA_HOURS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO sla_policies (department_id, priority, target_hours) "
                    "VALUES (?, ?, ?)",
                    (dept_id, priority, hours),
                )
            row = conn.execute(
                "SELECT * FROM departments WHERE id = ?", (dept_id,)
            ).fetchone()
    except sqlite3.IntegrityError:
        raise ValueError(f"Department '{clean_name}' already exists.")

    return _row_to_department(row)


def rename_department(
    actor: dict | None,
    department_id: int,
    new_name: str,
    new_description: str | None = None,
) -> dict:
    """Rename an existing department and optionally update its description (super_admin only)."""
    require(actor, "manage_departments")

    clean_name = sanitize_text(new_name, max_length=60, field_name="Department name")
    if len(clean_name) < 2:
        raise ValueError("Department name must be at least 2 characters long.")

    try:
        with closing(_db.get_connection()) as conn, conn:
            existing = conn.execute(
                "SELECT * FROM departments WHERE id = ?", (department_id,)
            ).fetchone()
            if existing is None:
                raise ValueError(f"Department with id {department_id} not found.")

            clean_desc = (
                sanitize_text(new_description, max_length=200, field_name="Department description")
                if new_description is not None
                else existing["description"]
            )
            if len(clean_desc) < 4:
                raise ValueError("Department description must be at least 4 characters long.")

            conn.execute(
                "UPDATE departments SET name = ?, description = ? WHERE id = ?",
                (clean_name, clean_desc, department_id),
            )
            updated = conn.execute(
                "SELECT * FROM departments WHERE id = ?", (department_id,)
            ).fetchone()
    except sqlite3.IntegrityError:
        raise ValueError(f"Department '{clean_name}' already exists.")

    return _row_to_department(updated)


def set_department_active(actor: dict | None, department_id: int, is_active: bool) -> dict:
    """Deactivate or reactivate a department (soft toggle; super_admin only)."""
    require(actor, "manage_departments")

    with closing(_db.get_connection()) as conn, conn:
        existing = conn.execute(
            "SELECT * FROM departments WHERE id = ?", (department_id,)
        ).fetchone()
        if existing is None:
            raise ValueError(f"Department with id {department_id} not found.")

        conn.execute(
            "UPDATE departments SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, department_id),
        )
        updated = conn.execute(
            "SELECT * FROM departments WHERE id = ?", (department_id,)
        ).fetchone()

    return _row_to_department(updated)
