"""
Weekly Admin Summary Digest cron script.
Generates zero-PII 7-day jurisdiction digests and queues/sends them to
active, opted-in, verified Admin accounts.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.database as _db  # noqa: E402
from core.reports import generate_weekly_admin_summary  # noqa: E402
from notifications.service import queue_notification  # noqa: E402


def send_weekly_admin_digests(
    *,
    days: int = 7,
    smtp_sender: Callable[[str, str, str], None] | None = None,
) -> list[dict]:
    _db.init_db()
    with closing(_db.get_connection()) as conn:
        admins = conn.execute(
            """
            SELECT id, username, full_name, role, department_id, district_id,
                   email, email_verified, email_notifications
            FROM users
            WHERE role = 'admin' AND is_active = 1 AND email_notifications = 1 AND email_verified = 1
            ORDER BY id ASC
            """
        ).fetchall()
        admin_dists: dict[int, list[int]] = {}
        for r in conn.execute("SELECT user_id, district_id FROM admin_jurisdictions").fetchall():
            admin_dists.setdefault(int(r["user_id"]), []).append(int(r["district_id"]))

    results = []
    for row in admins:
        adm = dict(row)
        adm["assigned_districts"] = admin_dists.get(int(adm["id"]), [])
        if not adm["assigned_districts"] and adm.get("district_id") is not None:
            adm["assigned_districts"] = [int(adm["district_id"])]
        summary = generate_weekly_admin_summary(adm, days=days)
        res = queue_notification(
            adm["id"],
            "weekly_admin_digest",
            summary["subject"],
            summary["body"],
            target_role="admin",
            deliver_now=True,
            smtp_sender=smtp_sender,
        )
        results.append({"admin": adm["username"], "summary": summary, "delivery": res})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Send weekly jurisdiction summary digests to opted-in verified Admins.")
    parser.add_argument("--days", type=int, default=7, help="Rolling window in days (default: 7)")
    args = parser.parse_args()
    out = send_weekly_admin_digests(days=args.days)
    print(f"Processed weekly digest for {len(out)} eligible Admin account(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
