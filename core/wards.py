"""
Hyderabad wards catalog and Haversine nearest-ward resolution.
"""

from contextlib import closing
import math

import core.database as _db


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in meters between two WGS84 coordinates."""
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def get_all_wards() -> list[dict]:
    """Return all seeded Hyderabad wards ordered by name."""
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            "SELECT id, name, lat, lon, polygon FROM wards ORDER BY name ASC"
        ).fetchall()
    return [
        {
            "id":      r["id"],
            "name":    r["name"],
            "lat":     float(r["lat"]),
            "lon":     float(r["lon"]),
            "polygon": r["polygon"] or "",
        }
        for r in rows
    ]


def get_ward_by_id(ward_id: int) -> dict | None:
    with closing(_db.get_connection()) as conn:
        r = conn.execute(
            "SELECT id, name, lat, lon, polygon FROM wards WHERE id = ?",
            (ward_id,),
        ).fetchone()
    if r is None:
        return None
    return {
        "id":      r["id"],
        "name":    r["name"],
        "lat":     float(r["lat"]),
        "lon":     float(r["lon"]),
        "polygon": r["polygon"] or "",
    }


def find_nearest_ward(lat: float, lon: float) -> dict | None:
    """Return the nearest ward dict for (lat, lon) by Haversine distance."""
    wards = get_all_wards()
    if not wards:
        return None
    return min(wards, key=lambda w: haversine_meters(lat, lon, w["lat"], w["lon"]))
