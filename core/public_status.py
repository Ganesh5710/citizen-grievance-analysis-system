"""
Public unauthenticated ticket status lookup (rate-limited, zero PII) and QR helpers.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import io
import os
import re
from typing import Any
from urllib.parse import quote

import qrcode

import core.database as _db
from core.security import sanitize_text


_GENERIC_LOOKUP_ERROR = (
    "No matching complaint found. Please check your Ticket ID and last 4 contact digits/characters."
)


def _utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.strptime(val.strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_public_lookup_rate_limit() -> int:
    raw = os.getenv("CGS_PUBLIC_LOOKUP_RATE_LIMIT_PER_HOUR", "30")
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def build_ticket_tracking_url(incident_id: str, base_url: str | None = None) -> str:
    clean_id = (incident_id or "").strip().upper()
    root = (base_url or os.getenv("PUBLIC_BASE_URL", "http://localhost:8501")).strip().rstrip("/")
    return f"{root}/?ticket={quote(clean_id)}"


def generate_ticket_qr_png(incident_id: str, base_url: str | None = None) -> bytes:
    """Render a PNG QR code pointing to the public ticket status lookup URL."""
    url = build_ticket_tracking_url(incident_id, base_url=base_url)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _check_and_bump_rate_limit(conn, client_key: str, now_dt: datetime) -> None:
    limit = get_public_lookup_rate_limit()
    key = sanitize_text(client_key or "global", max_length=100, field_name="Client key") or "global"
    row = conn.execute(
        "SELECT attempt_count, window_start FROM public_status_rate_limits WHERE client_key = ?",
        (key,),
    ).fetchone()
    now_str = _fmt_dt(now_dt)
    if row is None:
        conn.execute(
            "INSERT INTO public_status_rate_limits (client_key, attempt_count, window_start) VALUES (?, 1, ?)",
            (key, now_str),
        )
        return

    ws = _parse_dt(row["window_start"]) or now_dt
    if now_dt - ws >= timedelta(hours=1):
        conn.execute(
            "UPDATE public_status_rate_limits SET attempt_count = 1, window_start = ? WHERE client_key = ?",
            (now_str, key),
        )
        return

    cnt = int(row["attempt_count"] or 0)
    if cnt >= limit:
        raise ValueError("Too many public ticket lookup attempts. Please try again later.")
    conn.execute(
        "UPDATE public_status_rate_limits SET attempt_count = ? WHERE client_key = ?",
        (cnt + 1, key),
    )


def _matches_last4(phone: str | None, email: str | None, last4: str) -> bool:
    if len(last4) != 4:
        return False
    phone_digits = re.sub(r"\D", "", phone or "")
    if len(phone_digits) >= 4 and phone_digits[-4:] == last4:
        return True
    clean_email = (email or "").strip().lower()
    l4_lower = last4.lower()
    if len(clean_email) >= 4 and clean_email[-4:] == l4_lower:
        return True
    if "@" in clean_email:
        local = clean_email.split("@", 1)[0]
        if len(local) >= 4 and local[-4:] == l4_lower:
            return True
    return False


def _sanitize_timeline_row(row: Any) -> dict[str, str]:
    field = (row["field_name"] or "status").lower()
    new_v = str(row["new_value"] or row["new_status"] or "").strip()
    if field == "status":
        label = f"Status updated to {new_v}" if new_v else "Status updated"
    elif field == "department":
        label = f"Department routed to {new_v}" if new_v else "Department updated"
    elif field == "priority":
        label = f"Priority set to {new_v}" if new_v else "Priority updated"
    elif field == "assigned_to":
        label = "Officer assignment updated"
    elif field == "escalation":
        label = f"SLA escalation level {new_v}" if new_v else "SLA escalation recorded"
    elif field == "cluster_merge":
        label = "Linked to neighborhood incident cluster"
    elif field == "withdrawal_request":
        label = "Withdrawal request updated"
    else:
        label = f"Ticket {field.replace('_', ' ')} updated"
    return {
        "timestamp": row["created_at"] or "",
        "event": label,
    }


def lookup_public_status(
    incident_id: str,
    last4_contact: str,
    *,
    now: datetime | None = None,
    client_key: str = "global",
) -> dict[str, Any]:
    """
    Public unauthenticated status lookup by Ticket ID + last 4 digits of phone
    or last 4 characters of email/local-part. Rate-limited; strips all PII,
    remarks, officer names, photos, and internal staff notes.
    """
    now_dt = _utc_now(now)
    clean_ticket = sanitize_text(incident_id or "", max_length=40, field_name="Ticket ID").upper()
    clean_last4 = (last4_contact or "").strip()

    with closing(_db.get_connection()) as conn, conn:
        _check_and_bump_rate_limit(conn, client_key, now_dt)

    if len(clean_ticket) < 4 or len(clean_last4) != 4:
        raise ValueError(_GENERIC_LOOKUP_ERROR)

    with closing(_db.get_connection()) as conn:
        row = conn.execute(
            """
            SELECT c.id, c.incident_id, c.status, c.priority,
                   c.created_at, c.updated_at, c.due_at, c.resolved_at,
                   d.name AS department, dist.name AS district,
                   u.phone, u.email
            FROM complaints c
            JOIN users u ON u.id = c.user_id
            JOIN departments d ON d.id = c.department_id
            LEFT JOIN districts dist ON dist.id = c.district_id
            WHERE UPPER(c.incident_id) = ? AND c.is_deleted = 0
            """,
            (clean_ticket,),
        ).fetchone()

        if row is None or not _matches_last4(row["phone"], row["email"], clean_last4):
            raise ValueError(_GENERIC_LOOKUP_ERROR)

        upd_rows = conn.execute(
            """
            SELECT field_name, new_status, new_value, created_at
            FROM complaint_updates
            WHERE complaint_id = ?
            ORDER BY id ASC
            """,
            (row["id"],),
        ).fetchall()

    timeline = [_sanitize_timeline_row(u) for u in upd_rows]
    return {
        "incident_id": row["incident_id"],
        "status": row["status"],
        "department": row["department"],
        "district": row["district"] or "",
        "priority": row["priority"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "due_at": row["due_at"],
        "resolved_at": row["resolved_at"],
        "tracking_url": build_ticket_tracking_url(row["incident_id"]),
        "timeline": timeline,
    }
