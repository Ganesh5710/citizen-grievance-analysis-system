"""
core/oversight.py
Super Admin monitoring & control:
  - Unassigned-beyond-threshold escalation (default 4h) + Super Admin notifications
  - Admin Oversight metrics (awaiting assignment, avg time to assign, overdue in district,
    reassignment count, override count)
  - Audit log queries filtered by admin or district
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from core.config import get_env_float
import core.database as _db
from core.permissions import require
from core.sla import parse_utc


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def run_unassigned_escalation_check(
    now: datetime | None = None,
    threshold_hours: float | None = None,
) -> list[dict[str, Any]]:
    """
    Escalate open complaints that have remained unassigned beyond `threshold_hours`
    (default 4h via CGS_UNASSIGNED_ESCALATION_HOURS) to Super Admin with a notification
    and a system audit row.
    """
    limit_hrs = (
        float(threshold_hours)
        if threshold_hours is not None
        else get_env_float("CGS_UNASSIGNED_ESCALATION_HOURS", 4.0)
    )
    now_dt = _utc_now(now)
    now_str = _fmt_dt(now_dt)
    escalated_items: list[dict[str, Any]] = []

    with closing(_db.get_connection()) as conn, conn:
        rows = conn.execute(
            """
            SELECT c.id, c.incident_id, c.department_id, d.name AS dept_name,
                   c.district_id, dist.name AS dist_name, c.status, c.created_at
            FROM complaints c
            JOIN departments d ON d.id = c.department_id
            LEFT JOIN districts dist ON dist.id = c.district_id
            WHERE c.is_deleted = 0
              AND c.assigned_to IS NULL
              AND c.status IN ('Submitted', 'Awaiting Assignment', 'Pending', 'Reopened')
              AND COALESCE(c.unassigned_escalated, 0) = 0
            """
        ).fetchall()

        for r in rows:
            created_dt = parse_utc(r["created_at"])
            if created_dt is None:
                continue
            elapsed_hrs = (now_dt - created_dt).total_seconds() / 3600.0
            if elapsed_hrs < limit_hrs:
                continue

            conn.execute(
                """
                UPDATE complaints
                SET unassigned_escalated = 1,
                    unassigned_escalated_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now_str, now_str, r["id"]),
            )
            dist_lbl = r["dist_name"] or "Unscoped"
            msg = (
                f"Ticket #{r['incident_id']} ({r['dept_name']}, {dist_lbl}) has remained "
                f"unassigned for {elapsed_hrs:.1f}h (limit {limit_hrs:.1f}h)."
            )
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, NULL, 'system', 'unassigned_escalation', ?, ?, 'Unassigned', 'Escalated to Super Admin',
                        'system', ?, ?)
                """,
                (r["id"], r["status"], r["status"], msg, now_str),
            )
            conn.execute(
                """
                INSERT INTO notifications
                    (user_id, target_role, complaint_id, incident_id, title, message, is_read, created_at)
                VALUES (NULL, 'super_admin', ?, ?, ?, ?, 0, ?)
                """,
                (
                    r["id"],
                    r["incident_id"],
                    f"Unassigned SLA Alert: #{r['incident_id']}",
                    msg,
                    now_str,
                ),
            )
            escalated_items.append({
                "complaint_id": r["id"],
                "incident_id": r["incident_id"],
                "district": dist_lbl,
                "department": r["dept_name"],
                "elapsed_hours": round(elapsed_hrs, 2),
                "message": msg,
            })

    return escalated_items


def get_super_admin_notifications(
    actor: dict | None,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    require(actor, "view_admin_oversight")
    where = ["target_role = 'super_admin'"]
    if unread_only:
        where.append("is_read = 0")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM notifications
            WHERE {' AND '.join(where)}
            ORDER BY is_read ASC, created_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_notification_read(actor: dict | None, notification_id: int) -> None:
    require(actor, "view_admin_oversight")
    with closing(_db.get_connection()) as conn, conn:
        conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ?",
            (int(notification_id),),
        )


def mark_all_notifications_read(actor: dict | None) -> None:
    require(actor, "view_admin_oversight")
    with closing(_db.get_connection()) as conn, conn:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE target_role = 'super_admin'")


def get_admin_oversight_metrics(actor: dict | None, now: datetime | None = None) -> list[dict]:
    """
    Compute per-admin monitoring KPIs for Super Admin:
      - awaiting_assignment: open complaints in admin's district(s) awaiting assignment
      - avg_time_to_assign_hours: average hours from created_at -> assigned_at
      - overdue_in_district: open complaints in admin's district(s) past due_at
      - reassignment_count: reassignments performed by this admin
      - override_count: status overrides performed by this admin
    """
    require(actor, "view_admin_oversight")
    now_dt = _utc_now(now)

    with closing(_db.get_connection()) as conn:
        admins = conn.execute(
            "SELECT id, username, full_name, district_id, is_active FROM users WHERE role = 'admin' ORDER BY id"
        ).fetchall()
        j_rows = conn.execute(
            """
            SELECT aj.user_id, aj.district_id, d.name AS district_name, aj.department_id
            FROM admin_jurisdictions aj
            JOIN districts d ON d.id = aj.district_id
            """
        ).fetchall()
        all_comps = conn.execute(
            """
            SELECT id, district_id, department_id, status, assigned_to, assigned_by,
                   assigned_at, due_at, created_at
            FROM complaints
            WHERE is_deleted = 0
            """
        ).fetchall()
        updates = conn.execute(
            "SELECT complaint_id, employee_id, field_name, old_value, new_value, source "
            "FROM complaint_updates WHERE employee_id IS NOT NULL"
        ).fetchall()

    j_map: dict[int, list[tuple[int, str, int | None]]] = {}
    for jr in j_rows:
        j_map.setdefault(jr["user_id"], []).append((jr["district_id"], jr["district_name"], jr["department_id"]))

    dist_name_lookup: dict[int, str] = {}
    if admins:
        with closing(_db.get_connection()) as conn:
            for dr in conn.execute("SELECT id, name FROM districts").fetchall():
                dist_name_lookup[dr["id"]] = dr["name"]

    out: list[dict] = []
    for adm in admins:
        aid = adm["id"]
        scopes = j_map.get(aid, [])
        if not scopes and adm["district_id"] is not None:
            scopes = [(adm["district_id"], dist_name_lookup.get(adm["district_id"], ""), None)]

        dist_names = sorted({s[1] for s in scopes if s[1]})

        if scopes:
            scoped_comps = [
                c for c in all_comps
                if c["district_id"] is not None
                and any(
                    d_id == c["district_id"] and (dep_id is None or dep_id == c["department_id"])
                    for d_id, _, dep_id in scopes
                )
            ]
        else:
            scoped_comps = [c for c in all_comps if c["assigned_by"] == aid]

        awaiting = sum(
            1 for c in scoped_comps
            if c["status"] in ("Submitted", "Awaiting Assignment", "Pending") and c["assigned_to"] is None
        )
        overdue = 0
        assign_hours: list[float] = []
        for c in scoped_comps:
            if c["status"] not in ("Resolved", "Closed") and c["due_at"]:
                due_dt = parse_utc(c["due_at"])
                if due_dt is not None and now_dt > due_dt:
                    overdue += 1
            if c["assigned_at"] and (c["assigned_by"] == aid or scopes):
                cr_dt = parse_utc(c["created_at"])
                as_dt = parse_utc(c["assigned_at"])
                if cr_dt is not None and as_dt is not None:
                    assign_hours.append(max(0.0, (as_dt - cr_dt).total_seconds() / 3600.0))

        reassignments = sum(
            1 for u in updates
            if u["employee_id"] == aid
            and u["field_name"] == "assigned_to"
            and (u["old_value"] or "Unassigned") != "Unassigned"
        )
        overrides = sum(
            1 for u in updates
            if u["employee_id"] == aid and u["source"] == "admin_override"
        )

        out.append({
            "admin_id": aid,
            "username": adm["username"],
            "full_name": adm["full_name"],
            "is_active": bool(adm["is_active"]),
            "districts": ", ".join(dist_names) if dist_names else "Unassigned",
            "awaiting_assignment": awaiting,
            "avg_time_to_assign_hours": round(sum(assign_hours) / len(assign_hours), 2) if assign_hours else None,
            "overdue_in_district": overdue,
            "reassignment_count": reassignments,
            "override_count": overrides,
        })

    return out


def get_audit_logs_filtered(
    actor: dict | None,
    admin_user_id: int | None = None,
    district_id: int | None = None,
    limit: int = 150,
) -> list[dict]:
    """Return complaint audit rows filtered by admin user and/or district (super_admin only)."""
    require(actor, "view_admin_oversight")
    where = ["1=1"]
    params: list[Any] = []
    if admin_user_id is not None:
        where.append("cu.employee_id = ?")
        params.append(int(admin_user_id))
    if district_id is not None:
        where.append("c.district_id = ?")
        params.append(int(district_id))
    params.append(int(limit))

    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            f"""
            SELECT cu.id, c.incident_id, d.name AS department_name,
                   COALESCE(dist.name, '') AS district_name,
                   COALESCE(u.username, NULLIF(cu.actor_label, ''), 'system') AS actor_username,
                   COALESCE(u.role, 'system') AS actor_role,
                   cu.field_name, cu.old_value, cu.new_value, cu.source, cu.remark, cu.created_at
            FROM complaint_updates cu
            JOIN complaints c ON c.id = cu.complaint_id
            JOIN departments d ON d.id = c.department_id
            LEFT JOIN districts dist ON dist.id = c.district_id
            LEFT JOIN users u ON u.id = cu.employee_id
            WHERE {' AND '.join(where)}
            ORDER BY cu.created_at DESC, cu.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]
