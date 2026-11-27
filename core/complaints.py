"""
Complaint lifecycle: submit, query, detail, history, and status update.
Permissions are enforced here in the service layer for every operation.
"""

from contextlib import closing
from datetime import datetime, timezone
import uuid

from core.classifier import classify
import core.database as _db
from core.permissions import require
from core.security import sanitize_text

VALID_STATUSES = ("Pending", "Assigned", "In Progress", "Resolved")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_complaint(row) -> dict:
    return {
        "id":          row["id"],
        "incident_id": row["incident_id"],
        "user_id":     row["user_id"],
        "text":        row["text"],
        "location":    row["location"],
        "department":  row["department"],
        "priority":    row["priority"],
        "status":      row["status"],
        "created_at":  row["created_at"],
        "updated_at":  row["updated_at"],
    }


def submit_complaint(user: dict | None, text: str, location: str = "") -> str:
    """
    Requires 'submit_complaint' permission (only 'user' role).
    Validates & sanitizes text (min 10 chars) and location, classifies department
    and priority, generates incident_id, persists with status='Pending'.
    Returns incident_id string.
    """
    require(user, "submit_complaint")

    clean_text = sanitize_text(text, max_length=2000, field_name="Complaint description")
    if len(clean_text) < 10:
        raise ValueError("Complaint text must be at least 10 characters long.")

    clean_location = sanitize_text(location, max_length=200, field_name="Location")
    department, priority = classify(clean_text)
    incident_id = "CIV-" + uuid.uuid4().hex[:8].upper()
    now = _utc_now()

    with closing(_db.get_connection()) as conn, conn:
        conn.execute(
            "INSERT INTO complaints "
            "(incident_id, user_id, text, location, department, priority, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?)",
            (incident_id, user["id"], clean_text, clean_location, department, priority, now, now),
        )

    return incident_id


def get_complaints(user: dict | None) -> list[dict]:
    """
    - 'user' role: requires 'view_own_complaints', returns ONLY their own complaints.
    - Staff roles: requires 'view_all_complaints', returns ALL complaints.
    """
    if isinstance(user, dict) and user.get("role") == "user":
        require(user, "view_own_complaints")
        with closing(_db.get_connection()) as conn:
            rows = conn.execute(
                "SELECT * FROM complaints WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                (user["id"],),
            ).fetchall()
    else:
        require(user, "view_all_complaints")
        with closing(_db.get_connection()) as conn:
            rows = conn.execute(
                "SELECT * FROM complaints ORDER BY created_at DESC, id DESC"
            ).fetchall()

    return [_row_to_complaint(row) for row in rows]


def get_complaint_detail(user: dict | None, complaint_id: int) -> dict:
    """
    Fetch one complaint. For 'user', verifies ownership and raises PermissionError
    on any mismatch or missing ID so existence of other users' complaints is never leaked.
    """
    if isinstance(user, dict) and user.get("role") == "user":
        require(user, "view_own_complaints")
        with closing(_db.get_connection()) as conn:
            row = conn.execute(
                "SELECT * FROM complaints WHERE id = ?", (complaint_id,)
            ).fetchone()
        if row is None or row["user_id"] != user.get("id"):
            raise PermissionError("You do not have access to this complaint.")
        return _row_to_complaint(row)

    require(user, "view_all_complaints")
    with closing(_db.get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM complaints WHERE id = ?", (complaint_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Complaint with id {complaint_id} not found.")
    return _row_to_complaint(row)


def get_complaint_history(user: dict | None, complaint_id: int) -> list[dict]:
    """
    Requires 'view_complaint_history' plus ownership check for 'user' role
    (or 'view_all_complaints' for staff roles).
    Returns ordered list of audit updates joined with employee username.
    """
    require(user, "view_complaint_history")
    # Enforces both ownership (for 'user') and view_all_complaints (for staff).
    get_complaint_detail(user, complaint_id)

    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT cu.id,
                   cu.old_status,
                   cu.new_status,
                   cu.remark,
                   cu.created_at,
                   u.username AS employee_username
            FROM complaint_updates cu
            JOIN users u ON u.id = cu.employee_id
            WHERE cu.complaint_id = ?
            ORDER BY cu.created_at ASC, cu.id ASC
            """,
            (complaint_id,),
        ).fetchall()

    return [
        {
            "id":                row["id"],
            "old_status":        row["old_status"],
            "new_status":        row["new_status"],
            "remark":            row["remark"],
            "created_at":        row["created_at"],
            "employee_username": row["employee_username"],
        }
        for row in rows
    ]


def update_complaint(
    user: dict | None,
    complaint_id: int,
    new_status: str,
    remark: str = "",
) -> None:
    """
    Requires 'update_complaint' (only 'employee').
    Validates status, writes audit log to complaint_updates, and updates status in one transaction.
    """
    require(user, "update_complaint")

    if new_status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{new_status}'. Must be one of: {', '.join(VALID_STATUSES)}."
        )

    clean_remark = sanitize_text(remark, max_length=1000, field_name="Remark")
    now = _utc_now()

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT status FROM complaints WHERE id = ?", (complaint_id,)
        ).fetchone()

        if row is None:
            raise ValueError(f"Complaint with id {complaint_id} not found.")

        old_status = row["status"]

        conn.execute(
            "INSERT INTO complaint_updates "
            "(complaint_id, employee_id, old_status, new_status, remark, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (complaint_id, user["id"], old_status, new_status, clean_remark, now),
        )
        conn.execute(
            "UPDATE complaints SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, complaint_id),
        )
