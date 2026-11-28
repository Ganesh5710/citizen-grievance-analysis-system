"""
core/locations.py
Hierarchical India Post location taxonomy: states, districts, optional mandals,
areas (post offices/villages/wards), and 6-digit pincodes.
Defaults to DEFAULT_STATE ('Andhra Pradesh') from .env/config.
"""

from __future__ import annotations

import csv
import re
from contextlib import closing
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT, get_env_str
import core.database as _db
from core.permissions import require

DEFAULT_LOCATION_CSV = PROJECT_ROOT / "data" / "india_post_pincodes_ap.csv"
_PINCODE_RE = re.compile(r"^\d{6}$")


def get_default_state_name() -> str:
    return get_env_str("DEFAULT_STATE", "Andhra Pradesh")


def normalize_location_name(raw: str) -> str:
    """Collapse extra whitespace and title-case place names consistently while keeping B.O/S.O/H.O neat."""
    cleaned = " ".join(str(raw or "").strip().split())
    if not cleaned:
        return ""
    titled = cleaned.title()
    for suffix in ("B.O", "S.O", "H.O", "Bo", "So", "Ho"):
        titled = re.sub(rf"\b{re.escape(suffix)}$", suffix.upper() if "." in suffix else suffix.upper(), titled)
    return titled


def _parse_coord(raw: Any, min_val: float, max_val: float) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.upper() in ("NA", "NULL", "NONE", "NAN"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if min_val <= v <= max_val and v != 0.0:
        return round(v, 6)
    return None


def load_locations_from_csv(
    csv_path: str | Path | None = None,
    *,
    default_state: str | None = None,
    filter_to_default_state: bool = True,
) -> dict[str, Any]:
    """
    Idempotently load states, districts, optional mandals, areas, and pincodes from
    an official India Post / data.gov.in CSV placed in data/.
    Does NOT invent location data. Missing mandal columns are cleanly skipped and documented.
    Returns summary counters + up to 50 sample skipped row diagnostics.
    """
    src = Path(csv_path) if csv_path else DEFAULT_LOCATION_CSV
    if not src.exists():
        raise FileNotFoundError(f"Location CSV not found: {src}")

    target_state_norm = normalize_location_name(default_state or get_default_state_name())

    with open(src, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        raw_fields = list(reader.fieldnames or [])
        lower_to_orig = {col.strip().lower(): col for col in raw_fields}

        def _find_col(*candidates: str) -> str | None:
            for c in candidates:
                if c in lower_to_orig:
                    return lower_to_orig[c]
            return None

        col_state = _find_col("statename", "state", "state_name")
        col_dist = _find_col("district", "districtname", "district_name")
        col_mandal = _find_col("mandal", "mandalname", "taluk", "taluka", "subdistrict", "block")
        col_area = _find_col("officename", "office_name", "area", "area_name", "village", "postoffice")
        col_pin = _find_col("pincode", "pin", "pin_code")
        col_lat = _find_col("latitude", "lat")
        col_lon = _find_col("longitude", "lon", "lng")

        if not col_dist or not col_area or not col_pin:
            raise ValueError(
                f"CSV missing required location columns (district/area/pincode). Found: {raw_fields}"
            )

        rows = list(reader)

    mandal_present = col_mandal is not None
    skipped_reasons: dict[str, int] = {}
    skipped_samples: list[dict[str, Any]] = []
    total_rows = len(rows)

    def _record_skip(row_idx: int, reason: str, raw_row: dict) -> None:
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
        if len(skipped_samples) < 50:
            skipped_samples.append({
                "row": row_idx,
                "reason": reason,
                "pincode": str(raw_row.get(col_pin, "")),
                "district": str(raw_row.get(col_dist, "")),
                "area": str(raw_row.get(col_area, "")),
            })

    loaded_states = 0
    loaded_districts = 0
    loaded_mandals = 0
    loaded_areas = 0
    loaded_pincodes = 0
    processed_valid = 0

    with closing(_db.get_connection()) as conn, conn:
        state_cache: dict[str, int] = {
            r["name"].lower(): r["id"]
            for r in conn.execute("SELECT id, name FROM states").fetchall()
        }
        dist_cache: dict[tuple[int, str], int] = {
            (r["state_id"], r["name"].lower()): r["id"]
            for r in conn.execute("SELECT id, state_id, name FROM districts").fetchall()
        }
        mandal_cache: dict[tuple[int, str], int] = {
            (r["district_id"], r["name"].lower()): r["id"]
            for r in conn.execute("SELECT id, district_id, name FROM mandals").fetchall()
        }
        area_cache: dict[tuple[int, str], int] = {
            (r["district_id"], r["name"].lower()): r["id"]
            for r in conn.execute("SELECT id, district_id, name FROM areas").fetchall()
        }
        pin_cache: set[tuple[str, int]] = {
            (r["pincode"], r["area_id"])
            for r in conn.execute("SELECT pincode, area_id FROM pincodes").fetchall()
        }

        for row_num, row in enumerate(rows, start=2):
            raw_pin = str(row.get(col_pin) or "").strip()
            if not _PINCODE_RE.match(raw_pin):
                _record_skip(row_num, "invalid_pincode", row)
                continue

            raw_state = row.get(col_state) if col_state else target_state_norm
            state_name = normalize_location_name(raw_state or "")
            if not state_name:
                _record_skip(row_num, "missing_state", row)
                continue

            if filter_to_default_state and state_name.lower() != target_state_norm.lower():
                _record_skip(row_num, "filtered_other_state", row)
                continue

            dist_name = normalize_location_name(row.get(col_dist) or "")
            if not dist_name or dist_name.upper() in ("NA", "NULL", "NONE"):
                _record_skip(row_num, "missing_district", row)
                continue

            area_name = normalize_location_name(row.get(col_area) or "")
            if not area_name or area_name.upper() in ("NA", "NULL", "NONE"):
                _record_skip(row_num, "missing_area", row)
                continue

            lat = _parse_coord(row.get(col_lat) if col_lat else None, -90.0, 90.0)
            lon = _parse_coord(row.get(col_lon) if col_lon else None, -180.0, 180.0)

            s_key = state_name.lower()
            if s_key not in state_cache:
                cur = conn.execute(
                    "INSERT INTO states (name, code, is_active) VALUES (?, ?, 1)",
                    (state_name, "AP" if s_key == "andhra pradesh" else ""),
                )
                state_cache[s_key] = cur.lastrowid
                loaded_states += 1
            state_id = state_cache[s_key]

            d_key = (state_id, dist_name.lower())
            if d_key not in dist_cache:
                cur = conn.execute(
                    "INSERT INTO districts (state_id, name, is_active) VALUES (?, ?, 1)",
                    (state_id, dist_name),
                )
                dist_cache[d_key] = cur.lastrowid
                loaded_districts += 1
            district_id = dist_cache[d_key]

            mandal_id: int | None = None
            if mandal_present:
                m_raw = normalize_location_name(row.get(col_mandal) or "")
                if m_raw and m_raw.upper() not in ("NA", "NULL", "NONE"):
                    m_key = (district_id, m_raw.lower())
                    if m_key not in mandal_cache:
                        cur = conn.execute(
                            "INSERT INTO mandals (district_id, name, is_active) VALUES (?, ?, 1)",
                            (district_id, m_raw),
                        )
                        mandal_cache[m_key] = cur.lastrowid
                        loaded_mandals += 1
                    mandal_id = mandal_cache[m_key]

            a_key = (district_id, area_name.lower())
            if a_key not in area_cache:
                cur = conn.execute(
                    "INSERT INTO areas (district_id, mandal_id, name, latitude, longitude, is_active) "
                    "VALUES (?, ?, ?, ?, ?, 1)",
                    (district_id, mandal_id, area_name, lat, lon),
                )
                area_cache[a_key] = cur.lastrowid
                loaded_areas += 1
            else:
                # Backfill missing centroid coords if a duplicate post office row provides them
                if lat is not None and lon is not None:
                    conn.execute(
                        "UPDATE areas SET latitude = COALESCE(latitude, ?), "
                        "longitude = COALESCE(longitude, ?) WHERE id = ?",
                        (lat, lon, area_cache[a_key]),
                    )
            area_id = area_cache[a_key]

            p_key = (raw_pin, area_id)
            if p_key not in pin_cache:
                conn.execute(
                    "INSERT INTO pincodes (pincode, area_id, district_id, state_id) VALUES (?, ?, ?, ?)",
                    (raw_pin, area_id, district_id, state_id),
                )
                pin_cache.add(p_key)
                loaded_pincodes += 1
            processed_valid += 1

        notes = (
            "Loaded mandal hierarchy from CSV."
            if mandal_present
            else "Skipped mandal level: source CSV lacks mandal/taluk column."
        )
        conn.execute(
            """
            INSERT INTO location_load_logs
                (source_path, default_state, total_rows, loaded_areas, loaded_pincodes,
                 skipped_rows, mandal_level_present, notes, loaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(src),
                target_state_norm,
                total_rows,
                loaded_areas,
                loaded_pincodes,
                sum(skipped_reasons.values()),
                1 if mandal_present else 0,
                notes,
                _db.utc_now_str(),
            ),
        )

    return {
        "source_path": str(src),
        "default_state": target_state_norm,
        "total_rows": total_rows,
        "processed_valid_rows": processed_valid,
        "loaded_states": loaded_states,
        "loaded_districts": loaded_districts,
        "loaded_mandals": loaded_mandals,
        "loaded_areas": loaded_areas,
        "loaded_pincodes": loaded_pincodes,
        "skipped_rows": sum(skipped_reasons.values()),
        "skipped_reasons": skipped_reasons,
        "skipped_samples": skipped_samples,
        "mandal_level_present": mandal_present,
        "notes": notes,
    }


def get_states(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM states" + (" WHERE is_active = 1" if active_only else "") + " ORDER BY name"
    with closing(_db.get_connection()) as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_default_state() -> dict | None:
    target = get_default_state_name().strip().lower()
    with closing(_db.get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM states WHERE lower(name) = ? AND is_active = 1", (target,)
        ).fetchone()
        if row:
            return dict(row)
        first = conn.execute("SELECT * FROM states WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()
    return dict(first) if first else None


def get_districts(state_id: int | None = None, active_only: bool = True) -> list[dict]:
    where = []
    params: list[Any] = []
    if state_id is not None:
        where.append("state_id = ?")
        params.append(int(state_id))
    if active_only:
        where.append("is_active = 1")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(f"SELECT * FROM districts{clause} ORDER BY name", params).fetchall()
    return [dict(r) for r in rows]


def get_mandals(district_id: int, active_only: bool = True) -> list[dict]:
    where = ["district_id = ?"]
    params: list[Any] = [int(district_id)]
    if active_only:
        where.append("is_active = 1")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            f"SELECT * FROM mandals WHERE {' AND '.join(where)} ORDER BY name",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_areas(district_id: int, mandal_id: int | None = None, active_only: bool = True) -> list[dict]:
    where = ["a.district_id = ?"]
    params: list[Any] = [int(district_id)]
    if mandal_id is not None:
        where.append("a.mandal_id = ?")
        params.append(int(mandal_id))
    if active_only:
        where.append("a.is_active = 1")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            f"""
            SELECT a.*,
                   (SELECT p.pincode FROM pincodes p WHERE p.area_id = a.id ORDER BY p.id LIMIT 1) AS pincode
            FROM areas a
            WHERE {' AND '.join(where)}
            ORDER BY a.name
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def lookup_pincode(pincode: str) -> dict | None:
    """
    Validate 6-digit pincode and return state, district, and matching areas/mandals.
    Returns None if format invalid or pincode absent from pincodes table.
    """
    pin = str(pincode or "").strip()
    if not _PINCODE_RE.match(pin):
        return None
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT p.pincode,
                   s.id AS state_id, s.name AS state_name,
                   d.id AS district_id, d.name AS district_name,
                   m.id AS mandal_id, m.name AS mandal_name,
                   a.id AS area_id, a.name AS area_name,
                   a.latitude, a.longitude
            FROM pincodes p
            JOIN areas a ON a.id = p.area_id
            JOIN districts d ON d.id = a.district_id
            JOIN states s ON s.id = d.state_id
            LEFT JOIN mandals m ON m.id = a.mandal_id
            WHERE p.pincode = ? AND a.is_active = 1 AND d.is_active = 1 AND s.is_active = 1
            ORDER BY a.name ASC
            """,
            (pin,),
        ).fetchall()
    if not rows:
        return None
    first = rows[0]
    return {
        "pincode": pin,
        "state_id": first["state_id"],
        "state_name": first["state_name"],
        "district_id": first["district_id"],
        "district_name": first["district_name"],
        "areas": [
            {
                "area_id": r["area_id"],
                "area_name": r["area_name"],
                "mandal_id": r["mandal_id"],
                "mandal_name": r["mandal_name"],
                "latitude": r["latitude"],
                "longitude": r["longitude"],
            }
            for r in rows
        ],
    }


def resolve_location_selection(
    *,
    pincode: str | None = None,
    state_id: int | None = None,
    district_id: int | None = None,
    mandal_id: int | None = None,
    area_id: int | None = None,
) -> dict[str, Any]:
    """
    Strictly validate and resolve citizen location input from either pincode lookup
    or cascading state -> district -> (mandal) -> area selection.
    """
    clean_pin = str(pincode or "").strip()
    if clean_pin:
        if not _PINCODE_RE.match(clean_pin):
            raise ValueError("Pincode must be exactly 6 digits.")
        pin_info = lookup_pincode(clean_pin)
        if pin_info is None:
            raise ValueError(f"Pincode '{clean_pin}' was not found in the India Post directory.")

        areas = pin_info["areas"]
        chosen_area = None
        if area_id is not None:
            chosen_area = next((a for a in areas if a["area_id"] == int(area_id)), None)
            if chosen_area is None:
                raise ValueError(f"Selected area does not belong to pincode '{clean_pin}'.")
        else:
            chosen_area = areas[0]

        if district_id is not None and int(district_id) != pin_info["district_id"]:
            raise ValueError(f"Selected district does not match pincode '{clean_pin}'.")

        return {
            "state_id": pin_info["state_id"],
            "state_name": pin_info["state_name"],
            "district_id": pin_info["district_id"],
            "district_name": pin_info["district_name"],
            "mandal_id": chosen_area["mandal_id"] if chosen_area else mandal_id,
            "area_id": chosen_area["area_id"] if chosen_area else None,
            "area_name": chosen_area["area_name"] if chosen_area else "",
            "pincode": clean_pin,
            "latitude": chosen_area["latitude"] if chosen_area else None,
            "longitude": chosen_area["longitude"] if chosen_area else None,
        }

    # Cascading selection without pincode input -> auto-resolve pincode from area/district
    with closing(_db.get_connection()) as conn:
        if area_id is not None:
            row = conn.execute(
                """
                SELECT a.id AS area_id, a.name AS area_name, a.mandal_id, a.latitude, a.longitude,
                       d.id AS district_id, d.name AS district_name,
                       s.id AS state_id, s.name AS state_name,
                       (SELECT p.pincode FROM pincodes p WHERE p.area_id = a.id ORDER BY p.id LIMIT 1) AS pincode
                FROM areas a
                JOIN districts d ON d.id = a.district_id
                JOIN states s ON s.id = d.state_id
                WHERE a.id = ? AND a.is_active = 1
                """,
                (int(area_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Selected area does not exist or is inactive.")
            if district_id is not None and int(district_id) != row["district_id"]:
                raise ValueError("Selected area does not belong to the chosen district.")
            if state_id is not None and int(state_id) != row["state_id"]:
                raise ValueError("Selected district does not belong to the chosen state.")
            return {
                "state_id": row["state_id"],
                "state_name": row["state_name"],
                "district_id": row["district_id"],
                "district_name": row["district_name"],
                "mandal_id": mandal_id if mandal_id is not None else row["mandal_id"],
                "area_id": row["area_id"],
                "area_name": row["area_name"],
                "pincode": row["pincode"] or "",
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            }

        if district_id is not None:
            row = conn.execute(
                """
                SELECT d.id AS district_id, d.name AS district_name,
                       s.id AS state_id, s.name AS state_name,
                       (SELECT p.pincode FROM pincodes p WHERE p.district_id = d.id ORDER BY p.id LIMIT 1) AS sample_pincode
                FROM districts d
                JOIN states s ON s.id = d.state_id
                WHERE d.id = ? AND d.is_active = 1
                """,
                (int(district_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Selected district does not exist or is inactive.")
            if state_id is not None and int(state_id) != row["state_id"]:
                raise ValueError("Selected district does not belong to the chosen state.")
            return {
                "state_id": row["state_id"],
                "state_name": row["state_name"],
                "district_id": row["district_id"],
                "district_name": row["district_name"],
                "mandal_id": mandal_id,
                "area_id": None,
                "area_name": "",
                "pincode": row["sample_pincode"] or "",
                "latitude": None,
                "longitude": None,
            }

    return {
        "state_id": state_id,
        "state_name": "",
        "district_id": None,
        "district_name": "",
        "mandal_id": mandal_id,
        "area_id": None,
        "area_name": "",
        "pincode": "",
        "latitude": None,
        "longitude": None,
    }


def get_location_stats(actor: dict | None = None) -> dict[str, Any]:
    with closing(_db.get_connection()) as conn:
        counts = {
            "states": conn.execute("SELECT COUNT(*) FROM states").fetchone()[0],
            "districts": conn.execute("SELECT COUNT(*) FROM districts").fetchone()[0],
            "mandals": conn.execute("SELECT COUNT(*) FROM mandals").fetchone()[0],
            "areas": conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0],
            "pincodes": conn.execute("SELECT COUNT(*) FROM pincodes").fetchone()[0],
        }
        logs = conn.execute(
            "SELECT * FROM location_load_logs ORDER BY loaded_at DESC, id DESC LIMIT 10"
        ).fetchall()
    return {"counts": counts, "load_logs": [dict(r) for r in logs]}


def reload_locations_admin(
    actor: dict | None,
    csv_path: str | Path | None = None,
    filter_to_default_state: bool = True,
) -> dict[str, Any]:
    require(actor, "manage_locations")
    return load_locations_from_csv(
        csv_path=csv_path,
        filter_to_default_state=filter_to_default_state,
    )
