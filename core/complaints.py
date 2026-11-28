"""
Complaint lifecycle: submit (with optional photos & auto-assignment), query with role/dept
scoping, detail, audit history, claim, assign/reassign, updates, feedback/reopen, soft delete.
"""

from contextlib import closing
from datetime import datetime, timezone
import uuid

from core.classifier import classify
import core.database as _db
from core.images import (
    MAX_IMAGES_PER_COMPLAINT,
    get_image_counts_map,
    prepare_images,
    save_prepared_images_in_conn,
)
from core.permissions import require
from core.security import sanitize_text
from core.sla import AUTO_CLOSE_DAYS, compute_due_at, parse_utc
from core.wards import find_nearest_ward

VALID_STATUSES = ("Pending", "Assigned", "In Progress", "Resolved", "Reopened", "Closed")
VALID_PRIORITIES = ("High", "Medium", "Low")


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _pick_least_loaded_employee(conn, department_id: int) -> dict | None:
    """Return active employee in department_id with fewest open complaints (or None)."""
    row = conn.execute(
        """
        SELECT u.id, u.username, u.full_name,
               COUNT(c.id) AS open_count
        FROM users u
        LEFT JOIN complaints c
               ON c.assigned_to = u.id
              AND c.is_deleted = 0
              AND c.status NOT IN ('Resolved', 'Closed')
        WHERE u.role = 'employee'
          AND u.is_active = 1
          AND u.department_id = ?
        GROUP BY u.id, u.username, u.full_name
        ORDER BY open_count ASC, u.id ASC
        LIMIT 1
        """,
        (department_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _row_to_complaint(row, photo_count: int = 0, now: datetime | None = None) -> dict:
    now_dt = _utc_now(now)
    due_dt = parse_utc(row["due_at"])
    status = row["status"]
    is_open = status not in ("Resolved", "Closed")
    overdue_hours = 0.0
    is_overdue = False
    if is_open and due_dt is not None and now_dt > due_dt:
        is_overdue = True
        overdue_hours = round((now_dt - due_dt).total_seconds() / 3600.0, 1)

    return {
        "id":                   row["id"],
        "incident_id":          row["incident_id"],
        "user_id":              row["user_id"],
        "reporter_username":    row["reporter_username"],
        "text":                 row["text"],
        "location":             row["location"],
        "department_id":        row["department_id"],
        "department":           row["department_name"],
        "department_is_active": bool(row["department_is_active"]),
        "ward_id":              row["ward_id"],
        "ward_name":            row["ward_name"],
        "latitude":             row["latitude"],
        "longitude":            row["longitude"],
        "priority":             row["priority"],
        "status":               status,
        "assigned_to":          row["assigned_to"],
        "assigned_username":    row["assigned_username"],
        "assigned_full_name":   row["assigned_full_name"],
        "assigned_at":          row["assigned_at"],
        "due_at":               row["due_at"],
        "is_overdue":           is_overdue,
        "overdue_hours":        overdue_hours,
        "escalation_level":     int(row["escalation_level"] or 0),
        "resolved_at":          row["resolved_at"],
        "reopen_count":         int(row["reopen_count"] or 0),
        "photo_count":          int(photo_count),
        "created_at":           row["created_at"],
        "updated_at":           row["updated_at"],
    }


_BASE_SELECT = """
SELECT c.id,
       c.incident_id,
       c.user_id,
       u.username AS reporter_username,
       c.text,
       c.location,
       c.department_id,
       d.name AS department_name,
       d.is_active AS department_is_active,
       c.ward_id,
       w.name AS ward_name,
       c.latitude,
       c.longitude,
       c.priority,
       c.status,
       c.assigned_to,
       emp.username AS assigned_username,
       emp.full_name AS assigned_full_name,
       c.assigned_at,
       c.due_at,
       c.escalation_level,
       c.resolved_at,
       c.reopen_count,
       c.is_deleted,
       c.created_at,
       c.updated_at
FROM complaints c
JOIN departments d ON d.id = c.department_id
JOIN users u ON u.id = c.user_id
LEFT JOIN wards w ON w.id = c.ward_id
LEFT JOIN users emp ON emp.id = c.assigned_to
"""


def submit_complaint(
    actor: dict | None,
    department_id: int,
    text: str,
    location: str,
    files: list | tuple | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    ward_id: int | None = None,
) -> str:
    """
    Requires 'submit_complaint' permission (only 'user' role).
    Validates department exists and is active, validates text/location and optional photos,
    auto-detects priority via classify(), sets SLA due_at, auto-assigns least-loaded active
    employee in that department (or leaves unassigned), and logs creation in complaint_updates.
    Returns incident_id string.
    """
    require(actor, "submit_complaint")

    if not isinstance(department_id, int) or isinstance(department_id, bool):
        raise ValueError("Invalid department selection.")

    clean_text = sanitize_text(text, max_length=2000, field_name="Complaint description")
    if len(clean_text) < 10:
        raise ValueError("Complaint description must be at least 10 characters long.")

    clean_location = sanitize_text(location, max_length=200, field_name="Location / Landmark")
    if len(clean_location) < 2:
        raise ValueError("Location / Landmark is required (at least 2 characters).")

    file_list = list(files) if files else []
    if len(file_list) > MAX_IMAGES_PER_COMPLAINT:
        raise ValueError(f"Maximum {MAX_IMAGES_PER_COMPLAINT} photos allowed per complaint.")

    # Validate & strip EXIF/GPS before touching the database
    prepared_images = prepare_images(file_list)

    _, priority = classify(clean_text)
    incident_id = "CIV-" + uuid.uuid4().hex[:8].upper()
    now_dt = _utc_now()
    now_str = _fmt_dt(now_dt)

    resolved_ward_id = ward_id
    if resolved_ward_id is None and latitude is not None and longitude is not None:
        nearest = find_nearest_ward(float(latitude), float(longitude))
        if nearest:
            resolved_ward_id = nearest["id"]

    with closing(_db.get_connection()) as conn, conn:
        dept = conn.execute(
            "SELECT id, name, is_active FROM departments WHERE id = ?",
            (department_id,),
        ).fetchone()
        if dept is None or not dept["is_active"]:
            raise ValueError("Selected department does not exist or is inactive.")

        if resolved_ward_id is not None:
            w_row = conn.execute("SELECT id FROM wards WHERE id = ?", (resolved_ward_id,)).fetchone()
            if w_row is None:
                raise ValueError("Selected ward does not exist.")

        due_at = compute_due_at(conn, department_id, priority, now_dt)
        assignee = _pick_least_loaded_employee(conn, department_id)
        assigned_to = assignee["id"] if assignee else None
        assigned_at = now_str if assignee else None

        cursor = conn.execute(
            """
            INSERT INTO complaints
                (incident_id, user_id, text, location, department_id, ward_id, latitude, longitude,
                 priority, status, assigned_to, assigned_at, due_at, escalation_level, reopen_count,
                 is_deleted, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?, 0, 0, 0, ?, ?)
            """,
            (
                incident_id,
                actor["id"],
                clean_text,
                clean_location,
                department_id,
                resolved_ward_id,
                float(latitude) if latitude is not None else None,
                float(longitude) if longitude is not None else None,
                priority,
                assigned_to,
                assigned_at,
                due_at,
                now_str,
                now_str,
            ),
        )
        complaint_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'department', '', 'Pending', '', ?, 'citizen_selected', ?, ?)
            """,
            (
                complaint_id,
                actor["id"],
                actor.get("username", ""),
                dept["name"],
                f"Submitted under {dept['name']} (priority: {priority})",
                now_str,
            ),
        )

        if assignee:
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, NULL, 'system', 'assigned_to', 'Pending', 'Pending',
                        'Unassigned', ?, 'system', ?, ?)
                """,
                (
                    complaint_id,
                    assignee["username"],
                    f"Auto-assigned to {assignee['username']} (fewest open complaints)",
                    now_str,
                ),
            )

        save_prepared_images_in_conn(
            conn, actor, complaint_id, "Pending", prepared_images, now=now_str
        )

    return incident_id


def get_complaints(user: dict | None, now: datetime | None = None) -> list[dict]:
    """
    - 'user' role: requires 'view_own', returns ONLY their own non-deleted complaints.
    - 'employee' role: requires 'view_all', scoped strictly to their department_id.
    - 'admin' / 'super_admin': requires 'view_all', returns ALL non-deleted complaints.
    """
    if isinstance(user, dict) and user.get("role") == "user":
        require(user, "view_own")
        with closing(_db.get_connection()) as conn:
            rows = conn.execute(
                _BASE_SELECT
                + " WHERE c.is_deleted = 0 AND c.user_id = ? ORDER BY c.created_at DESC, c.id DESC",
                (user["id"],),
            ).fetchall()
    else:
        require(user, "view_all")
        with closing(_db.get_connection()) as conn:
            if user.get("role") == "employee":
                dept_id = user.get("department_id")
                if dept_id is None:
                    return []
                rows = conn.execute(
                    _BASE_SELECT
                    + " WHERE c.is_deleted = 0 AND c.department_id = ? "
                    "ORDER BY c.created_at DESC, c.id DESC",
                    (dept_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    _BASE_SELECT
                    + " WHERE c.is_deleted = 0 ORDER BY c.created_at DESC, c.id DESC"
                ).fetchall()

    counts_map = get_image_counts_map([r["id"] for r in rows])
    return [_row_to_complaint(r, photo_count=counts_map.get(r["id"], 0), now=now) for r in rows]


def get_complaint_detail(user: dict | None, complaint_id: int, now: datetime | None = None) -> dict:
    """
    Fetch one non-deleted complaint with strict role & scope checks:
      - 'user': own complaints only (PermissionError on mismatch/missing).
      - 'employee': own department only (PermissionError if another department).
      - 'admin' / 'super_admin': all complaints.
    """
    if isinstance(user, dict) and user.get("role") == "user":
        require(user, "view_own")
        with closing(_db.get_connection()) as conn:
            row = conn.execute(
                _BASE_SELECT + " WHERE c.id = ? AND c.is_deleted = 0",
                (complaint_id,),
            ).fetchone()
        if row is None or row["user_id"] != user.get("id"):
            raise PermissionError("You do not have access to this complaint.")
        cnt = get_image_counts_map([row["id"]]).get(row["id"], 0)
        return _row_to_complaint(row, photo_count=cnt, now=now)

    require(user, "view_all")
    with closing(_db.get_connection()) as conn:
        row = conn.execute(
            _BASE_SELECT + " WHERE c.id = ? AND c.is_deleted = 0",
            (complaint_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Complaint with id {complaint_id} not found.")
    if user.get("role") == "employee":
        if user.get("department_id") is None or row["department_id"] != user.get("department_id"):
            raise PermissionError("Employees can only access complaints in their own department.")
    cnt = get_image_counts_map([row["id"]]).get(row["id"], 0)
    return _row_to_complaint(row, photo_count=cnt, now=now)


def get_complaint_history(user: dict | None, complaint_id: int) -> list[dict]:
    """
    Enforces ownership / department scope via get_complaint_detail()
    and returns chronological audit log rows for complaint_id.
    """
    get_complaint_detail(user, complaint_id)

    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT cu.id,
                   cu.field_name,
                   cu.old_status,
                   cu.new_status,
                   cu.old_value,
                   cu.new_value,
                   cu.source,
                   cu.remark,
                   cu.created_at,
                   COALESCE(u.username, NULLIF(cu.actor_label, ''), 'system') AS actor_username,
                   COALESCE(u.role, 'system') AS actor_role
            FROM complaint_updates cu
            LEFT JOIN users u ON u.id = cu.employee_id
            WHERE cu.complaint_id = ?
            ORDER BY cu.created_at ASC, cu.id ASC
            """,
            (complaint_id,),
        ).fetchall()

    return [
        {
            "id":                row["id"],
            "field_name":        row["field_name"],
            "old_status":        row["old_status"],
            "new_status":        row["new_status"],
            "old_value":         row["old_value"],
            "new_value":         row["new_value"],
            "source":            row["source"],
            "remark":            row["remark"],
            "created_at":        row["created_at"],
            "actor_username":    row["actor_username"],
            "actor_role":        row["actor_role"],
            "employee_username": row["actor_username"],
        }
        for row in rows
    ]


def get_department_employees(actor: dict | None, department_id: int) -> list[dict]:
    """Return active employees in department_id for admin/super_admin assignment dropdowns."""
    require(actor, "assign_complaint")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.full_name,
                   COUNT(c.id) AS open_count
            FROM users u
            LEFT JOIN complaints c
                   ON c.assigned_to = u.id
                  AND c.is_deleted = 0
                  AND c.status NOT IN ('Resolved', 'Closed')
            WHERE u.role = 'employee'
              AND u.is_active = 1
              AND u.department_id = ?
            GROUP BY u.id, u.username, u.full_name
            ORDER BY open_count ASC, u.id ASC
            """,
            (department_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def claim_complaint(actor: dict | None, complaint_id: int) -> dict:
    """
    Allow an active employee to claim an unassigned complaint in their own department.
    Writes audit row(s) to complaint_updates.
    """
    require(actor, "claim_complaint")

    now_str = _fmt_dt(_utc_now())
    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT id, department_id, status, assigned_to, is_deleted FROM complaints WHERE id = ?",
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")
        if actor.get("department_id") is None or row["department_id"] != actor.get("department_id"):
            raise PermissionError("You can only claim complaints in your own department.")
        if row["assigned_to"] is not None:
            raise ValueError("Complaint is already assigned.")
        if row["status"] in ("Resolved", "Closed"):
            raise ValueError("Cannot claim a Resolved or Closed complaint.")

        old_status = row["status"]
        new_status = "Assigned" if old_status == "Pending" else old_status

        conn.execute(
            """
            UPDATE complaints
            SET assigned_to = ?, assigned_at = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (actor["id"], now_str, new_status, now_str, complaint_id),
        )
        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'assigned_to', ?, ?, 'Unassigned', ?, 'staff_update', ?, ?)
            """,
            (
                complaint_id,
                actor["id"],
                actor.get("username", ""),
                old_status,
                new_status,
                actor.get("username", ""),
                "Claimed from unassigned queue",
                now_str,
            ),
        )
        if new_status != old_status:
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, ?, ?, 'status', ?, ?, ?, ?, 'staff_update', ?, ?)
                """,
                (
                    complaint_id,
                    actor["id"],
                    actor.get("username", ""),
                    old_status,
                    new_status,
                    old_status,
                    new_status,
                    "Status moved to Assigned on claim",
                    now_str,
                ),
            )

    return get_complaint_detail(actor, complaint_id)


def assign_complaint(
    actor: dict | None,
    complaint_id: int,
    employee_id: int | None,
    remark: str = "",
) -> dict:
    """
    Assign or reassign a complaint to an active employee in the complaint's department,
    or pass employee_id=None to unassign (admin / super_admin only).
    """
    require(actor, "assign_complaint")

    clean_remark = sanitize_text(remark, max_length=500, field_name="Assignment remark")
    now_str = _fmt_dt(_utc_now())

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            """
            SELECT c.id, c.department_id, c.status, c.assigned_to, c.is_deleted,
                   u.username AS old_emp_username
            FROM complaints c
            LEFT JOIN users u ON u.id = c.assigned_to
            WHERE c.id = ?
            """,
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")

        new_emp_username = "Unassigned"
        if employee_id is not None:
            emp = conn.execute(
                "SELECT id, username, role, department_id, is_active FROM users WHERE id = ?",
                (employee_id,),
            ).fetchone()
            if emp is None or not emp["is_active"] or emp["role"] != "employee":
                raise ValueError("Assignee must be an active employee account.")
            if emp["department_id"] != row["department_id"]:
                raise ValueError(
                    "Employee must belong to the complaint's current department."
                )
            new_emp_username = emp["username"]

        old_emp_username = row["old_emp_username"] or "Unassigned"
        assigned_at = now_str if employee_id is not None else None

        conn.execute(
            "UPDATE complaints SET assigned_to = ?, assigned_at = ?, updated_at = ? WHERE id = ?",
            (employee_id, assigned_at, now_str, complaint_id),
        )
        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'assigned_to', ?, ?, ?, ?, 'staff_update', ?, ?)
            """,
            (
                complaint_id,
                actor["id"],
                actor.get("username", ""),
                row["status"],
                row["status"],
                old_emp_username,
                new_emp_username,
                clean_remark or f"Assigned to {new_emp_username}",
                now_str,
            ),
        )

    return get_complaint_detail(actor, complaint_id)


def update_complaint(
    user: dict | None,
    complaint_id: int,
    new_status: str | None = None,
    remark: str = "",
    department_id: int | None = None,
    priority: str | None = None,
) -> None:
    """
    Update complaint status/remark (employee only when assigned to them; admin & super_admin on all)
    and/or department/priority (admin & super_admin ONLY).
    - Resolving requires a closing remark >= 10 characters.
    - Changing priority or department recalculates SLA due_at; changing department auto-reassigns
      to the least-loaded active employee in the new department (or unassigns).
    """
    require(user, "update_status_remark")

    if department_id is not None or priority is not None:
        require(user, "edit_complaint_fields")

    if new_status is not None and new_status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{new_status}'. Must be one of: {', '.join(VALID_STATUSES)}."
        )
    if priority is not None and priority not in VALID_PRIORITIES:
        raise ValueError(
            f"Invalid priority '{priority}'. Must be one of: {', '.join(VALID_PRIORITIES)}."
        )

    clean_remark = sanitize_text(remark, max_length=1000, field_name="Remark")
    if new_status == "Resolved" and len(clean_remark) < 10:
        raise ValueError(
            "Resolving a complaint requires a closing remark of at least 10 characters."
        )

    now_dt = _utc_now()
    now_str = _fmt_dt(now_dt)

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            """
            SELECT c.status, c.department_id, c.priority, c.assigned_to,
                   c.due_at, c.resolved_at, c.created_at, c.reopen_count, c.is_deleted,
                   d.name AS dept_name,
                   emp.username AS old_emp_username
            FROM complaints c
            JOIN departments d ON d.id = c.department_id
            LEFT JOIN users emp ON emp.id = c.assigned_to
            WHERE c.id = ?
            """,
            (complaint_id,),
        ).fetchone()

        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")

        if user.get("role") == "employee":
            if user.get("department_id") is None or row["department_id"] != user.get("department_id"):
                raise PermissionError("Employees can only update complaints in their own department.")
            if row["assigned_to"] != user.get("id"):
                raise PermissionError("Employees can only update complaints assigned to them.")

        old_status = row["status"]
        old_dept_id = row["department_id"]
        old_dept_name = row["dept_name"]
        old_priority = row["priority"]

        target_status = new_status if new_status is not None else old_status
        target_dept_id = department_id if department_id is not None else old_dept_id
        target_priority = priority if priority is not None else old_priority

        new_dept_name = old_dept_name
        if target_dept_id != old_dept_id:
            dept_row = conn.execute(
                "SELECT id, name FROM departments WHERE id = ?",
                (target_dept_id,),
            ).fetchone()
            if dept_row is None:
                raise ValueError(f"Department with id {target_dept_id} not found.")
            new_dept_name = dept_row["name"]

        # Recalculate due_at when priority or department changes
        target_due_at = row["due_at"]
        if target_dept_id != old_dept_id or target_priority != old_priority or target_due_at is None:
            target_due_at = compute_due_at(conn, target_dept_id, target_priority, row["created_at"])

        # Handle resolved_at transitions
        target_resolved_at = row["resolved_at"]
        if target_status == "Resolved" and old_status != "Resolved":
            target_resolved_at = now_str
        elif target_status == "Closed" and target_resolved_at is None:
            target_resolved_at = now_str
        elif target_status not in ("Resolved", "Closed"):
            target_resolved_at = None

        # Auto-reassign (or unassign) when department changes
        target_assigned_to = row["assigned_to"]
        target_assigned_at = None
        new_assignee = None
        dept_changed = target_dept_id != old_dept_id
        if dept_changed:
            new_assignee = _pick_least_loaded_employee(conn, target_dept_id)
            target_assigned_to = new_assignee["id"] if new_assignee else None
            target_assigned_at = now_str if new_assignee else None

        audit_rows: list[tuple[str, str, str, str]] = []
        if target_status != old_status or (
            not dept_changed and target_priority == old_priority and clean_remark
        ):
            audit_rows.append(("status", old_status, target_status, clean_remark))

        if dept_changed:
            audit_rows.append(("department", old_dept_name, new_dept_name, clean_remark))

        if target_priority != old_priority:
            audit_rows.append(("priority", old_priority, target_priority, clean_remark))

        if not audit_rows:
            audit_rows.append(("status", old_status, target_status, clean_remark))

        for field_name, old_val, new_val, row_remark in audit_rows:
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'staff_update', ?, ?)
                """,
                (
                    complaint_id,
                    user["id"],
                    user.get("username", ""),
                    field_name,
                    old_status,
                    target_status,
                    old_val,
                    new_val,
                    row_remark,
                    now_str,
                ),
            )

        if dept_changed:
            old_emp_name = row["old_emp_username"] or "Unassigned"
            new_emp_name = new_assignee["username"] if new_assignee else "Unassigned"
            if old_emp_name != new_emp_name or row["assigned_to"] is not None:
                conn.execute(
                    """
                    INSERT INTO complaint_updates
                        (complaint_id, employee_id, actor_label, field_name,
                         old_status, new_status, old_value, new_value, source, remark, created_at)
                    VALUES (?, NULL, 'system', 'assigned_to', ?, ?, ?, ?, 'system', ?, ?)
                    """,
                    (
                        complaint_id,
                        target_status,
                        target_status,
                        old_emp_name,
                        new_emp_name,
                        f"Reassigned automatically after department change to {new_dept_name}",
                        now_str,
                    ),
                )

        if dept_changed:
            conn.execute(
                """
                UPDATE complaints
                SET status = ?, department_id = ?, priority = ?, due_at = ?,
                    assigned_to = ?, assigned_at = ?, resolved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    target_status,
                    target_dept_id,
                    target_priority,
                    target_due_at,
                    target_assigned_to,
                    target_assigned_at,
                    target_resolved_at,
                    now_str,
                    complaint_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE complaints
                SET status = ?, priority = ?, due_at = ?, resolved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    target_status,
                    target_priority,
                    target_due_at,
                    target_resolved_at,
                    now_str,
                    complaint_id,
                ),
            )


def get_complaint_feedback(actor: dict | None, complaint_id: int) -> list[dict]:
    """Return feedback entries for a complaint (respects get_complaint_detail scope)."""
    get_complaint_detail(actor, complaint_id)
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT cf.id, cf.complaint_id, cf.user_id, cf.resolution_cycle,
                   cf.rating, cf.comment, cf.accepted, cf.created_at, u.username
            FROM complaint_feedback cf
            JOIN users u ON u.id = cf.user_id
            WHERE cf.complaint_id = ?
            ORDER BY cf.resolution_cycle ASC, cf.id ASC
            """,
            (complaint_id,),
        ).fetchall()
    return [
        {
            "id":               r["id"],
            "complaint_id":     r["complaint_id"],
            "user_id":          r["user_id"],
            "username":         r["username"],
            "resolution_cycle": int(r["resolution_cycle"]),
            "rating":           int(r["rating"]),
            "comment":          r["comment"],
            "accepted":         bool(r["accepted"]),
            "created_at":       r["created_at"],
        }
        for r in rows
    ]


def submit_feedback(
    actor: dict | None,
    complaint_id: int,
    accepted: bool,
    rating: int,
    comment: str = "",
    now: datetime | None = None,
) -> dict:
    """
    Citizen feedback on their own Resolved complaint (within 7 days, once per resolution cycle):
      - accepted=True  ("Confirm resolved"): sets status='Closed'.
      - accepted=False ("Not resolved"): requires comment, sets status='Reopened',
        increments reopen_count, resets escalation_level=0, recalculates due_at from now,
        and returns complaint to the same active employee (or unassigned queue).
    """
    require(actor, "submit_feedback")

    if not isinstance(rating, int) or isinstance(rating, bool) or not (1 <= rating <= 5):
        raise ValueError("Rating must be an integer between 1 and 5.")

    clean_comment = sanitize_text(comment, max_length=1000, field_name="Feedback comment")
    if not accepted and len(clean_comment) < 3:
        raise ValueError("Please provide a comment explaining why the issue is not resolved.")

    now_dt = _utc_now(now)
    now_str = _fmt_dt(now_dt)

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            """
            SELECT id, user_id, department_id, priority, status, assigned_to,
                   resolved_at, reopen_count, is_deleted
            FROM complaints
            WHERE id = ?
            """,
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"] or row["user_id"] != actor.get("id"):
            raise PermissionError("You can only submit feedback on your own complaints.")
        if row["status"] != "Resolved":
            raise ValueError("Feedback can only be submitted when a complaint is Resolved.")

        resolved_dt = parse_utc(row["resolved_at"])
        if resolved_dt is not None:
            if (now_dt - resolved_dt).total_seconds() > AUTO_CLOSE_DAYS * 86400:
                raise ValueError("The 7-day feedback window for this resolution has expired.")

        cycle = int(row["reopen_count"] or 0)
        dup = conn.execute(
            "SELECT 1 FROM complaint_feedback WHERE complaint_id = ? AND resolution_cycle = ?",
            (complaint_id, cycle),
        ).fetchone()
        if dup is not None:
            raise ValueError("Feedback has already been submitted for this resolution.")

        conn.execute(
            """
            INSERT INTO complaint_feedback
                (complaint_id, user_id, resolution_cycle, rating, comment, accepted, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (complaint_id, actor["id"], cycle, rating, clean_comment, 1 if accepted else 0, now_str),
        )

        if accepted:
            conn.execute(
                "UPDATE complaints SET status = 'Closed', updated_at = ? WHERE id = ?",
                (now_str, complaint_id),
            )
            remark_msg = f"Citizen confirmed resolution (Rating: {rating}/5)."
            if clean_comment:
                remark_msg += f" {clean_comment}"
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, ?, ?, 'status', 'Resolved', 'Closed',
                        'Resolved', 'Closed', 'citizen_feedback', ?, ?)
                """,
                (complaint_id, actor["id"], actor.get("username", ""), remark_msg, now_str),
            )
        else:
            new_reopen_count = cycle + 1
            new_due_at = compute_due_at(conn, row["department_id"], row["priority"], now_dt)

            # Return to the same employee if still active in the department; otherwise unassigned
            keep_assigned = None
            if row["assigned_to"] is not None:
                emp = conn.execute(
                    "SELECT id FROM users WHERE id = ? AND role = 'employee' AND is_active = 1 AND department_id = ?",
                    (row["assigned_to"], row["department_id"]),
                ).fetchone()
                if emp is not None:
                    keep_assigned = emp["id"]

            conn.execute(
                """
                UPDATE complaints
                SET status = 'Reopened',
                    reopen_count = ?,
                    due_at = ?,
                    escalation_level = 0,
                    resolved_at = NULL,
                    assigned_to = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_reopen_count, new_due_at, keep_assigned, now_str, complaint_id),
            )
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, ?, ?, 'status', 'Resolved', 'Reopened',
                        'Resolved', 'Reopened', 'citizen_feedback', ?, ?)
                """,
                (
                    complaint_id,
                    actor["id"],
                    actor.get("username", ""),
                    f"Reopened by citizen (Rating: {rating}/5): {clean_comment}",
                    now_str,
                ),
            )

    return get_complaint_detail(actor, complaint_id, now=now_dt)


def delete_complaint(user: dict | None, complaint_id: int, reason: str = "") -> None:
    """
    Soft-delete a complaint (super_admin only) and write an audit row to complaint_updates.
    """
    require(user, "delete_complaint")

    clean_reason = sanitize_text(reason, max_length=500, field_name="Deletion reason")
    now_str = _fmt_dt(_utc_now())

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT id, status, is_deleted FROM complaints WHERE id = ?",
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")

        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'deleted', ?, ?, 'Active', 'Deleted', 'staff_update', ?, ?)
            """,
            (
                complaint_id,
                user["id"],
                user.get("username", ""),
                row["status"],
                row["status"],
                clean_reason or "Complaint deleted by Super Admin",
                now_str,
            ),
        )
        conn.execute(
            "UPDATE complaints SET is_deleted = 1, updated_at = ? WHERE id = ?",
            (now_str, complaint_id),
        )
