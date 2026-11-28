"""
analytics/anomaly.py
Phase 2: Rolling-window Spike / Anomaly Detection per (district, department)
using configurable Z-score + optional IsolationForest check.
Creates deduplicated alerts for matching District Admins and Super Admin.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

from core.config import get_env_float, get_env_int
import core.database as _db
from core.permissions import require


def _utc_now(now: datetime | None = None) -> datetime:
    dt = now or datetime.now(timezone.utc)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def detect_anomalies(
    *,
    now: datetime | None = None,
    window_hours: int | None = None,
    z_threshold: float | None = None,
    min_spike_count: int = 4,
    notify: bool = True,
) -> list[dict[str, Any]]:
    """
    Check recent complaint counts within `[now - window_hours, now]` against trailing
    7-day baseline windows per `(district_id, department_id)`.
    Flags spikes where `current_count >= min_spike_count` AND `(z_score >= z_threshold or iso_outlier)`.
    Writes deduplicated notifications for matching District Admin(s) and Super Admin.
    """
    now_dt = _utc_now(now)
    win_h = max(1, int(window_hours if window_hours is not None else get_env_int("CGS_ANOMALY_WINDOW_HOURS", 2)))
    z_thresh = float(z_threshold if z_threshold is not None else get_env_float("CGS_ANOMALY_ZSCORE", 2.5))

    win_start_dt = now_dt - timedelta(hours=win_h)
    hist_start_dt = win_start_dt - timedelta(days=7)

    now_str = _fmt_dt(now_dt)
    win_start_str = _fmt_dt(win_start_dt)
    hist_start_str = _fmt_dt(hist_start_dt)

    alerts: list[dict[str, Any]] = []

    with closing(_db.get_connection()) as conn, conn:
        rows = conn.execute(
            """
            SELECT c.id, c.district_id, COALESCE(dist.name, 'Unscoped') AS district_name,
                   c.department_id, d.name AS department_name,
                   c.created_at, COALESCE(c.is_demo_spike, 0) AS is_demo_spike
            FROM complaints c
            JOIN departments d ON d.id = c.department_id
            LEFT JOIN districts dist ON dist.id = c.district_id
            WHERE c.is_deleted = 0
              AND c.created_at >= ?
              AND c.created_at <= ?
            """,
            (hist_start_str, now_str),
        ).fetchall()

        if not rows:
            return []

        buckets: dict[tuple[int | None, int], dict[str, Any]] = {}
        n_hist_windows = max(1, (7 * 24) // win_h)

        for r in rows:
            key = (r["district_id"], int(r["department_id"]))
            b = buckets.setdefault(key, {
                "district_id": r["district_id"],
                "district_name": r["district_name"],
                "department_id": int(r["department_id"]),
                "department_name": r["department_name"],
                "current_count": 0,
                "demo_count": 0,
                "hist_counts": np.zeros(n_hist_windows, dtype=float),
            })
            c_ts = r["created_at"]
            if c_ts >= win_start_str:
                b["current_count"] += 1
                if r["is_demo_spike"]:
                    b["demo_count"] += 1
            else:
                try:
                    c_dt = datetime.strptime(c_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    slot = int((c_dt - hist_start_dt).total_seconds() // (win_h * 3600))
                    if 0 <= slot < n_hist_windows:
                        b["hist_counts"][slot] += 1.0
                except Exception:
                    pass

        dedup_cutoff = _fmt_dt(now_dt - timedelta(hours=win_h))

        for (did, dep_id), b in buckets.items():
            cur_cnt = int(b["current_count"])
            if cur_cnt < min_spike_count:
                continue

            hist_arr = b["hist_counts"]
            mean_val = float(np.mean(hist_arr))
            std_val = float(np.std(hist_arr))
            denom = max(std_val, 1.0)
            z_score = (cur_cnt - mean_val) / denom

            iso_outlier = False
            if np.any(hist_arr > 0) and len(hist_arr) >= 12:
                try:
                    iso = IsolationForest(contamination=0.05, random_state=42)
                    iso.fit(hist_arr.reshape(-1, 1))
                    if int(iso.predict([[cur_cnt]])[0]) == -1:
                        iso_outlier = True
                except Exception:
                    pass

            if z_score >= z_thresh or (cur_cnt >= max(min_spike_count + 2, int(mean_val * 3 + 3)) and iso_outlier):
                is_demo = b["demo_count"] > 0
                demo_tag = " [DEMO SPIKE]" if is_demo else ""
                title = f"Spike Alert{demo_tag}: {b['department_name']} ({b['district_name']})"
                msg = (
                    f"{cur_cnt} complaints in {win_h}h window for {b['department_name']} in "
                    f"{b['district_name']} (7d baseline mean={mean_val:.2f}, z={z_score:.2f}"
                    f"{', IsolationForest outlier' if iso_outlier else ''})."
                )
                alerts.append({
                    "district_id": did,
                    "district_name": b["district_name"],
                    "department_id": dep_id,
                    "department_name": b["department_name"],
                    "window_hours": win_h,
                    "current_count": cur_cnt,
                    "baseline_mean": round(mean_val, 2),
                    "z_score": round(z_score, 2),
                    "iso_outlier": iso_outlier,
                    "is_demo_spike": is_demo,
                    "title": title,
                    "message": msg,
                })

                if notify:
                    # Deduplicate Super Admin notification in current window
                    exists_sa = conn.execute(
                        """
                        SELECT id FROM notifications
                        WHERE target_role = 'super_admin' AND title = ? AND created_at >= ?
                        """,
                        (title, dedup_cutoff),
                    ).fetchone()
                    if exists_sa is None:
                        conn.execute(
                            """
                            INSERT INTO notifications
                                (user_id, target_role, complaint_id, incident_id, type, title,
                                 body, message, is_read, channel, status, created_at)
                            VALUES (NULL, 'super_admin', NULL, ?, 'anomaly_alert', ?, ?, ?, 0, 'in_app', 'sent', ?)
                            """,
                            (f"ANOM-{b['district_name'][:6].upper()}-{dep_id}", title, msg, msg, now_str),
                        )

                    # Notify active District Admin(s) responsible for this district (+ dept)
                    if did is not None:
                        adm_rows = conn.execute(
                            """
                            SELECT DISTINCT aj.user_id
                            FROM admin_jurisdictions aj
                            JOIN users u ON u.id = aj.user_id
                            WHERE aj.district_id = ?
                              AND u.role = 'admin' AND u.is_active = 1
                              AND (aj.department_id IS NULL OR aj.department_id = ?)
                            """,
                            (int(did), dep_id),
                        ).fetchall()
                        for ar in adm_rows:
                            exists_adm = conn.execute(
                                """
                                SELECT id FROM notifications
                                WHERE user_id = ? AND title = ? AND created_at >= ?
                                """,
                                (ar["user_id"], title, dedup_cutoff),
                            ).fetchone()
                            if exists_adm is None:
                                conn.execute(
                                    """
                                    INSERT INTO notifications
                                        (user_id, target_role, complaint_id, incident_id, type, title,
                                         body, message, is_read, channel, status, created_at)
                                    VALUES (?, 'admin', NULL, ?, 'anomaly_alert', ?, ?, ?, 0, 'in_app', 'sent', ?)
                                    """,
                                    (ar["user_id"], f"ANOM-{b['district_name'][:6].upper()}-{dep_id}", title, msg, msg, now_str),
                                )
                                conn.execute(
                                    """
                                    INSERT INTO notifications
                                        (user_id, target_role, complaint_id, incident_id, type, title,
                                         body, message, is_read, channel, status, attempts, created_at)
                                    SELECT u.id, 'admin', NULL, ?, 'anomaly_alert', ?, ?, ?, 1, 'email', 'pending', 0, ?
                                    FROM users u
                                    WHERE u.id = ? AND u.email_notifications = 1 AND u.email_verified = 1 AND u.email != ''
                                    """,
                                    (f"ANOM-{b['district_name'][:6].upper()}-{dep_id}", title, msg, msg, now_str, ar["user_id"]),
                                )

    if notify and alerts:
        try:
            from notifications.service import deliver_pending_emails
            deliver_pending_emails(limit=15)
        except Exception:
            pass

    alerts.sort(key=lambda a: a["z_score"], reverse=True)
    return alerts


def get_anomaly_alerts(actor: dict | None, *, unread_only: bool = False, limit: int = 25) -> list[dict]:
    """Return anomaly alert notifications visible to the calling Super Admin or scoped Admin."""
    require(actor, "view_analytics")
    role = actor.get("role")
    if role not in ("admin", "super_admin"):
        raise PermissionError("Only Admin and Super Admin can inspect anomaly alerts.")

    with closing(_db.get_connection()) as conn:
        if role == "super_admin":
            sql = "SELECT * FROM notifications WHERE target_role = 'super_admin' AND title LIKE 'Spike Alert%'"
            params: list[Any] = []
        else:
            sql = "SELECT * FROM notifications WHERE user_id = ? AND title LIKE 'Spike Alert%'"
            params = [actor["id"]]
        if unread_only:
            sql += " AND is_read = 0"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
