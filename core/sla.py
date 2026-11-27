"""
SLA policies, due-date computation, escalation checks (Level 1 / Level 2),
7-day resolved auto-close, and escalation acknowledgements.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone

import core.database as _db
from core.permissions import require
from core.security import sanitize_text

VALID_PRIORITIES = ("High", "Medium", "Low")
AUTO_CLOSE_DAYS = 7


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_utc(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def get_target_hours(conn, department_id: int, priority: str) -> int:
    """Look up target SLA hours for (department_id, priority)."""
    row = conn.execute(
        "SELECT target_hours FROM sla_policies WHERE department_id = ? AND priority = ?",
        (department_id, priority),
    ).fetchone()
    if row is not None:
        return int(row["target_hours"])
    return int(_db.DEFAULT_SLA_HOURS.get(priority, 48))


def compute_due_at(conn, department_id: int, priority: str, base_dt: datetime | str | None = None) -> str:
    """Return due_at UTC timestamp string = base_dt + SLA target_hours."""
    if isinstance(base_dt, str):
        parsed = parse_utc(base_dt) or _utc_now()
    elif isinstance(base_dt, datetime):
        parsed = _utc_now(base_dt)
    else:
        parsed = _utc_now()
    hours = get_target_hours(conn, department_id, priority)
    return _fmt_dt(parsed + timedelta(hours=hours))


def get_sla_policies(actor: dict | None) -> list[dict]:
    """Return all SLA policies joined with department names (staff view_all)."""
    require(actor, "view_all")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT sp.id,
                   sp.department_id,
                   d.name AS department_name,
                   d.is_active AS department_is_active,
                   sp.priority,
                   sp.target_hours,
                   sp.updated_at
            FROM sla_policies sp
            JOIN departments d ON d.id = sp.department_id
            ORDER BY d.id ASC,
                     CASE sp.priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END
            """
        ).fetchall()
    return [
        {
            "id":                   r["id"],
            "department_id":        r["department_id"],
            "department":           r["department_name"],
            "department_is_active": bool(r["department_is_active"]),
            "priority":             r["priority"],
            "target_hours":         int(r["target_hours"]),
            "updated_at":           r["updated_at"],
        }
        for r in rows
    ]


def update_sla_policy(
    actor: dict | None,
    department_id: int,
    priority: str,
    target_hours: int,
) -> dict:
    """Create or update an SLA target_hours policy (super_admin only)."""
    require(actor, "manage_sla")

    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority '{priority}'.")
    if not isinstance(target_hours, int) or isinstance(target_hours, bool) or target_hours <= 0:
        raise ValueError("Target hours must be a positive integer.")
    if target_hours > 8760:
        raise ValueError("Target hours cannot exceed 8760 (1 year).")

    now_str = _fmt_dt(_utc_now())
    with closing(_db.get_connection()) as conn, conn:
        dept = conn.execute(
            "SELECT id, name, is_active FROM departments WHERE id = ?",
            (department_id,),
        ).fetchone()
        if dept is None:
            raise ValueError(f"Department with id {department_id} not found.")

        conn.execute(
            """
            INSERT INTO sla_policies (department_id, priority, target_hours, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(department_id, priority)
            DO UPDATE SET target_hours = excluded.target_hours, updated_at = excluded.updated_at
            """,
            (department_id, priority, target_hours, now_str),
        )
        row = conn.execute(
            "SELECT * FROM sla_policies WHERE department_id = ? AND priority = ?",
            (department_id, priority),
        ).fetchone()

    return {
        "id":                   row["id"],
        "department_id":        row["department_id"],
        "department":           dept["name"],
        "department_is_active": bool(dept["is_active"]),
        "priority":             row["priority"],
        "target_hours":         int(row["target_hours"]),
        "updated_at":           row["updated_at"],
    }


def run_escalation_check(now: datetime | None = None) -> dict[str, int]:
    """
    Evaluate all open complaints for SLA breach escalations (Level 1 / Level 2)
    and auto-close Resolved complaints older than 7 days without citizen feedback.
    Every automated change writes an audit row with actor = 'system'.
    """
    now_dt = _utc_now(now)
    now_str = _fmt_dt(now_dt)
    escalated_1 = 0
    escalated_2 = 0
    auto_closed = 0

    with closing(_db.get_connection()) as conn, conn:
        open_rows = conn.execute(
            """
            SELECT id, status, due_at, escalation_level
            FROM complaints
            WHERE is_deleted = 0
              AND status NOT IN ('Resolved', 'Closed')
              AND due_at IS NOT NULL
            """
        ).fetchall()

        for r in open_rows:
            due_dt = parse_utc(r["due_at"])
            if due_dt is None:
                continue
            overdue_secs = (now_dt - due_dt).total_seconds()
            if overdue_secs > 24 * 3600:
                target_level = 2
            elif overdue_secs > 0:
                target_level = 1
            else:
                target_level = 0

            current_level = int(r["escalation_level"] or 0)
            if target_level > current_level:
                conn.execute(
                    "UPDATE complaints SET escalation_level = ?, updated_at = ? WHERE id = ?",
                    (target_level, now_str, r["id"]),
                )
                hours_over = round(overdue_secs / 3600.0, 1)
                conn.execute(
                    """
                    INSERT INTO complaint_updates
                        (complaint_id, employee_id, actor_label, field_name,
                         old_status, new_status, old_value, new_value, source, remark, created_at)
                    VALUES (?, NULL, 'system', 'escalation_level', ?, ?, ?, ?, 'system', ?, ?)
                    """,
                    (
                        r["id"],
                        r["status"],
                        r["status"],
                        f"Level {current_level}",
                        f"Level {target_level}",
                        f"Escalated to Level {target_level} ({hours_over}h overdue)",
                        now_str,
                    ),
                )
                if target_level == 1:
                    escalated_1 += 1
                elif target_level == 2:
                    escalated_2 += 1

        resolved_rows = conn.execute(
            """
            SELECT id, resolved_at, reopen_count
            FROM complaints
            WHERE is_deleted = 0
              AND status = 'Resolved'
              AND resolved_at IS NOT NULL
            """
        ).fetchall()

        for r in resolved_rows:
            resolved_dt = parse_utc(r["resolved_at"])
            if resolved_dt is None:
                continue
            if (now_dt - resolved_dt).total_seconds() >= AUTO_CLOSE_DAYS * 86400:
                conn.execute(
                    "UPDATE complaints SET status = 'Closed', updated_at = ? WHERE id = ?",
                    (now_str, r["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO complaint_updates
                        (complaint_id, employee_id, actor_label, field_name,
                         old_status, new_status, old_value, new_value, source, remark, created_at)
                    VALUES (?, NULL, 'system', 'status', 'Resolved', 'Closed',
                            'Resolved', 'Closed', 'system', ?, ?)
                    """,
                    (
                        r["id"],
                        "Auto-closed after 7 days without citizen feedback",
                        now_str,
                    ),
                )
                auto_closed += 1

    return {
        "escalated_to_1": escalated_1,
        "escalated_to_2": escalated_2,
        "auto_closed":    auto_closed,
    }


def acknowledge_escalation(actor: dict | None, complaint_id: int, note: str) -> None:
    """Allow Admin or Super Admin to log an acknowledgement note on an escalated complaint."""
    require(actor, "assign_complaint")

    clean_note = sanitize_text(note, max_length=1000, field_name="Acknowledgement note")
    if len(clean_note) < 3:
        raise ValueError("Acknowledgement note must be at least 3 characters long.")

    now_str = _fmt_dt(_utc_now())
    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT id, status, escalation_level, is_deleted FROM complaints WHERE id = ?",
            (complaint_id,),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")

        conn.execute(
            "UPDATE complaints SET updated_at = ? WHERE id = ?",
            (now_str, complaint_id),
        )
        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'escalation_ack', ?, ?, ?, 'Acknowledged', 'staff_update', ?, ?)
            """,
            (
                complaint_id,
                actor["id"],
                actor.get("username", ""),
                row["status"],
                row["status"],
                f"Level {row['escalation_level']}",
                clean_note,
                now_str,
            ),
        )
