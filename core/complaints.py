"""
Complaint lifecycle: submit (with optional photos & auto-assignment), query with role/dept
scoping, detail, audit history, claim, assign/reassign, updates, feedback/reopen, soft delete.
"""

from contextlib import closing
from datetime import datetime, timezone
import json
import uuid

from core.classifier import classify
from core.config import get_env_float
import core.database as _db
from core.images import (
    MAX_IMAGES_PER_COMPLAINT,
    add_after_images,
    get_image_counts_map,
    prepare_images,
    save_prepared_images_in_conn,
)
from core.locations import resolve_location_selection
from core.permissions import require
from core.security import sanitize_text
from core.sla import AUTO_CLOSE_DAYS, compute_due_at, parse_utc
from core.wards import find_nearest_ward
from ml.classifier import predict_complaint
from nlp.duration import looks_like_unparsed_duration

VALID_STATUSES = (
    "Submitted",
    "Awaiting Assignment",
    "Pending",
    "Assigned",
    "In Progress",
    "Resolved",
    "Reopened",
    "Closed",
)
VALID_PRIORITIES = ("High", "Medium", "Low")


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _pick_least_loaded_employee(
    conn,
    department_id: int,
    district_id: int | None = None,
    mandal_id: int | None = None,
) -> dict | None:
    """Return active employee in matching department + jurisdiction with fewest open complaints."""
    where = ["u.role = 'employee'", "u.is_active = 1", "u.department_id = ?"]
    params: list = [department_id]
    if district_id is not None:
        where.append("u.district_id = ?")
        params.append(int(district_id))
    else:
        where.append("u.district_id IS NULL")

    order_expr = "open_count ASC, u.id ASC"
    if mandal_id is not None:
        order_expr = "CASE WHEN u.mandal_id = ? THEN 0 WHEN u.mandal_id IS NULL THEN 1 ELSE 2 END, " + order_expr
        params_with_order = [*params, int(mandal_id)]
    else:
        params_with_order = params

    row = conn.execute(
        f"""
        SELECT u.id, u.username, u.full_name, u.district_id, u.mandal_id,
               COUNT(c.id) AS open_count
        FROM users u
        LEFT JOIN complaints c
               ON c.assigned_to = u.id
              AND c.is_deleted = 0
              AND c.status NOT IN ('Resolved', 'Closed')
        WHERE {' AND '.join(where)}
        GROUP BY u.id, u.username, u.full_name, u.district_id, u.mandal_id
        ORDER BY {order_expr}
        LIMIT 1
        """,
        params_with_order,
    ).fetchone()
    return dict(row) if row is not None else None


def _get_admin_jurisdictions_in_conn(conn, actor: dict) -> list[tuple[int, int | None]]:
    """Return list of (district_id, department_id_or_None) allowed for this admin."""
    rows = conn.execute(
        "SELECT district_id, department_id FROM admin_jurisdictions WHERE user_id = ?",
        (actor["id"],),
    ).fetchall()
    scopes = [(int(r["district_id"]), r["department_id"]) for r in rows]
    if not scopes and actor.get("district_id") is not None:
        scopes = [(int(actor["district_id"]), None)]
    return scopes


def _assert_admin_jurisdiction(
    conn,
    actor: dict,
    district_id: int | None,
    department_id: int,
) -> None:
    """Enforce district (+ optional department) scope for admins. Super Admin bypasses cleanly."""
    if actor.get("role") != "admin":
        return
    scopes = _get_admin_jurisdictions_in_conn(conn, actor)
    if district_id is None:
        if scopes:
            raise PermissionError("Scoped admin cannot access complaints outside their district jurisdiction.")
        return
    if not any(
        d_id == int(district_id) and (dep_id is None or int(dep_id) == int(department_id))
        for d_id, dep_id in scopes
    ):
        raise PermissionError("You do not have jurisdiction over complaints in this district/department.")


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
        "state_id":             row["state_id"],
        "state_name":           row["state_name"] or "",
        "district_id":          row["district_id"],
        "district_name":        row["district_name"] or "",
        "mandal_id":            row["mandal_id"],
        "mandal_name":          row["mandal_name"] or "",
        "area_id":              row["area_id"],
        "area_name":            row["area_name"] or "",
        "pincode":              row["pincode"] or "",
        "latitude":             row["latitude"] if row["latitude"] is not None else row["area_latitude"],
        "longitude":            row["longitude"] if row["longitude"] is not None else row["area_longitude"],
        "raw_latitude":         row["latitude"],
        "raw_longitude":        row["longitude"],
        "priority":             row["priority"],
        "status":               status,
        "assigned_to":          row["assigned_to"],
        "assigned_by":          row["assigned_by"],
        "assigned_by_username": row["assigned_by_username"],
        "assigned_username":    row["assigned_username"],
        "assigned_full_name":   row["assigned_full_name"],
        "assigned_at":          row["assigned_at"],
        "accepted_at":          row["accepted_at"],
        "assignment_instructions": row["assignment_instructions"] or "",
        "unassigned_escalated": bool(row["unassigned_escalated"] or 0),
        "unassigned_escalated_at": row["unassigned_escalated_at"],
        "due_at":               row["due_at"],
        "is_overdue":           is_overdue,
        "overdue_hours":        overdue_hours,
        "escalation_level":     int(row["escalation_level"] or 0),
        "resolved_at":          row["resolved_at"],
        "reopen_count":         int(row["reopen_count"] or 0),
        "photo_count":          int(photo_count),
        "ml_suggested_department": row["ml_suggested_department"],
        "ml_confidence":        row["ml_confidence"],
        "ml_top_terms":         json.loads(row["ml_top_terms"] or "[]") if isinstance(row["ml_top_terms"], str) else [],
        "duration_hours":       row["duration_hours"],
        "duration_matched_text": row["duration_matched_text"] or "",
        "duration_confidence":  row["duration_confidence"],
        "is_recurring":         bool(row["is_recurring"] or 0),
        "priority_reason":      row["priority_reason"] or "",
        "district_allow_self_claim": bool(row["district_allow_self_claim"] or 0),
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
       c.state_id,
       st.name AS state_name,
       c.district_id,
       dist.name AS district_name,
       COALESCE(dist.allow_self_claim, 0) AS district_allow_self_claim,
       c.mandal_id,
       mnd.name AS mandal_name,
       c.area_id,
       ar.name AS area_name,
       ar.latitude AS area_latitude,
       ar.longitude AS area_longitude,
       c.pincode,
       c.latitude,
       c.longitude,
       c.priority,
       c.status,
       c.assigned_to,
       c.assigned_by,
       adm_by.username AS assigned_by_username,
       emp.username AS assigned_username,
       emp.full_name AS assigned_full_name,
       c.assigned_at,
       c.accepted_at,
       c.assignment_instructions,
       c.unassigned_escalated,
       c.unassigned_escalated_at,
       c.due_at,
       c.escalation_level,
       c.resolved_at,
       c.reopen_count,
       c.ml_suggested_department,
       c.ml_confidence,
       c.ml_top_terms,
       c.duration_hours,
       c.duration_matched_text,
       c.duration_confidence,
       c.is_recurring,
       c.priority_reason,
       c.is_deleted,
       c.created_at,
       c.updated_at
FROM complaints c
JOIN departments d ON d.id = c.department_id
JOIN users u ON u.id = c.user_id
LEFT JOIN wards w ON w.id = c.ward_id
LEFT JOIN states st ON st.id = c.state_id
LEFT JOIN districts dist ON dist.id = c.district_id
LEFT JOIN mandals mnd ON mnd.id = c.mandal_id
LEFT JOIN areas ar ON ar.id = c.area_id
LEFT JOIN users emp ON emp.id = c.assigned_to
LEFT JOIN users adm_by ON adm_by.id = c.assigned_by
"""


def submit_complaint(
    actor: dict | None,
    department_id: int,
    text: str,
    location: str = "",
    files: list | tuple | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    ward_id: int | None = None,
    pincode: str | None = None,
    state_id: int | None = None,
    district_id: int | None = None,
    mandal_id: int | None = None,
    area_id: int | None = None,
) -> str:
    """
    Requires 'submit_complaint' permission (only 'user' role).
    When jurisdiction/location parameters (pincode, district_id, area_id) are provided,
    validates India Post pincode/hierarchy and puts the complaint in 'Awaiting Assignment'
    for the District Admin (no auto-assign). Legacy calls without location hierarchy keep
    compatibility ('Pending' + auto-assign).
    """
    require(actor, "submit_complaint")

    if not isinstance(department_id, int) or isinstance(department_id, bool):
        raise ValueError("Invalid department selection.")

    clean_text = sanitize_text(text, max_length=2000, field_name="Complaint description")
    if len(clean_text) < 10:
        raise ValueError("Complaint description must be at least 10 characters long.")

    has_loc_hierarchy = any(x is not None and str(x).strip() != "" for x in (pincode, district_id, area_id))
    clean_location = sanitize_text(location or "", max_length=200, field_name="Location / Landmark")
    if not has_loc_hierarchy and len(clean_location) < 2:
        raise ValueError("Location / Landmark is required (at least 2 characters).")

    resolved_loc = resolve_location_selection(
        pincode=pincode,
        state_id=state_id,
        district_id=district_id,
        mandal_id=mandal_id,
        area_id=area_id,
    )
    res_state_id = resolved_loc["state_id"]
    res_dist_id = resolved_loc["district_id"]
    res_mandal_id = resolved_loc["mandal_id"]
    res_area_id = resolved_loc["area_id"]
    res_pincode = resolved_loc["pincode"]

    if res_dist_id is None:
        raise ValueError(
            "A valid district and location (via 6-digit pincode or State -> District -> Area) are required."
        )

    if not clean_location:
        parts = [p for p in (resolved_loc.get("area_name"), resolved_loc.get("district_name"), res_pincode) if p]
        clean_location = ", ".join(parts) or "Location recorded"

    eff_lat = float(latitude) if latitude is not None else resolved_loc.get("latitude")
    eff_lon = float(longitude) if longitude is not None else resolved_loc.get("longitude")

    file_list = list(files) if files else []
    if len(file_list) > MAX_IMAGES_PER_COMPLAINT:
        raise ValueError(f"Maximum {MAX_IMAGES_PER_COMPLAINT} photos allowed per complaint.")

    # Validate & strip EXIF/GPS before touching the database
    prepared_images = prepare_images(file_list)

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

        _, rule_priority = classify(clean_text)
        ml_pred = predict_complaint(clean_text, department_hint=dept["name"])
        priority = "High" if (rule_priority == "High" or ml_pred.get("emergency_override")) else ml_pred["priority"]
        ml_suggested_dept = ml_pred.get("department")
        ml_confidence = float(ml_pred.get("confidence", 0.0))
        ml_top_terms_json = json.dumps(ml_pred.get("top_terms", []))
        conf_threshold = get_env_float("ML_CONFIDENCE_THRESHOLD", 0.60)

        duration_hours = ml_pred.get("duration_hours")
        duration_matched = str(ml_pred.get("duration_matched_text") or "")
        duration_conf = ml_pred.get("duration_confidence")
        is_recurring = 1 if ml_pred.get("is_recurring") else 0
        priority_reason = str(ml_pred.get("priority_reason") or "")
        low_dur_conf = bool(ml_pred.get("low_duration_confidence"))

        if resolved_ward_id is not None:
            w_row = conn.execute("SELECT id FROM wards WHERE id = ?", (resolved_ward_id,)).fetchone()
            if w_row is None:
                raise ValueError("Selected ward does not exist.")

        due_at = compute_due_at(conn, department_id, priority, now_dt)

        # Always route to 'Awaiting Assignment' for Admin review; never auto-assign.
        initial_status = "Awaiting Assignment"
        assignee = None
        assigned_to = None
        assigned_at = None

        cursor = conn.execute(
            """
            INSERT INTO complaints
                (incident_id, user_id, text, location, department_id, ward_id,
                 state_id, district_id, mandal_id, area_id, pincode,
                 latitude, longitude,
                 priority, status, assigned_to, assigned_at, due_at, escalation_level, reopen_count,
                 is_deleted, ml_suggested_department, ml_confidence, ml_top_terms,
                 duration_hours, duration_matched_text, duration_confidence, is_recurring, priority_reason,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                actor["id"],
                clean_text,
                clean_location,
                department_id,
                resolved_ward_id,
                res_state_id,
                res_dist_id,
                res_mandal_id,
                res_area_id,
                res_pincode,
                float(eff_lat) if eff_lat is not None else None,
                float(eff_lon) if eff_lon is not None else None,
                priority,
                initial_status,
                assigned_to,
                assigned_at,
                due_at,
                ml_suggested_dept,
                ml_confidence,
                ml_top_terms_json,
                duration_hours,
                duration_matched,
                duration_conf,
                is_recurring,
                priority_reason,
                now_str,
                now_str,
            ),
        )
        complaint_id = cursor.lastrowid

        # Flag low ML confidence, citizen-vs-ML department disagreement, or low duration confidence
        review_reasons: list[str] = []
        if ml_confidence < conf_threshold:
            review_reasons.append("Low ML confidence")
        if ml_suggested_dept and ml_suggested_dept.strip().lower() != dept["name"].strip().lower():
            review_reasons.append("Citizen-ML department mismatch")
        if low_dur_conf:
            review_reasons.append("Low duration confidence")
        if review_reasons:
            conn.execute(
                """
                INSERT INTO review_queue
                    (complaint_id, reason, ml_suggested_department, ml_confidence,
                     citizen_department, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'Pending', ?)
                """,
                (
                    complaint_id,
                    "; ".join(review_reasons),
                    ml_suggested_dept,
                    ml_confidence,
                    dept["name"],
                    now_str,
                ),
            )

        if low_dur_conf:
            conn.execute(
                """
                INSERT INTO duration_extraction_logs
                    (complaint_id, incident_id, text, found_hours, matched_text,
                     confidence, status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'Low Confidence', 'Flagged vague or ambiguous duration phrasing', ?)
                """,
                (complaint_id, incident_id, clean_text, duration_hours, duration_matched, duration_conf, now_str),
            )
        elif looks_like_unparsed_duration(clean_text):
            conn.execute(
                """
                INSERT INTO duration_extraction_logs
                    (complaint_id, incident_id, text, found_hours, matched_text,
                     confidence, status, reason, created_at)
                VALUES (?, ?, ?, NULL, '', NULL, 'Failed', 'Time cue detected without valid duration extraction', ?)
                """,
                (complaint_id, incident_id, clean_text, now_str),
            )

        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'department', '', ?, '', ?, 'citizen_selected', ?, ?)
            """,
            (
                complaint_id,
                actor["id"],
                actor.get("username", ""),
                initial_status,
                dept["name"],
                f"Submitted under {dept['name']} (priority: {priority}, status: {initial_status})",
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
            conn, actor, complaint_id, initial_status, prepared_images, now=now_str
        )

    return incident_id


def get_complaints(user: dict | None, now: datetime | None = None) -> list[dict]:
    """
    - 'user' role: requires 'view_own', returns ONLY their own non-deleted complaints.
    - 'employee' role: sees ONLY tasks assigned to them (plus legacy district-less unassigned queue).
    - 'admin' role: scoped strictly to their assigned district (+ optional department) jurisdictions.
    - 'super_admin' role: returns ALL non-deleted complaints statewide.
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
            role = user.get("role")
            if role == "employee":
                dept_id = user.get("department_id")
                if dept_id is None:
                    return []
                emp_dist_id = user.get("district_id")
                if emp_dist_id is not None:
                    rows = conn.execute(
                        _BASE_SELECT
                        + " WHERE c.is_deleted = 0 AND c.department_id = ? "
                        "AND (c.district_id IS NULL OR c.district_id = ?) "
                        "AND (c.assigned_to = ? OR (c.assigned_to IS NULL AND COALESCE(dist.allow_self_claim, 0) = 1 AND c.status NOT IN ('Resolved', 'Closed'))) "
                        "ORDER BY c.created_at DESC, c.id DESC",
                        (dept_id, int(emp_dist_id), user["id"]),
                    ).fetchall()
                else:
                    # District-less employee: sees assigned tasks + legacy unassigned district-less tasks only
                    rows = conn.execute(
                        _BASE_SELECT
                        + " WHERE c.is_deleted = 0 AND c.department_id = ? "
                        "AND (c.assigned_to = ? OR (c.assigned_to IS NULL AND c.district_id IS NULL)) "
                        "ORDER BY c.created_at DESC, c.id DESC",
                        (dept_id, user["id"]),
                    ).fetchall()
            elif role == "admin":
                scopes = _get_admin_jurisdictions_in_conn(conn, user)
                if not scopes:
                    rows = conn.execute(
                        _BASE_SELECT
                        + " WHERE c.is_deleted = 0 AND c.district_id IS NULL "
                        "ORDER BY c.created_at DESC, c.id DESC"
                    ).fetchall()
                else:
                    all_rows = conn.execute(
                        _BASE_SELECT + " WHERE c.is_deleted = 0 ORDER BY c.created_at DESC, c.id DESC"
                    ).fetchall()
                    rows = [
                        r for r in all_rows
                        if r["district_id"] is not None
                        and any(
                            d_id == r["district_id"] and (dep_id is None or dep_id == r["department_id"])
                            for d_id, dep_id in scopes
                        )
                    ]
            else:
                rows = conn.execute(
                    _BASE_SELECT
                    + " WHERE c.is_deleted = 0 ORDER BY c.created_at DESC, c.id DESC"
                ).fetchall()

    counts_map = get_image_counts_map([r["id"] for r in rows])
    return [_row_to_complaint(r, photo_count=counts_map.get(r["id"], 0), now=now) for r in rows]


def get_complaint_detail(user: dict | None, complaint_id: int, now: datetime | None = None) -> dict:
    """
    Fetch one non-deleted complaint with strict role, department, assignment & jurisdiction checks.
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
        role = user.get("role")
        if role == "employee":
            if user.get("department_id") is None or row["department_id"] != user.get("department_id"):
                raise PermissionError("Employees can only access complaints in their own department.")
            if row["assigned_to"] is not None and row["assigned_to"] != user.get("id"):
                raise PermissionError("Employees cannot view tasks assigned to other employees.")
            if row["district_id"] is not None:
                if user.get("district_id") is None or int(user["district_id"]) != int(row["district_id"]):
                    raise PermissionError("Complaint is outside your district jurisdiction.")
                if row["assigned_to"] != user.get("id"):
                    can_see_unassigned = (
                        row["assigned_to"] is None
                        and bool(row["district_allow_self_claim"])
                        and row["status"] not in ("Resolved", "Closed")
                    )
                    if not can_see_unassigned:
                        raise PermissionError("Employees can only view tasks assigned to them.")
        elif role == "admin":
            _assert_admin_jurisdiction(conn, user, row["district_id"], row["department_id"])

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


def get_department_employees(
    actor: dict | None,
    department_id: int,
    district_id: int | None = None,
    mandal_id: int | None = None,
) -> list[dict]:
    """Return active employees matching department_id (and district/mandal jurisdiction when scoped)."""
    require(actor, "assign_task")
    where = ["u.role = 'employee'", "u.is_active = 1", "u.department_id = ?"]
    params: list = [department_id]
    if district_id is not None:
        where.append("u.district_id = ?")
        params.append(int(district_id))
    else:
        where.append("u.district_id IS NULL")
    if mandal_id is not None:
        where.append("(u.mandal_id IS NULL OR u.mandal_id = ?)")
        params.append(int(mandal_id))

    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            f"""
            SELECT u.id, u.username, u.full_name, u.district_id, u.mandal_id,
                   COUNT(c.id) AS open_count
            FROM users u
            LEFT JOIN complaints c
                   ON c.assigned_to = u.id
                  AND c.is_deleted = 0
                  AND c.status NOT IN ('Resolved', 'Closed')
            WHERE {' AND '.join(where)}
            GROUP BY u.id, u.username, u.full_name, u.district_id, u.mandal_id
            ORDER BY open_count ASC, u.id ASC
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_suggested_employee(actor: dict | None, complaint_id: int) -> dict | None:
    """Return recommended employee (matching department + jurisdiction, fewest open tasks)."""
    require(actor, "assign_task")
    with closing(_db.get_connection()) as conn:
        comp = conn.execute(
            "SELECT department_id, district_id, mandal_id FROM complaints WHERE id = ? AND is_deleted = 0",
            (complaint_id,),
        ).fetchone()
        if comp is None:
            return None
        _assert_admin_jurisdiction(conn, actor, comp["district_id"], comp["department_id"])
        return _pick_least_loaded_employee(
            conn,
            comp["department_id"],
            district_id=comp["district_id"],
            mandal_id=comp["mandal_id"],
        )


def claim_complaint(actor: dict | None, complaint_id: int) -> dict:
    """
    Allow an active employee to claim an unassigned complaint in their own department
    and district ONLY when an Admin/Super Admin has enabled allow_self_claim for that district.
    Disabled by default (self-claim forbidden outside admin assignment flow).
    """
    require(actor, "claim_complaint")

    now_str = _fmt_dt(_utc_now())
    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            """
            SELECT c.id, c.department_id, c.district_id, c.mandal_id, c.status,
                   c.assigned_to, c.is_deleted,
                   COALESCE(d.allow_self_claim, 0) AS allow_self_claim
            FROM complaints c
            LEFT JOIN districts d ON d.id = c.district_id
            WHERE c.id = ?
            """,
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")
        if not int(row["allow_self_claim"] or 0):
            raise PermissionError(
                "Self-claim is disabled for this district; tasks must be assigned by an Admin."
            )
        if actor.get("department_id") is None or row["department_id"] != actor.get("department_id"):
            raise PermissionError("You can only claim complaints in your own department.")
        if row["district_id"] is not None:
            if actor.get("district_id") is None or int(actor["district_id"]) != int(row["district_id"]):
                raise PermissionError("You can only claim complaints in your own district.")
            if actor.get("mandal_id") is not None and row["mandal_id"] is not None:
                if int(actor["mandal_id"]) != int(row["mandal_id"]):
                    raise PermissionError("You can only claim complaints in your own mandal.")
        if row["assigned_to"] is not None:
            raise ValueError("Complaint is already assigned.")
        if row["status"] in ("Resolved", "Closed"):
            raise ValueError("Cannot claim a Resolved or Closed complaint.")

        old_status = row["status"]
        new_status = "Assigned" if old_status in ("Awaiting Assignment", "Pending", "Submitted") else old_status

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


def assign_task(
    actor: dict | None,
    complaint_id: int,
    employee_id: int | None,
    due_at: str | None = None,
    instructions: str = "",
    reason: str = "",
) -> dict:
    """
    Hierarchical assignment: Admin (within own jurisdiction) or Super Admin assigns/reassigns
    a complaint to an active employee in the matching department & district/mandal, sets optional
    due date and instructions, moves status to 'Assigned', and audit-logs assigned_by, assigned_to,
    time, and reason. Passing employee_id=None returns it to 'Awaiting Assignment' (if districted).
    """
    require(actor, "assign_task")

    clean_instructions = sanitize_text(instructions or "", max_length=500, field_name="Instructions")
    clean_reason = sanitize_text(reason or "", max_length=500, field_name="Assignment reason")
    now_str = _fmt_dt(_utc_now())

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            """
            SELECT c.id, c.department_id, c.district_id, c.mandal_id, c.status,
                   c.assigned_to, c.due_at, c.is_deleted,
                   u.username AS old_emp_username
            FROM complaints c
            LEFT JOIN users u ON u.id = c.assigned_to
            WHERE c.id = ?
            """,
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")

        _assert_admin_jurisdiction(conn, actor, row["district_id"], row["department_id"])

        new_emp_username = "Unassigned"
        if employee_id is not None:
            emp = conn.execute(
                "SELECT id, username, role, department_id, district_id, mandal_id, is_active "
                "FROM users WHERE id = ?",
                (employee_id,),
            ).fetchone()
            if emp is None or not emp["is_active"] or emp["role"] != "employee":
                raise ValueError("Assignee must be an active employee account.")
            if emp["department_id"] != row["department_id"]:
                raise ValueError("Employee must belong to the complaint's current department.")
            if row["district_id"] is not None:
                if emp["district_id"] is None or int(emp["district_id"]) != int(row["district_id"]):
                    raise ValueError("Employee must belong to the complaint's district jurisdiction.")
                if emp["mandal_id"] is not None and row["mandal_id"] is not None:
                    if int(emp["mandal_id"]) != int(row["mandal_id"]):
                        raise ValueError("Employee mandal jurisdiction does not match the complaint.")
            elif emp["district_id"] is not None:
                raise ValueError("District-scoped employee cannot be assigned to a district-less complaint.")
            new_emp_username = emp["username"]

        old_emp_username = row["old_emp_username"] or "Unassigned"
        assigned_at = now_str if employee_id is not None else None
        assigned_by = actor["id"] if employee_id is not None else None
        old_status = row["status"]
        if employee_id is not None:
            new_status = "Assigned" if old_status in ("Submitted", "Awaiting Assignment", "Pending", "Reopened") else old_status
        else:
            new_status = "Awaiting Assignment" if row["district_id"] is not None else "Pending"

        target_due = due_at.strip() if isinstance(due_at, str) and due_at.strip() else row["due_at"]

        conn.execute(
            """
            UPDATE complaints
            SET assigned_to = ?, assigned_by = ?, assigned_at = ?, accepted_at = NULL,
                assignment_instructions = ?, due_at = ?, status = ?,
                unassigned_escalated = 0, updated_at = ?
            WHERE id = ?
            """,
            (
                employee_id,
                assigned_by,
                assigned_at,
                clean_instructions,
                target_due,
                new_status,
                now_str,
                complaint_id,
            ),
        )
        audit_note = clean_reason or f"Assigned by {actor.get('username', '')} to {new_emp_username}"
        if clean_instructions:
            audit_note += f" | Instructions: {clean_instructions}"

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
                old_status,
                new_status,
                old_emp_username,
                new_emp_username,
                audit_note,
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
                    audit_note,
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
    """Backward-compatible wrapper around assign_task preserving original status when district_id is None."""
    require(actor, "assign_complaint")
    clean_remark = sanitize_text(remark, max_length=500, field_name="Assignment remark")
    return assign_task(
        actor,
        complaint_id,
        employee_id,
        reason=clean_remark,
    )


def accept_task(actor: dict | None, complaint_id: int) -> dict:
    """Employee accepts a task assigned to them, recording accepted_at and moving to In Progress."""
    require(actor, "accept_task")
    now_str = _fmt_dt(_utc_now())
    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT id, status, assigned_to, department_id, district_id, is_deleted "
            "FROM complaints WHERE id = ?",
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")
        if row["assigned_to"] != actor.get("id"):
            raise PermissionError("You can only accept tasks assigned to you.")
        if row["status"] in ("Resolved", "Closed"):
            raise ValueError("Cannot accept an already Resolved or Closed complaint.")

        old_status = row["status"]
        new_status = "In Progress" if old_status in ("Assigned", "Pending", "Reopened") else old_status
        conn.execute(
            "UPDATE complaints SET accepted_at = ?, status = ?, updated_at = ? WHERE id = ?",
            (now_str, new_status, now_str, complaint_id),
        )
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
                f"Task accepted by {actor.get('username', '')}",
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
    override_reason: str = "",
    after_files: list | tuple | None = None,
) -> None:
    """
    Update complaint status/remark (employee only when assigned to them; admin within
    own jurisdiction; super_admin on all) and/or department/priority (admin & super_admin ONLY).
    - Admin / Super Admin overriding status requires a mandatory override reason (>= 5 chars).
    - Resolving requires a closing remark >= 10 characters.
    - Changing priority or department recalculates SLA due_at.
    """
    require(user, "update_task_status")

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
    clean_override = sanitize_text(override_reason or "", max_length=500, field_name="Override reason")
    effective_reason = clean_override or clean_remark

    if new_status == "Resolved" and len(clean_remark) < 10 and len(clean_override) < 10:
        raise ValueError(
            "Resolving a complaint requires a closing remark of at least 10 characters."
        )

    after_list = list(after_files) if after_files else []
    prepared_after = prepare_images(after_list) if after_list else []

    now_dt = _utc_now()
    now_str = _fmt_dt(now_dt)

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            """
            SELECT c.incident_id, c.text, c.duration_hours, c.duration_matched_text,
                   c.status, c.department_id, c.district_id, c.priority, c.assigned_to,
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
            if row["district_id"] is not None and (
                user.get("district_id") is None or int(user["district_id"]) != int(row["district_id"])
            ):
                raise PermissionError("Employees can only update complaints in their jurisdiction.")
        elif user.get("role") == "admin":
            _assert_admin_jurisdiction(conn, user, row["district_id"], row["department_id"])

        old_status = row["status"]
        old_dept_id = row["department_id"]
        old_dept_name = row["dept_name"]
        old_priority = row["priority"]

        target_status = new_status if new_status is not None else old_status
        target_dept_id = department_id if department_id is not None else old_dept_id
        target_priority = priority if priority is not None else old_priority

        is_admin_override = (
            user.get("role") in ("admin", "super_admin")
            and new_status is not None
            and target_status != old_status
        )
        if is_admin_override and len(effective_reason) < 5:
            raise ValueError("Admin status override requires a mandatory reason (at least 5 characters).")

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

        # Department change always clears assignment and returns to Awaiting Assignment
        target_assigned_to = row["assigned_to"]
        target_assigned_at = None
        new_assignee = None
        dept_changed = target_dept_id != old_dept_id
        if dept_changed:
            target_assigned_to = None
            target_assigned_at = None
            if target_status in ("Assigned", "In Progress", "Pending"):
                target_status = "Awaiting Assignment"

        audit_rows: list[tuple[str, str, str, str, str]] = []
        status_src = "admin_override" if is_admin_override else "staff_update"
        if target_status != old_status or (
            not dept_changed and target_priority == old_priority and effective_reason
        ):
            audit_rows.append(("status", old_status, target_status, effective_reason, status_src))

        if dept_changed:
            audit_rows.append(("department", old_dept_name, new_dept_name, effective_reason, "staff_update"))

        if target_priority != old_priority:
            audit_rows.append(("priority", old_priority, target_priority, effective_reason, "staff_update"))
            conn.execute(
                """
                INSERT INTO priority_feedback_log
                    (complaint_id, incident_id, text, duration_hours, matched_text,
                     corrected_priority, corrected_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    complaint_id,
                    row["incident_id"],
                    row["text"],
                    row["duration_hours"],
                    row["duration_matched_text"] or "",
                    target_priority,
                    user["id"],
                    now_str,
                ),
            )

        if not audit_rows:
            audit_rows.append(("status", old_status, target_status, effective_reason, status_src))

        for field_name, old_val, new_val, row_remark, row_src in audit_rows:
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    row_src,
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
                        f"Updated assignment after department change to {new_dept_name}",
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

        if prepared_after:
            existing_after = conn.execute(
                "SELECT COUNT(*) FROM complaint_images WHERE complaint_id = ? AND is_removed = 0 AND photo_stage = 'after'",
                (complaint_id,),
            ).fetchone()[0]
            if existing_after + len(prepared_after) > MAX_IMAGES_PER_COMPLAINT:
                raise ValueError(
                    f"Maximum {MAX_IMAGES_PER_COMPLAINT} after-photos allowed per complaint."
                )
            save_prepared_images_in_conn(
                conn, user, complaint_id, target_status, prepared_after, now=now_str, photo_stage="after"
            )


def update_task_status(
    actor: dict | None,
    complaint_id: int,
    new_status: str | None = None,
    remark: str = "",
    override_reason: str = "",
    after_files: list | tuple | None = None,
    department_id: int | None = None,
    priority: str | None = None,
) -> dict:
    """Service API for employee status/remark/after-photo updates or admin status overrides."""
    update_complaint(
        actor,
        complaint_id,
        new_status=new_status,
        remark=remark,
        department_id=department_id,
        priority=priority,
        override_reason=override_reason,
        after_files=after_files,
    )
    return get_complaint_detail(actor, complaint_id)


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
