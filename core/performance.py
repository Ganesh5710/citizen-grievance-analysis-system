"""
Performance analytics per department, per employee, and individual employee self-summary.
"""

from contextlib import closing

import core.database as _db
from core.permissions import require
from core.sla import parse_utc


def _compute_group_metrics(rows: list, feedback_map: dict[int, list[int]]) -> dict:
    handled = len(rows)
    resolved_rows = [r for r in rows if r["status"] in ("Resolved", "Closed")]
    resolved_count = len(resolved_rows)
    open_count = handled - resolved_count

    res_hours: list[float] = []
    sla_total = 0
    sla_met = 0
    for r in resolved_rows:
        created_dt = parse_utc(r["created_at"])
        resolved_dt = parse_utc(r["resolved_at"])
        due_dt = parse_utc(r["due_at"])
        if created_dt is not None and resolved_dt is not None:
            hrs = max(0.0, (resolved_dt - created_dt).total_seconds() / 3600.0)
            res_hours.append(hrs)
        if resolved_dt is not None and due_dt is not None:
            sla_total += 1
            if resolved_dt <= due_dt:
                sla_met += 1

    reopened_count = sum(1 for r in rows if int(r["reopen_count"] or 0) > 0)

    ratings: list[int] = []
    for r in rows:
        ratings.extend(feedback_map.get(r["id"], []))

    return {
        "handled":              handled,
        "resolved":             resolved_count,
        "open":                 open_count,
        "avg_resolution_hours": round(sum(res_hours) / len(res_hours), 2) if res_hours else None,
        "sla_met_pct":          round(100.0 * sla_met / sla_total, 1) if sla_total > 0 else None,
        "reopen_rate_pct":      round(100.0 * reopened_count / handled, 1) if handled > 0 else 0.0,
        "avg_rating":           round(sum(ratings) / len(ratings), 2) if ratings else None,
        "rating_count":         len(ratings),
    }


def _load_feedback_map(conn) -> dict[int, list[int]]:
    fb_rows = conn.execute("SELECT complaint_id, rating FROM complaint_feedback").fetchall()
    fb_map: dict[int, list[int]] = {}
    for fb in fb_rows:
        fb_map.setdefault(fb["complaint_id"], []).append(int(fb["rating"]))
    return fb_map


def get_performance_metrics(actor: dict | None) -> dict[str, list[dict]]:
    """
    Return per-department and per-employee performance metrics (Admin / Super Admin only).
    """
    require(actor, "assign_complaint")

    with closing(_db.get_connection()) as conn:
        depts = conn.execute(
            "SELECT id, name, is_active FROM departments ORDER BY id ASC"
        ).fetchall()
        emps = conn.execute(
            """
            SELECT u.id, u.username, u.full_name, u.is_active,
                   COALESCE(d.name, 'Unassigned') AS department_name
            FROM users u
            LEFT JOIN departments d ON d.id = u.department_id
            WHERE u.role = 'employee'
            ORDER BY u.id ASC
            """
        ).fetchall()
        complaints = conn.execute(
            """
            SELECT id, department_id, assigned_to, status, created_at, resolved_at, due_at, reopen_count
            FROM complaints
            WHERE is_deleted = 0
            """
        ).fetchall()
        fb_map = _load_feedback_map(conn)

    by_dept: list[dict] = []
    for d in depts:
        d_rows = [c for c in complaints if c["department_id"] == d["id"]]
        metrics = _compute_group_metrics(d_rows, fb_map)
        by_dept.append({
            "department_id":        d["id"],
            "department":           d["name"],
            "department_is_active": bool(d["is_active"]),
            **metrics,
        })

    by_emp: list[dict] = []
    for e in emps:
        e_rows = [c for c in complaints if c["assigned_to"] == e["id"]]
        metrics = _compute_group_metrics(e_rows, fb_map)
        by_emp.append({
            "employee_id": e["id"],
            "username":    e["username"],
            "full_name":   e["full_name"],
            "department":  e["department_name"],
            "is_active":   bool(e["is_active"]),
            **metrics,
        })

    return {
        "by_department": by_dept,
        "by_employee":   by_emp,
    }


def get_employee_own_performance(actor: dict | None) -> dict:
    """Return performance summary solely for the signed-in employee."""
    require(actor, "update_status_remark")
    if actor.get("role") != "employee":
        raise PermissionError("Own performance summary is intended for employee accounts.")

    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT id, department_id, assigned_to, status, created_at, resolved_at, due_at, reopen_count
            FROM complaints
            WHERE is_deleted = 0 AND assigned_to = ?
            """,
            (actor["id"],),
        ).fetchall()
        fb_map = _load_feedback_map(conn)

    return _compute_group_metrics(rows, fb_map)
