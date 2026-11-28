"""
notifications/service.py
Phase 3: Multi-channel (in_app + queued SMTP email) notifications for all roles,
validated/verified email gating, 3-attempt retry tracking without blocking workflows,
privacy-minimal email bodies (Ticket ID + Status only), and user notification preferences.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from email.message import EmailMessage
import re
import smtplib
from typing import Any, Callable

from core.config import get_env_bool, get_env_int, get_env_str
import core.database as _db
from core.logging_setup import get_logger

_logger = get_logger("notifications")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

MAX_EMAIL_ATTEMPTS = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_valid_email(addr: str | None) -> bool:
    return bool(addr and _EMAIL_RE.match(str(addr).strip()))


def _send_via_smtp(to_email: str, subject: str, body: str) -> None:
    """Default synchronous SMTP transport reading credentials strictly from .env."""
    host = get_env_str("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not configured (offline/queued mode).")
    port = get_env_int("SMTP_PORT", 587)
    user = get_env_str("SMTP_USER", "")
    password = get_env_str("SMTP_PASSWORD", "")
    sender = get_env_str("SMTP_FROM", "noreply@civic-portal.local")
    use_tls = get_env_bool("SMTP_USE_TLS", True)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=10) as smtp:
        if use_tls:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


def _safe_email_content(incident_id: str, event_type: str, status_label: str) -> tuple[str, str]:
    """Build privacy-minimal email text containing ONLY ticket ID, status/event, and portal link."""
    portal_url = get_env_str("PUBLIC_BASE_URL", "http://localhost:8501").rstrip("/")
    clean_inc = incident_id or "PORTAL-ALERT"
    subject = f"[Citizen Portal] Ticket #{clean_inc} - {status_label or event_type}"
    lines = [
        f"Ticket ID: #{clean_inc}",
        f"Event: {event_type}",
        f"Status: {status_label or event_type}",
    ]
    if event_type == "complaint_resolved":
        lines.append("Please log in to submit your satisfaction rating and confirm resolution within 7 days.")
    lines.append(f"Portal: {portal_url}")
    return subject, "\n".join(lines)


def deliver_pending_emails(
    *,
    limit: int = 25,
    smtp_sender: Callable[[str, str, str], None] | None = None,
) -> dict[str, int]:
    """
    Attempt delivery of queued 'email' notifications (`status='pending'`, `attempts < 3`).
    Never raises; records failed attempts up to MAX_EMAIL_ATTEMPTS before marking 'failed'.
    """
    transport = smtp_sender or _send_via_smtp
    sent = 0
    retried = 0
    failed = 0

    with closing(_db.get_connection()) as conn, conn:
        rows = conn.execute(
            """
            SELECT n.id, n.title, n.body, n.message, n.attempts,
                   u.email, u.email_verified, u.email_notifications, u.is_active
            FROM notifications n
            JOIN users u ON u.id = n.user_id
            WHERE n.channel = 'email'
              AND n.status = 'pending'
              AND n.attempts < ?
            ORDER BY n.id ASC
            LIMIT ?
            """,
            (MAX_EMAIL_ATTEMPTS, int(limit)),
        ).fetchall()

        for r in rows:
            next_attempts = int(r["attempts"] or 0) + 1
            email_addr = (r["email"] or "").strip()
            if not r["is_active"] or not r["email_notifications"] or not r["email_verified"] or not is_valid_email(email_addr):
                conn.execute(
                    "UPDATE notifications SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
                    (next_attempts, "Recipient ineligible, unverified, or opted out", r["id"]),
                )
                failed += 1
                continue

            body_txt = r["body"] or r["message"] or ""
            try:
                transport(email_addr, r["title"], body_txt)
                conn.execute(
                    "UPDATE notifications SET status = 'sent', attempts = ?, last_error = '' WHERE id = ?",
                    (next_attempts, r["id"]),
                )
                sent += 1
            except Exception as exc:
                err_msg = str(exc)[:240]
                new_status = "failed" if next_attempts >= MAX_EMAIL_ATTEMPTS else "pending"
                conn.execute(
                    "UPDATE notifications SET status = ?, attempts = ?, last_error = ? WHERE id = ?",
                    (new_status, next_attempts, err_msg, r["id"]),
                )
                if new_status == "failed":
                    failed += 1
                else:
                    retried += 1

    return {"sent": sent, "retried": retried, "failed": failed}


def queue_notification(
    user_id: int | None,
    event_type: str,
    title: str,
    in_app_body: str,
    *,
    complaint_id: int | None = None,
    incident_id: str = "",
    status_label: str = "",
    target_role: str = "",
    deliver_now: bool = True,
    smtp_sender: Callable[[str, str, str], None] | None = None,
) -> dict[str, int]:
    """
    Create in-app and/or email queue notification rows honoring user preferences
    and verified email addresses. Never blocks caller on SMTP failure.
    """
    now_str = _utc_now()
    created_ids: dict[str, int] = {}

    with closing(_db.get_connection()) as conn, conn:
        user_row = None
        if user_id is not None:
            user_row = conn.execute(
                """
                SELECT id, role, email, email_verified, email_notifications,
                       COALESCE(in_app_notifications, 1) AS in_app_notifications, is_active
                FROM users WHERE id = ?
                """,
                (int(user_id),),
            ).fetchone()
            if user_row is None or not user_row["is_active"]:
                return {}
            eff_role = target_role or user_row["role"]
        else:
            eff_role = target_role or "super_admin"

        want_in_app = True if user_row is None else bool(user_row["in_app_notifications"])
        if want_in_app:
            cur = conn.execute(
                """
                INSERT INTO notifications
                    (user_id, target_role, complaint_id, incident_id, type, title,
                     body, message, is_read, channel, status, attempts, last_error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'in_app', 'sent', 0, '', ?)
                """,
                (
                    user_id,
                    eff_role,
                    complaint_id,
                    incident_id or "",
                    event_type,
                    title,
                    in_app_body,
                    in_app_body,
                    now_str,
                ),
            )
            created_ids["in_app_id"] = int(cur.lastrowid)

        if (
            user_row is not None
            and bool(user_row["email_notifications"])
            and bool(user_row["email_verified"])
            and is_valid_email(user_row["email"])
        ):
            subj, safe_body = _safe_email_content(incident_id, event_type, status_label or title)
            cur_em = conn.execute(
                """
                INSERT INTO notifications
                    (user_id, target_role, complaint_id, incident_id, type, title,
                     body, message, is_read, channel, status, attempts, last_error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'email', 'pending', 0, '', ?)
                """,
                (
                    user_id,
                    eff_role,
                    complaint_id,
                    incident_id or "",
                    event_type,
                    subj,
                    safe_body,
                    safe_body,
                    now_str,
                ),
            )
            created_ids["email_id"] = int(cur_em.lastrowid)

    if deliver_now and "email_id" in created_ids:
        try:
            deliver_pending_emails(limit=10, smtp_sender=smtp_sender)
        except Exception as exc:
            _logger.warning("Email queue delivery error ignored: %s", exc)

    return created_ids


def emit_complaint_event(
    event_type: str,
    complaint_id: int,
    *,
    status_label: str = "",
    detail_note: str = "",
    smtp_sender: Callable[[str, str, str], None] | None = None,
) -> None:
    """
    Dispatch notifications to the appropriate participants for complaint lifecycle events:
      complaint_created, complaint_assigned, task_accepted, status_changed,
      complaint_resolved, complaint_reopened, complaint_escalated, complaint_transferred
    """
    try:
        with closing(_db.get_connection()) as conn:
            comp = conn.execute(
                """
                SELECT c.id, c.incident_id, c.user_id, c.department_id, c.district_id,
                       c.assigned_to, c.status, d.name AS department_name
                FROM complaints c
                JOIN departments d ON d.id = c.department_id
                WHERE c.id = ?
                """,
                (int(complaint_id),),
            ).fetchone()
            if comp is None:
                return

            admin_ids: list[int] = []
            if comp["district_id"] is not None:
                a_rows = conn.execute(
                    """
                    SELECT DISTINCT aj.user_id
                    FROM admin_jurisdictions aj
                    JOIN users u ON u.id = aj.user_id
                    WHERE aj.district_id = ?
                      AND u.role = 'admin' AND u.is_active = 1
                      AND (aj.department_id IS NULL OR aj.department_id = ?)
                    """,
                    (comp["district_id"], comp["department_id"]),
                ).fetchall()
                admin_ids = [int(r["user_id"]) for r in a_rows]

        inc = comp["incident_id"]
        st_lbl = status_label or comp["status"]
        reporter_id = int(comp["user_id"])
        assignee_id = int(comp["assigned_to"]) if comp["assigned_to"] is not None else None

        targets: list[tuple[int | None, str, str, str]] = []
        if event_type == "complaint_created":
            targets.append((
                reporter_id,
                f"Complaint Submitted: #{inc}",
                f"Ticket #{inc} was submitted to {comp['department_name']} ({st_lbl}).",
                "user",
            ))
        elif event_type == "complaint_assigned":
            if assignee_id is not None:
                targets.append((
                    assignee_id,
                    f"New Task Assigned: #{inc}",
                    f"You were assigned ticket #{inc} ({comp['department_name']}). {detail_note}".strip(),
                    "employee",
                ))
            targets.append((
                reporter_id,
                f"Complaint Assigned: #{inc}",
                f"Ticket #{inc} has been assigned to a department officer.",
                "user",
            ))
        elif event_type == "task_accepted":
            targets.append((
                reporter_id,
                f"Complaint In Progress: #{inc}",
                f"Work started on your complaint #{inc} (status: {st_lbl}).",
                "user",
            ))
            for aid in admin_ids:
                targets.append((
                    aid,
                    f"Task Accepted: #{inc}",
                    f"Assigned employee accepted ticket #{inc}.",
                    "admin",
                ))
        elif event_type == "status_changed":
            targets.append((
                reporter_id,
                f"Status Update ({st_lbl}): #{inc}",
                f"Ticket #{inc} moved to status '{st_lbl}'. {detail_note}".strip(),
                "user",
            ))
        elif event_type == "complaint_resolved":
            targets.append((
                reporter_id,
                f"Complaint Resolved: #{inc}",
                f"Ticket #{inc} is Resolved. Please rate and confirm or reopen within 7 days.",
                "user",
            ))
        elif event_type == "complaint_reopened":
            if assignee_id is not None:
                targets.append((
                    assignee_id,
                    f"Complaint Reopened: #{inc}",
                    f"Citizen reopened ticket #{inc} for follow-up.",
                    "employee",
                ))
            for aid in admin_ids:
                targets.append((
                    aid,
                    f"Complaint Reopened: #{inc}",
                    f"Ticket #{inc} was reopened by the citizen.",
                    "admin",
                ))
        elif event_type == "complaint_escalated":
            for aid in admin_ids:
                targets.append((
                    aid,
                    f"SLA Escalation Alert: #{inc}",
                    f"Ticket #{inc} triggered {detail_note or 'SLA escalation'}.",
                    "admin",
                ))
            if not admin_ids:
                targets.append((
                    None,
                    f"SLA Escalation Alert: #{inc}",
                    f"Ticket #{inc} triggered {detail_note or 'SLA escalation'}.",
                    "super_admin",
                ))
        elif event_type == "complaint_transferred":
            targets.append((
                reporter_id,
                f"Complaint Transferred: #{inc}",
                f"Ticket #{inc} was transferred to {comp['department_name']}.",
                "user",
            ))

        seen_uid: set[int | None] = set()
        for uid, t_str, msg_str, role_lbl in targets:
            if uid is not None and uid in seen_uid:
                continue
            seen_uid.add(uid)
            queue_notification(
                uid,
                event_type,
                t_str,
                msg_str,
                complaint_id=int(complaint_id),
                incident_id=inc,
                status_label=st_lbl,
                target_role=role_lbl,
                deliver_now=True,
                smtp_sender=smtp_sender,
            )
    except Exception as exc:
        _logger.warning("Failed emitting notifications for %s (%s): %s", event_type, complaint_id, exc)


def get_user_inbox(
    actor: dict | None,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Return in-app notifications scoped to the logged-in user (all roles supported)."""
    if not isinstance(actor, dict) or "id" not in actor:
        raise PermissionError("Authentication required.")
    uid = int(actor["id"])
    role = actor.get("role")

    with closing(_db.get_connection()) as conn:
        if role == "super_admin":
            where = ["channel = 'in_app'", "(user_id = ? OR (user_id IS NULL AND target_role = 'super_admin'))"]
            params: list[Any] = [uid]
        else:
            where = ["channel = 'in_app'", "user_id = ?"]
            params = [uid]
        if unread_only:
            where.append("is_read = 0")
        params.append(int(limit))
        rows = conn.execute(
            f"""
            SELECT id, user_id, target_role, complaint_id, incident_id,
                   type, title, COALESCE(NULLIF(body, ''), message) AS body,
                   message, is_read, channel, status, attempts, created_at
            FROM notifications
            WHERE {' AND '.join(where)}
            ORDER BY is_read ASC, created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_unread_count(actor: dict | None) -> int:
    if not isinstance(actor, dict) or "id" not in actor:
        return 0
    return len(get_user_inbox(actor, unread_only=True, limit=200))


def mark_user_notification_read(actor: dict | None, notification_id: int) -> None:
    if not isinstance(actor, dict) or "id" not in actor:
        raise PermissionError("Authentication required.")
    uid = int(actor["id"])
    with closing(_db.get_connection()) as conn, conn:
        if actor.get("role") == "super_admin":
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ? AND (user_id = ? OR target_role = 'super_admin')",
                (int(notification_id), uid),
            )
        else:
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
                (int(notification_id), uid),
            )


def mark_all_user_notifications_read(actor: dict | None) -> None:
    if not isinstance(actor, dict) or "id" not in actor:
        raise PermissionError("Authentication required.")
    uid = int(actor["id"])
    with closing(_db.get_connection()) as conn, conn:
        if actor.get("role") == "super_admin":
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE channel = 'in_app' AND (user_id = ? OR target_role = 'super_admin')",
                (uid,),
            )
        else:
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE channel = 'in_app' AND user_id = ?",
                (uid,),
            )
