"""
scripts/inject_spike.py
Creates or removes clearly labeled synthetic demo spike complaints (`is_demo_spike=1`)
so rolling-window anomaly alerts can be demonstrated live and cleaned up afterward.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import uuid

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics.anomaly import detect_anomalies  # noqa: E402
from core.auth import register  # noqa: E402
import core.database as _db  # noqa: E402


def inject_demo_spike(
    *,
    count: int = 8,
    department_name: str = "Water",
    district_name: str | None = None,
) -> dict:
    _db.init_db()
    now_dt = datetime.now(timezone.utc)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

    with closing(_db.get_connection()) as conn, conn:
        dept = conn.execute(
            "SELECT id, name FROM departments WHERE lower(name) = lower(?) AND is_active = 1",
            (department_name,),
        ).fetchone()
        if dept is None:
            dept = conn.execute("SELECT id, name FROM departments WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()

        if district_name:
            dist = conn.execute(
                "SELECT d.id, d.state_id, d.name FROM districts d WHERE lower(d.name) = lower(?)",
                (district_name,),
            ).fetchone()
        else:
            dist = conn.execute("SELECT id, state_id, name FROM districts WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()
        if dist is None:
            raise RuntimeError("No districts loaded. Run scripts/load_locations.py first.")

        area = conn.execute(
            "SELECT a.id, a.name, a.latitude, a.longitude, p.pincode "
            "FROM areas a LEFT JOIN pincodes p ON p.area_id = a.id "
            "WHERE a.district_id = ? ORDER BY a.id LIMIT 1",
            (dist["id"],),
        ).fetchone()

        cit = conn.execute("SELECT id FROM users WHERE role = 'user' AND is_active = 1 ORDER BY id LIMIT 1").fetchone()
        cit_id = cit["id"] if cit else None

    if cit_id is None:
        cit_user = register("demo_spike_citizen", "Demo Spike Citizen", "DemoSpike@123")
        cit_id = cit_user["id"]

    with closing(_db.get_connection()) as conn, conn:
        for i in range(count):
            inc_id = "CIV-SPK" + uuid.uuid4().hex[:5].upper()
            ts = (now_dt - timedelta(minutes=min(45, i * 3))).strftime("%Y-%m-%d %H:%M:%S")
            due = (now_dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO complaints
                    (incident_id, user_id, text, location, department_id, state_id, district_id, area_id, pincode,
                     latitude, longitude, priority, status, due_at, is_demo_spike, priority_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'High', 'Awaiting Assignment', ?, 1, 'Demo spike simulation', ?, ?)
                """,
                (
                    inc_id,
                    cit_id,
                    f"[DEMO SPIKE] Sudden pressure drop & pipe rupture #{i + 1} in {dist['name']}",
                    area["name"] if area else dist["name"],
                    dept["id"],
                    dist["state_id"],
                    dist["id"],
                    area["id"] if area else None,
                    area["pincode"] if area and area["pincode"] else "522002",
                    area["latitude"] if area else None,
                    area["longitude"] if area else None,
                    due,
                    ts,
                    now_str,
                ),
            )

    alerts = detect_anomalies(now=now_dt, notify=True)
    return {"inserted": count, "district": dist["name"], "department": dept["name"], "alerts_triggered": len(alerts)}


def remove_demo_spikes() -> int:
    _db.init_db()
    with closing(_db.get_connection()) as conn, conn:
        cur = conn.execute("UPDATE complaints SET is_deleted = 1 WHERE COALESCE(is_demo_spike, 0) = 1 AND is_deleted = 0")
        conn.execute("DELETE FROM notifications WHERE title LIKE 'Spike Alert [DEMO SPIKE]%'")
        return int(cur.rowcount or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inject or remove clearly labeled demo spike complaints.")
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--department", default="Water")
    parser.add_argument("--district", default=None)
    parser.add_argument("--remove", action="store_true", help="Remove demo spike rows and alerts.")
    args = parser.parse_args()

    if args.remove:
        removed = remove_demo_spikes()
        print(f"Removed {removed} demo spike complaint(s) and demo spike alert notifications.")
    else:
        res = inject_demo_spike(count=args.count, department_name=args.department, district_name=args.district)
        print(f"Injected {res['inserted']} [DEMO SPIKE] rows in {res['district']} / {res['department']}. Alerts triggered: {res['alerts_triggered']}")


if __name__ == "__main__":
    main()
