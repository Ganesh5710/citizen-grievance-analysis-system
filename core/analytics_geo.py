"""
core/analytics_geo.py
District / mandal / pincode filtering, coordinate fallback to area centroid
for hotspot maps/heatmaps, and anonymized Public Transparency Dashboard (<5 hidden).
"""

from __future__ import annotations

from contextlib import closing
from typing import Any

import core.database as _db
from core.permissions import require


def filter_complaints_by_location(
    complaints: list[dict],
    *,
    district_id: int | None = None,
    mandal_id: int | None = None,
    pincode: str | None = None,
) -> list[dict]:
    """Filter role-scoped complaint dicts by district, optional mandal, and/or pincode."""
    out = complaints
    if district_id is not None:
        out = [c for c in out if c.get("district_id") == int(district_id)]
    if mandal_id is not None:
        out = [c for c in out if c.get("mandal_id") == int(mandal_id)]
    pin = str(pincode or "").strip()
    if pin:
        out = [c for c in out if str(c.get("pincode") or "").strip() == pin]
    return out


def extract_map_points(complaints: list[dict]) -> list[dict]:
    """
    Return mappable points using explicit coordinates when available,
    otherwise falling back to the India Post area centroid already merged into latitude/longitude.
    """
    points = []
    for c in complaints:
        lat = c.get("latitude")
        lon = c.get("longitude")
        if lat is None or lon is None:
            continue
        points.append({
            "incident_id": c.get("incident_id"),
            "department": c.get("department"),
            "priority": c.get("priority"),
            "status": c.get("status"),
            "district": c.get("district_name") or "",
            "area": c.get("area_name") or "",
            "pincode": c.get("pincode") or "",
            "latitude": float(lat),
            "longitude": float(lon),
        })
    return points


def get_public_transparency_by_district(actor: dict | None = None) -> dict[str, Any]:
    """
    Publicly accessible (view_public_dashboard='*') anonymized summary grouped by district.
    Strictly hides any district group with fewer than 5 complaints (< 5) to protect anonymity.
    Returns zero PII.
    """
    require(actor, "view_public_dashboard")
    with closing(_db.get_connection()) as conn:
        total_all = conn.execute(
            "SELECT COUNT(*) FROM complaints WHERE is_deleted = 0"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT COALESCE(dist.name, 'Unspecified District') AS district_name,
                   COUNT(c.id) AS total_count,
                   SUM(CASE WHEN c.status IN ('Resolved', 'Closed') THEN 1 ELSE 0 END) AS resolved_count,
                   SUM(CASE WHEN c.status IN ('Submitted', 'Awaiting Assignment', 'Pending') THEN 1 ELSE 0 END) AS awaiting_count,
                   SUM(CASE WHEN c.status IN ('Assigned', 'In Progress', 'Reopened') THEN 1 ELSE 0 END) AS active_count,
                   AVG(
                       CASE WHEN c.resolved_at IS NOT NULL
                            THEN (julianday(c.resolved_at) - julianday(c.created_at)) * 24.0
                       END
                   ) AS avg_resolution_hours
            FROM complaints c
            LEFT JOIN districts dist ON dist.id = c.district_id
            WHERE c.is_deleted = 0
            GROUP BY COALESCE(dist.name, 'Unspecified District')
            ORDER BY total_count DESC, district_name ASC
            """
        ).fetchall()

    visible_groups = []
    suppressed_count = 0
    for r in rows:
        cnt = int(r["total_count"] or 0)
        if cnt < 5:
            suppressed_count += 1
            continue
        res_cnt = int(r["resolved_count"] or 0)
        visible_groups.append({
            "district": r["district_name"],
            "total": cnt,
            "resolved": res_cnt,
            "awaiting_assignment": int(r["awaiting_count"] or 0),
            "in_progress": int(r["active_count"] or 0),
            "resolution_rate_pct": round((res_cnt / cnt) * 100.0, 1) if cnt > 0 else 0.0,
            "avg_resolution_hours": round(float(r["avg_resolution_hours"]), 1)
            if r["avg_resolution_hours"] is not None
            else None,
        })

    return {
        "total_complaints": int(total_all),
        "visible_districts": visible_groups,
        "suppressed_groups_count": suppressed_count,
    }
