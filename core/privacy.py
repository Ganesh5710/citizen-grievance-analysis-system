"""
core/privacy.py
Phase 5: Citizen DPDP Act 2023 privacy tools:
  - Strict single-user data export (JSON & CSV) covering profile, complaints, feedback, and supports
  - Account anonymization / deletion preserving anonymized civic governance records and audit logs
  - Configurable data retention window and structured DPDP 2023 Privacy Notice
"""

from __future__ import annotations

from contextlib import closing
import csv
from datetime import datetime, timezone
import io
import json
import secrets
from typing import Any

from core.config import get_env_int
import core.database as _db
from core.permissions import require
from core.security import hash_password, sanitize_text


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_retention_days() -> int:
    return get_env_int("CGS_DATA_RETENTION_DAYS", 730)


def get_privacy_notice_markdown() -> str:
    days = get_retention_days()
    return f"""
### Privacy Notice (Aligned with India's Digital Personal Data Protection Act, 2023)

1. **Purpose Limitation & Minimal Data Collection:** Your account details (username, full name, phone, email) and complaint geo-location are collected strictly to resolve civic grievances, coordinate district departments, and notify you of ticket progress. Uploaded photos are stripped of EXIF and GPS metadata automatically.
2. **Email Confidentiality:** Automated email alerts include only your **Ticket ID** and **Status**—never descriptions, addresses, phone numbers, or personal names.
3. **Data Principal Rights (DPDP Act, 2023):**
   - **Right to Access & Portability:** Use *My Privacy & Data Export* to download all personal profile, complaint, feedback, and support records in **JSON** or **CSV**.
   - **Right to Erasure / Anonymization:** You may request account deletion at any time. Personal identifiers (name, phone, email, username) are permanently erased/anonymized while retaining de-identified ticket statistics and audit logs required for public accountability and SLA compliance.
4. **Data Retention:** Personal records are retained for up to **{days} days** unless anonymized earlier upon your request or required longer by statutory audit obligations.
"""


def export_own_data(actor: dict | None, fmt: str = "json") -> dict[str, Any]:
    """
    Export ONLY the authenticated citizen's own profile (no password hashes/salts),
    complaints, feedback, and complaint_supports in JSON or CSV.
    """
    require(actor, "export_own_data")
    uid = int(actor["id"])
    clean_fmt = (fmt or "json").strip().lower()
    if clean_fmt not in ("json", "csv"):
        raise ValueError("Export format must be 'json' or 'csv'.")

    with closing(_db.get_connection()) as conn:
        u_row = conn.execute(
            """
            SELECT id, username, full_name, role, preferred_language, phone, email,
                   email_verified, email_notifications, in_app_notifications, created_at
            FROM users WHERE id = ?
            """,
            (uid,),
        ).fetchone()
        if u_row is None:
            raise ValueError("User record not found.")

        comp_rows = conn.execute(
            """
            SELECT c.id, c.incident_id, d.name AS department, c.text, c.location,
                   c.pincode, dist.name AS district, ar.name AS area,
                   c.priority, c.status, c.due_at, c.resolved_at, c.created_at, c.updated_at
            FROM complaints c
            JOIN departments d ON d.id = c.department_id
            LEFT JOIN districts dist ON dist.id = c.district_id
            LEFT JOIN areas ar ON ar.id = c.area_id
            WHERE c.user_id = ? AND c.is_deleted = 0
            ORDER BY c.id ASC
            """,
            (uid,),
        ).fetchall()

        fb_rows = conn.execute(
            """
            SELECT cf.complaint_id, c.incident_id, cf.resolution_cycle, cf.rating,
                   cf.comment, cf.accepted, cf.created_at
            FROM complaint_feedback cf
            JOIN complaints c ON c.id = cf.complaint_id
            WHERE cf.user_id = ?
            ORDER BY cf.id ASC
            """,
            (uid,),
        ).fetchall()

        sup_rows = conn.execute(
            """
            SELECT cs.complaint_id, c.incident_id, cs.created_at
            FROM complaint_supports cs
            JOIN complaints c ON c.id = cs.complaint_id
            WHERE cs.user_id = ?
            ORDER BY cs.id ASC
            """,
            (uid,),
        ).fetchall()

    payload = {
        "exported_at_utc": _utc_now(),
        "retention_policy_days": get_retention_days(),
        "profile": dict(u_row),
        "complaints": [dict(r) for r in comp_rows],
        "feedback": [dict(r) for r in fb_rows],
        "supports": [dict(r) for r in sup_rows],
    }

    if clean_fmt == "json":
        return {
            "format": "json",
            "content": json.dumps(payload, indent=2),
            "filename": f"dpdp_export_user_{uid}.json",
            "data": payload,
        }

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "record_type", "incident_id", "department", "district", "area",
        "pincode", "priority", "status", "text_or_comment", "rating_or_detail", "created_at",
    ])
    p = payload["profile"]
    writer.writerow([
        "profile", "", "", "", "", "", "", "",
        f"username={p['username']}; full_name={p['full_name']}; email={p['email']}",
        f"phone={p['phone']}; lang={p['preferred_language']}", p["created_at"],
    ])
    for c in payload["complaints"]:
        writer.writerow([
            "complaint", c["incident_id"], c["department"], c["district"] or "", c["area"] or "",
            c["pincode"] or "", c["priority"], c["status"], c["text"], c["location"], c["created_at"],
        ])
    for f in payload["feedback"]:
        writer.writerow([
            "feedback", f["incident_id"], "", "", "", "", "",
            "Accepted" if f["accepted"] else "Reopened", f["comment"] or "", f"rating={f['rating']}", f["created_at"],
        ])
    for s in payload["supports"]:
        writer.writerow([
            "support", s["incident_id"], "", "", "", "", "", "",
            "Supported existing cluster", "", s["created_at"],
        ])

    return {
        "format": "csv",
        "content": buf.getvalue(),
        "filename": f"dpdp_export_user_{uid}.csv",
        "data": payload,
    }


def anonymize_own_account(actor: dict | None, reason: str = "") -> dict[str, Any]:
    """
    Citizen Right-to-Erasure / Deletion Request:
      - Permanently erases/anonymizes PII fields on users (username, full_name, phone, email, credentials)
      - Deactivates account (is_active=0) & sets anonymized_at
      - Scrubs free-text landmark/contact strings on complaints if needed while retaining anonymized
        civic complaints & audit rows for public accountability and SLA integrity.
    """
    require(actor, "export_own_data")
    uid = int(actor["id"])
    clean_reason = sanitize_text(reason or "", max_length=300, field_name="Deletion reason")
    now_str = _utc_now()
    anon_username = f"anon_user_{uid}_{secrets.token_hex(3)}"
    rand_hash, rand_salt = hash_password(secrets.token_urlsafe(32))

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute("SELECT id, username, role, is_active FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise ValueError("Account not found.")
        if row["role"] != "user":
            raise PermissionError("Only citizen accounts may self-anonymize via this workflow.")

        conn.execute(
            """
            UPDATE users
            SET username = ?,
                full_name = 'Anonymized Citizen',
                password_hash = ?,
                salt = ?,
                phone = '',
                email = '',
                email_verified = 0,
                email_notifications = 0,
                in_app_notifications = 0,
                is_active = 0,
                anonymized_at = ?
            WHERE id = ?
            """,
            (anon_username, rand_hash, rand_salt, now_str, uid),
        )
        # Preserve anonymized complaints + department/district/status; scrub free-text personal location notes
        conn.execute(
            "UPDATE complaints SET location = 'Anonymized Area', updated_at = ? WHERE user_id = ?",
            (now_str, uid),
        )
        conn.execute(
            """
            INSERT INTO moderation_audit_log
                (actor_id, actor_username, target_user_id, target_username, complaint_id, action, reason, created_at)
            VALUES (?, ?, ?, ?, NULL, 'dpdp_account_anonymized', ?, ?)
            """,
            (uid, anon_username, uid, anon_username, clean_reason or "Citizen DPDP account deletion request", now_str),
        )

    return {"anonymized_user_id": uid, "anonymized_username": anon_username, "anonymized_at": now_str}
