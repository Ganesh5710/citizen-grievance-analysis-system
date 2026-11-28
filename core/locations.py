"""
core/locations.py
Hierarchical address & India Post / LGD location taxonomy:
Country > State > District > Sub-district (Mandal, optional) > Town / Village (optional) > Area / Locality > Pincode.
Defaults to DEFAULT_COUNTRY ('India') and DEFAULT_STATE ('Andhra Pradesh') from .env/config.
"""

from __future__ import annotations

from contextlib import closing
import csv
import json
from pathlib import Path
import re
from typing import Any

from core.config import PROJECT_ROOT, get_env_str
import core.database as _db
from core.permissions import require

DEFAULT_LOCATION_CSV = PROJECT_ROOT / "data" / "india_post_pincodes_ap.csv"
DEFAULT_LGD_CSV = PROJECT_ROOT / "data" / "lgd_ap_villages.csv"
DEFAULT_INDIA_POST_DOWNLOAD_DATE = "2026-11-28"
DEFAULT_LGD_DOWNLOAD_DATE = "2026-11-28"

_PINCODE_RE = re.compile(r"^\d{6}$")

# Post-2022 Andhra Pradesh 26/28-district & India Post spelling unification table
_AP_DISTRICT_ALIASES: dict[str, str] = {
    "anantapur": "Ananthapuramu",
    "ananthapur": "Ananthapuramu",
    "ananthapuramu": "Ananthapuramu",
    "anantapuramu": "Ananthapuramu",
    "spsr nellore": "Sri Potti Sriramulu Nellore",
    "sri potti sriramulu nellore": "Sri Potti Sriramulu Nellore",
    "nellore": "Sri Potti Sriramulu Nellore",
    "y.s.r.": "Y.S.R. Kadapa",
    "y.s.r": "Y.S.R. Kadapa",
    "ysr": "Y.S.R. Kadapa",
    "ysr kadapa": "Y.S.R. Kadapa",
    "y.s.r. kadapa": "Y.S.R. Kadapa",
    "kadapa": "Y.S.R. Kadapa",
    "cuddapah": "Y.S.R. Kadapa",
    "konaseema": "Dr. B.R. Ambedkar Konaseema",
    "kona seema": "Dr. B.R. Ambedkar Konaseema",
    "dr. b.r. ambedkar konaseema": "Dr. B.R. Ambedkar Konaseema",
    "dr. br ambedkar konaseema": "Dr. B.R. Ambedkar Konaseema",
    "dr b r ambedkar konaseema": "Dr. B.R. Ambedkar Konaseema",
    "ntr": "NTR",
    "n.t.r.": "NTR",
    "n.t.r": "NTR",
    "parvatipuram manyam": "Parvathipuram Manyam",
    "parvathipuram manyam": "Parvathipuram Manyam",
    "alluri sitaramaraju": "Alluri Sitharama Raju",
    "alluri sitharama raju": "Alluri Sitharama Raju",
    "sri satyasai": "Sri Sathya Sai",
    "sri sathya sai": "Sri Sathya Sai",
    "visakhapatanam": "Visakhapatnam",
    "vizag": "Visakhapatnam",
    "anakapalle": "Anakapalli",
    "anakapalli": "Anakapalli",
}


def get_default_country_name() -> str:
    return get_env_str("DEFAULT_COUNTRY", "India")


def get_default_state_name() -> str:
    return get_env_str("DEFAULT_STATE", "Andhra Pradesh")


def normalize_location_name(raw: str) -> str:
    """Collapse extra whitespace and title-case place names consistently while keeping B.O/S.O/H.O neat."""
    cleaned = " ".join(str(raw or "").strip().split())
    if not cleaned:
        return ""
    titled = cleaned.title()
    for suffix in ("B.O", "S.O", "H.O", "Bo", "So", "Ho"):
        titled = re.sub(rf"\b{re.escape(suffix)}$", suffix.upper(), titled)
    return titled


def normalize_district_name(raw: str) -> str:
    """Normalize spacing/casing and unify post-2022 AP district naming variants."""
    cleaned = " ".join(str(raw or "").strip().split())
    if not cleaned:
        return ""
    key = cleaned.lower()
    if key in _AP_DISTRICT_ALIASES:
        return _AP_DISTRICT_ALIASES[key]
    return normalize_location_name(cleaned)


def _strip_po_suffix(area_name: str) -> str:
    return re.sub(r"\s+(B\.?O|S\.?O|H\.?O|R\.?S)\.?$", "", area_name.strip(), flags=re.IGNORECASE).strip()


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


def _ensure_country_and_state(
    conn,
    country_name: str,
    state_name: str,
    country_cache: dict[str, int],
    state_cache: dict[str, int],
) -> tuple[int, int, int, int]:
    """Return (country_id, state_id, new_countries, new_states)."""
    c_key = country_name.lower()
    new_c = 0
    if c_key not in country_cache:
        cur = conn.execute(
            "INSERT INTO countries (name, code, is_active) VALUES (?, ?, 1)",
            (country_name, "IN" if c_key == "india" else ""),
        )
        country_cache[c_key] = cur.lastrowid
        new_c = 1
    country_id = country_cache[c_key]

    s_key = state_name.lower()
    new_s = 0
    if s_key not in state_cache:
        cur = conn.execute(
            "INSERT INTO states (country_id, name, code, is_active) VALUES (?, ?, ?, 1)",
            (country_id, state_name, "AP" if s_key == "andhra pradesh" else ""),
        )
        state_cache[s_key] = cur.lastrowid
        new_s = 1
    else:
        conn.execute(
            "UPDATE states SET country_id = COALESCE(country_id, ?) WHERE id = ?",
            (country_id, state_cache[s_key]),
        )
    return country_id, state_cache[s_key], new_c, new_s


def _refresh_district_centroids_in_conn(conn) -> None:
    conn.execute(
        """
        UPDATE districts
        SET latitude = (
                SELECT ROUND(AVG(a.latitude), 6) FROM areas a
                WHERE a.district_id = districts.id AND a.latitude IS NOT NULL
            ),
            longitude = (
                SELECT ROUND(AVG(a.longitude), 6) FROM areas a
                WHERE a.district_id = districts.id AND a.longitude IS NOT NULL
            )
        WHERE EXISTS (
            SELECT 1 FROM areas a WHERE a.district_id = districts.id AND a.latitude IS NOT NULL
        )
        """
    )


def load_locations_from_csv(
    csv_path: str | Path | None = None,
    *,
    default_country: str | None = None,
    default_state: str | None = None,
    download_date: str | None = None,
    filter_to_default_state: bool = True,
) -> dict[str, Any]:
    """
    Idempotently load countries, states, districts, optional mandals, areas, and pincodes
    from an official India Post / data.gov.in CSV placed in data/.
    Does NOT invent location data. Missing mandal columns are cleanly skipped and logged.
    """
    src = Path(csv_path) if csv_path else DEFAULT_LOCATION_CSV
    if not src.exists():
        raise FileNotFoundError(f"Location CSV not found: {src}")

    target_country_norm = normalize_location_name(default_country or get_default_country_name()) or "India"
    target_state_norm = normalize_location_name(default_state or get_default_state_name())
    dl_date = (download_date or DEFAULT_INDIA_POST_DOWNLOAD_DATE).strip()

    with open(src, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        raw_fields = list(reader.fieldnames or [])
        lower_to_orig = {col.strip().lower(): col for col in raw_fields}

        def _find_col(*candidates: str) -> str | None:
            for c in candidates:
                if c in lower_to_orig:
                    return lower_to_orig[c]
            return None

        col_country = _find_col("countryname", "country", "country_name")
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

    loaded_countries = 0
    loaded_states = 0
    loaded_districts = 0
    loaded_mandals = 0
    loaded_areas = 0
    loaded_pincodes = 0
    processed_valid = 0

    with closing(_db.get_connection()) as conn, conn:
        country_cache: dict[str, int] = {
            r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM countries").fetchall()
        }
        state_cache: dict[str, int] = {
            r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM states").fetchall()
        }
        dist_cache: dict[tuple[int, str], int] = {
            (r["state_id"], normalize_district_name(r["name"]).lower()): r["id"]
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

            raw_country = row.get(col_country) if col_country else target_country_norm
            country_name = normalize_location_name(raw_country or "") or target_country_norm

            raw_state = row.get(col_state) if col_state else target_state_norm
            state_name = normalize_location_name(raw_state or "")
            if not state_name:
                _record_skip(row_num, "missing_state", row)
                continue

            if filter_to_default_state and state_name.lower() != target_state_norm.lower():
                _record_skip(row_num, "filtered_other_state", row)
                continue

            dist_name = normalize_district_name(row.get(col_dist) or "")
            if not dist_name or dist_name.upper() in ("NA", "NULL", "NONE"):
                _record_skip(row_num, "missing_district", row)
                continue

            area_name = normalize_location_name(row.get(col_area) or "")
            if not area_name or area_name.upper() in ("NA", "NULL", "NONE"):
                _record_skip(row_num, "missing_area", row)
                continue

            lat = _parse_coord(row.get(col_lat) if col_lat else None, -90.0, 90.0)
            lon = _parse_coord(row.get(col_lon) if col_lon else None, -180.0, 180.0)

            _cid, state_id, nc, ns = _ensure_country_and_state(
                conn, country_name, state_name, country_cache, state_cache
            )
            loaded_countries += nc
            loaded_states += ns

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
            elif lat is not None and lon is not None:
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

        _refresh_district_centroids_in_conn(conn)

        notes = (
            "Loaded mandal hierarchy from CSV."
            if mandal_present
            else "Skipped mandal level: source CSV lacks mandal/taluk column (load LGD CSV for full hierarchy)."
        )
        conn.execute(
            """
            INSERT INTO location_load_logs
                (source_path, source_dataset, source_download_date, default_state, total_rows,
                 loaded_mandals, loaded_towns_villages, loaded_areas, loaded_pincodes,
                 skipped_rows, skipped_reasons_json, mandal_level_present, notes, loaded_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(src),
                "India Post Pincode Directory (data.gov.in)",
                dl_date,
                target_state_norm,
                total_rows,
                loaded_mandals,
                loaded_areas,
                loaded_pincodes,
                sum(skipped_reasons.values()),
                json.dumps(skipped_reasons),
                1 if mandal_present else 0,
                notes,
                _db.utc_now_str(),
            ),
        )

    return {
        "source_path": str(src),
        "source_dataset": "India Post Pincode Directory (data.gov.in)",
        "source_download_date": dl_date,
        "default_country": target_country_norm,
        "default_state": target_state_norm,
        "total_rows": total_rows,
        "processed_valid_rows": processed_valid,
        "loaded_countries": loaded_countries,
        "loaded_states": loaded_states,
        "loaded_districts": loaded_districts,
        "loaded_mandals": loaded_mandals,
        "loaded_towns_villages": 0,
        "loaded_areas": loaded_areas,
        "loaded_pincodes": loaded_pincodes,
        "skipped_rows": sum(skipped_reasons.values()),
        "skipped_reasons": skipped_reasons,
        "skipped_samples": skipped_samples,
        "mandal_level_present": mandal_present,
        "notes": notes,
    }


def load_lgd_villages_csv(
    csv_path: str | Path | None = None,
    *,
    default_country: str | None = None,
    default_state: str | None = None,
    download_date: str | None = None,
    filter_to_default_state: bool = True,
) -> dict[str, Any]:
    """
    Idempotently load Local Government Directory (LGD) District > Sub-district (Mandal) > Town/Village
    hierarchy from data/lgd_ap_villages.csv and link India Post areas sharing exact village names or pincodes.
    """
    src = Path(csv_path) if csv_path else DEFAULT_LGD_CSV
    if not src.exists():
        raise FileNotFoundError(f"LGD village CSV not found: {src}")

    target_country_norm = normalize_location_name(default_country or get_default_country_name()) or "India"
    target_state_norm = normalize_location_name(default_state or get_default_state_name())
    dl_date = (download_date or DEFAULT_LGD_DOWNLOAD_DATE).strip()

    with open(src, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        raw_fields = list(reader.fieldnames or [])
        lower_to_orig = {col.strip().lower(): col for col in raw_fields}

        def _find_col(*candidates: str) -> str | None:
            for c in candidates:
                if c in lower_to_orig:
                    return lower_to_orig[c]
            return None

        col_state = _find_col("state", "statename", "state_name")
        col_dist = _find_col("district", "districtname", "district_name")
        col_mandal = _find_col("mandal", "mandalname", "subdistrict", "sub_district", "taluk", "taluka", "block")
        col_tv = _find_col("village", "villagename", "village_name", "town", "town_village")
        col_lgd_code = _find_col("village code", "village_code", "lgd_code")
        col_pin = _find_col("pincode", "pin", "pin_code")
        col_cat = _find_col("category", "type")

        if not col_dist or not col_mandal or not col_tv:
            raise ValueError(
                f"LGD CSV missing required columns (District/Mandal/Village). Found: {raw_fields}"
            )
        rows = list(reader)

    skipped_reasons: dict[str, int] = {}
    skipped_samples: list[dict[str, Any]] = []
    loaded_districts = 0
    loaded_mandals = 0
    loaded_tvs = 0
    linked_areas = 0
    processed_valid = 0

    def _record_skip(row_idx: int, reason: str, raw_row: dict) -> None:
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
        if len(skipped_samples) < 50:
            skipped_samples.append({
                "row": row_idx,
                "reason": reason,
                "district": str(raw_row.get(col_dist, "")),
                "mandal": str(raw_row.get(col_mandal, "")),
                "village": str(raw_row.get(col_tv, "")),
            })

    with closing(_db.get_connection()) as conn, conn:
        country_cache: dict[str, int] = {
            r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM countries").fetchall()
        }
        state_cache: dict[str, int] = {
            r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM states").fetchall()
        }
        dist_cache: dict[tuple[int, str], int] = {
            (r["state_id"], normalize_district_name(r["name"]).lower()): r["id"]
            for r in conn.execute("SELECT id, state_id, name FROM districts").fetchall()
        }
        mandal_cache: dict[tuple[int, str], int] = {
            (r["district_id"], r["name"].lower()): r["id"]
            for r in conn.execute("SELECT id, district_id, name FROM mandals").fetchall()
        }
        tv_cache: dict[tuple[int, int, str], int] = {
            (r["district_id"], r["mandal_id"] or 0, r["name"].lower()): r["id"]
            for r in conn.execute("SELECT id, district_id, mandal_id, name FROM towns_villages").fetchall()
        }

        # Map (district_id, base_name_lower) -> list[area_id] to link PO names matching villages
        area_base_index: dict[tuple[int, str], list[int]] = {}
        for a_row in conn.execute("SELECT id, district_id, name, town_village_id FROM areas").fetchall():
            base = _strip_po_suffix(a_row["name"]).lower()
            area_base_index.setdefault((a_row["district_id"], base), []).append(a_row["id"])

        for row_num, row in enumerate(rows, start=2):
            state_name = normalize_location_name((row.get(col_state) if col_state else None) or target_state_norm)
            if not state_name:
                _record_skip(row_num, "missing_state", row)
                continue
            if filter_to_default_state and state_name.lower() != target_state_norm.lower():
                _record_skip(row_num, "filtered_other_state", row)
                continue

            dist_name = normalize_district_name(row.get(col_dist) or "")
            if not dist_name or dist_name.upper() in ("NA", "NULL", "NONE"):
                _record_skip(row_num, "missing_district", row)
                continue

            mandal_name = normalize_location_name(row.get(col_mandal) or "")
            if not mandal_name or mandal_name.upper() in ("NA", "NULL", "NONE"):
                _record_skip(row_num, "missing_mandal", row)
                continue

            tv_name = normalize_location_name(row.get(col_tv) or "")
            if not tv_name or tv_name.upper() in ("NA", "NULL", "NONE"):
                _record_skip(row_num, "missing_village", row)
                continue

            pin_val = str((row.get(col_pin) if col_pin else "") or "").strip()
            if pin_val and not _PINCODE_RE.match(pin_val):
                pin_val = ""

            _cid, state_id, _, _ = _ensure_country_and_state(
                conn, target_country_norm, state_name, country_cache, state_cache
            )

            d_key = (state_id, dist_name.lower())
            if d_key not in dist_cache:
                cur = conn.execute(
                    "INSERT INTO districts (state_id, name, is_active) VALUES (?, ?, 1)",
                    (state_id, dist_name),
                )
                dist_cache[d_key] = cur.lastrowid
                loaded_districts += 1
            district_id = dist_cache[d_key]

            m_key = (district_id, mandal_name.lower())
            if m_key not in mandal_cache:
                cur = conn.execute(
                    "INSERT INTO mandals (district_id, name, is_active) VALUES (?, ?, 1)",
                    (district_id, mandal_name),
                )
                mandal_cache[m_key] = cur.lastrowid
                loaded_mandals += 1
            mandal_id = mandal_cache[m_key]

            tv_key = (district_id, mandal_id, tv_name.lower())
            if tv_key not in tv_cache:
                lgd_code = str((row.get(col_lgd_code) if col_lgd_code else "") or "").strip()
                cat = str((row.get(col_cat) if col_cat else "") or "").strip()
                cur = conn.execute(
                    """
                    INSERT INTO towns_villages (district_id, mandal_id, name, lgd_code, pincode, category, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (district_id, mandal_id, tv_name, lgd_code, pin_val, cat),
                )
                tv_cache[tv_key] = cur.lastrowid
                loaded_tvs += 1
            tv_id = tv_cache[tv_key]

            # Link India Post post-office areas with matching base name inside same district
            matched_area_ids = area_base_index.get((district_id, tv_name.lower()), [])
            for aid in matched_area_ids:
                cur = conn.execute(
                    "UPDATE areas SET mandal_id = COALESCE(mandal_id, ?), "
                    "town_village_id = COALESCE(town_village_id, ?) WHERE id = ? AND town_village_id IS NULL",
                    (mandal_id, tv_id, aid),
                )
                linked_areas += int(cur.rowcount or 0)
            processed_valid += 1

        _refresh_district_centroids_in_conn(conn)
        notes = (
            f"Loaded LGD sub-districts ({loaded_mandals} new) & towns/villages ({loaded_tvs} new); "
            f"linked {linked_areas} India Post areas."
        )
        conn.execute(
            """
            INSERT INTO location_load_logs
                (source_path, source_dataset, source_download_date, default_state, total_rows,
                 loaded_mandals, loaded_towns_villages, loaded_areas, loaded_pincodes,
                 skipped_rows, skipped_reasons_json, mandal_level_present, notes, loaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 1, ?, ?)
            """,
            (
                str(src),
                "Local Government Directory AP (lgdirectory.gov.in)",
                dl_date,
                target_state_norm,
                len(rows),
                loaded_mandals,
                loaded_tvs,
                linked_areas,
                sum(skipped_reasons.values()),
                json.dumps(skipped_reasons),
                notes,
                _db.utc_now_str(),
            ),
        )

    return {
        "source_path": str(src),
        "source_dataset": "Local Government Directory AP (lgdirectory.gov.in)",
        "source_download_date": dl_date,
        "default_state": target_state_norm,
        "total_rows": len(rows),
        "processed_valid_rows": processed_valid,
        "loaded_districts": loaded_districts,
        "loaded_mandals": loaded_mandals,
        "loaded_towns_villages": loaded_tvs,
        "linked_areas": linked_areas,
        "skipped_rows": sum(skipped_reasons.values()),
        "skipped_reasons": skipped_reasons,
        "skipped_samples": skipped_samples,
        "notes": notes,
    }


def load_all_default_locations(*, filter_to_default_state: bool = True) -> dict[str, Any]:
    """Load India Post directory and LGD sub-district/village CSV if present."""
    ip_summary = load_locations_from_csv(filter_to_default_state=filter_to_default_state)
    lgd_summary = None
    if DEFAULT_LGD_CSV.exists():
        lgd_summary = load_lgd_villages_csv(filter_to_default_state=filter_to_default_state)
    return {"india_post": ip_summary, "lgd": lgd_summary}


def get_countries(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM countries" + (" WHERE is_active = 1" if active_only else "") + " ORDER BY name"
    with closing(_db.get_connection()) as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_default_country() -> dict | None:
    target = get_default_country_name().strip().lower()
    with closing(_db.get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM countries WHERE lower(name) = ? AND is_active = 1", (target,)
        ).fetchone()
        if row:
            return dict(row)
        first = conn.execute("SELECT * FROM countries WHERE is_active = 1 ORDER BY id LIMIT 1").fetchone()
    return dict(first) if first else None


def get_states(country_id: int | None = None, active_only: bool = True) -> list[dict]:
    where = []
    params: list[Any] = []
    if country_id is not None:
        where.append("country_id = ?")
        params.append(int(country_id))
    if active_only:
        where.append("is_active = 1")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with closing(_db.get_connection()) as conn:
        return [dict(r) for r in conn.execute(f"SELECT * FROM states{clause} ORDER BY name", params).fetchall()]


def get_default_state(country_id: int | None = None) -> dict | None:
    target = get_default_state_name().strip().lower()
    where_c = " AND country_id = ?" if country_id is not None else ""
    params: list[Any] = [target] + ([int(country_id)] if country_id is not None else [])
    with closing(_db.get_connection()) as conn:
        row = conn.execute(
            f"SELECT * FROM states WHERE lower(name) = ? AND is_active = 1{where_c}", params
        ).fetchone()
        if row:
            return dict(row)
        if country_id is not None:
            first = conn.execute(
                "SELECT * FROM states WHERE country_id = ? AND is_active = 1 ORDER BY id LIMIT 1",
                (int(country_id),),
            ).fetchone()
        else:
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


def get_towns_villages(
    district_id: int,
    mandal_id: int | None = None,
    active_only: bool = True,
) -> list[dict]:
    where = ["district_id = ?"]
    params: list[Any] = [int(district_id)]
    if mandal_id is not None:
        where.append("mandal_id = ?")
        params.append(int(mandal_id))
    if active_only:
        where.append("is_active = 1")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            f"SELECT * FROM towns_villages WHERE {' AND '.join(where)} ORDER BY name",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_areas(
    district_id: int,
    mandal_id: int | None = None,
    town_village_id: int | None = None,
    active_only: bool = True,
) -> list[dict]:
    active_sql = " AND a.is_active = 1" if active_only else ""
    with closing(_db.get_connection()) as conn:
        if town_village_id is not None:
            rows = conn.execute(
                f"""
                SELECT a.*,
                       (SELECT p.pincode FROM pincodes p WHERE p.area_id = a.id ORDER BY p.id LIMIT 1) AS pincode
                FROM areas a
                WHERE a.district_id = ? AND a.town_village_id = ?{active_sql}
                ORDER BY a.name
                """,
                (int(district_id), int(town_village_id)),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
            # Fallback to areas sharing the town/village's mapped pincode inside the same district
            tv_row = conn.execute(
                "SELECT pincode FROM towns_villages WHERE id = ? AND district_id = ?",
                (int(town_village_id), int(district_id)),
            ).fetchone()
            tv_pin = str(tv_row["pincode"] or "").strip() if tv_row else ""
            if tv_pin:
                pin_rows = conn.execute(
                    f"""
                    SELECT DISTINCT a.*, p.pincode AS pincode
                    FROM areas a
                    JOIN pincodes p ON p.area_id = a.id
                    WHERE a.district_id = ? AND p.pincode = ?{active_sql}
                    ORDER BY a.name
                    """,
                    (int(district_id), tv_pin),
                ).fetchall()
                if pin_rows:
                    return [dict(r) for r in pin_rows]
            return []

        if mandal_id is not None:
            rows = conn.execute(
                f"""
                SELECT a.*,
                       (SELECT p.pincode FROM pincodes p WHERE p.area_id = a.id ORDER BY p.id LIMIT 1) AS pincode
                FROM areas a
                WHERE a.district_id = ? AND a.mandal_id = ?{active_sql}
                ORDER BY a.name
                """,
                (int(district_id), int(mandal_id)),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
            # Areas sharing pincodes of towns/villages in that mandal inside the same district
            pin_rows = conn.execute(
                f"""
                SELECT DISTINCT a.*, p.pincode AS pincode
                FROM areas a
                JOIN pincodes p ON p.area_id = a.id
                WHERE a.district_id = ?
                  AND p.pincode IN (
                      SELECT DISTINCT tv.pincode FROM towns_villages tv
                      WHERE tv.district_id = ? AND tv.mandal_id = ? AND COALESCE(tv.pincode, '') <> ''
                  ){active_sql}
                ORDER BY a.name
                """,
                (int(district_id), int(district_id), int(mandal_id)),
            ).fetchall()
            if pin_rows:
                return [dict(r) for r in pin_rows]
            return []

        rows = conn.execute(
            f"""
            SELECT a.*,
                   (SELECT p.pincode FROM pincodes p WHERE p.area_id = a.id ORDER BY p.id LIMIT 1) AS pincode
            FROM areas a
            WHERE a.district_id = ?{active_sql}
            ORDER BY a.name
            """,
            (int(district_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def lookup_pincode(pincode: str) -> dict | None:
    """
    Validate 6-digit pincode and return country, state, district, and matching areas/mandals/villages.
    Returns None if format invalid or pincode absent from pincodes table.
    """
    pin = str(pincode or "").strip()
    if not _PINCODE_RE.match(pin):
        return None
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT p.pincode,
                   COALESCE(co.id, 1) AS country_id, COALESCE(co.name, 'India') AS country_name,
                   s.id AS state_id, s.name AS state_name,
                   d.id AS district_id, d.name AS district_name,
                   m.id AS mandal_id, m.name AS mandal_name,
                   tv.id AS town_village_id, tv.name AS town_village_name,
                   a.id AS area_id, a.name AS area_name,
                   a.latitude, a.longitude
            FROM pincodes p
            JOIN areas a ON a.id = p.area_id
            JOIN districts d ON d.id = a.district_id
            JOIN states s ON s.id = d.state_id
            LEFT JOIN countries co ON co.id = s.country_id
            LEFT JOIN mandals m ON m.id = a.mandal_id
            LEFT JOIN towns_villages tv ON tv.id = a.town_village_id
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
        "country_id": first["country_id"],
        "country_name": first["country_name"],
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
                "town_village_id": r["town_village_id"],
                "town_village_name": r["town_village_name"],
                "latitude": r["latitude"],
                "longitude": r["longitude"],
            }
            for r in rows
        ],
    }


def build_formatted_address(
    *,
    house_no: str = "",
    street: str = "",
    landmark: str = "",
    area_name: str = "",
    town_village_name: str = "",
    mandal_name: str = "",
    district_name: str = "",
    state_name: str = "",
    pincode: str = "",
    country_name: str = "",
    digipin: str = "",
) -> str:
    """Build a consistent human-readable address string from structured components."""
    parts: list[str] = []
    for v in (house_no, street, landmark, area_name, town_village_name, mandal_name, district_name):
        s = str(v or "").strip()
        if s and s not in parts:
            parts.append(s)
    st_s = str(state_name or "").strip()
    pin_s = str(pincode or "").strip()
    if st_s and pin_s:
        parts.append(f"{st_s} - {pin_s}")
    elif st_s:
        parts.append(st_s)
    elif pin_s:
        parts.append(pin_s)
    c_s = str(country_name or "").strip()
    if c_s:
        parts.append(c_s)
    dp_s = str(digipin or "").strip()
    if dp_s:
        parts.append(f"DIGIPIN: {dp_s}")
    return ", ".join(parts)


def resolve_location_selection(
    *,
    pincode: str | None = None,
    country_id: int | None = None,
    state_id: int | None = None,
    district_id: int | None = None,
    mandal_id: int | None = None,
    town_village_id: int | None = None,
    area_id: int | None = None,
) -> dict[str, Any]:
    """
    Strictly validate and resolve citizen location input from pincode and/or cascading
    country -> state -> district -> (mandal) -> (town/village) -> area selection.
    Never trusts arbitrary mismatched UI IDs: every child must belong to its parent,
    and pincode must exist and match area/district. Rejects mismatches with ValueError.
    """
    clean_pin = str(pincode or "").strip()
    with closing(_db.get_connection()) as conn:
        # 1. Validate Country / State / District / Mandal / TownVillage hierarchy relationships explicitly
        if state_id is not None:
            st_row = conn.execute(
                "SELECT id, country_id, name FROM states WHERE id = ? AND is_active = 1",
                (int(state_id),),
            ).fetchone()
            if st_row is None:
                raise ValueError("Selected state does not exist or is inactive.")
            if country_id is not None and st_row["country_id"] is not None and int(st_row["country_id"]) != int(country_id):
                raise ValueError("Selected state does not belong to the chosen country.")

        d_row = None
        if district_id is not None:
            d_row = conn.execute(
                """
                SELECT d.id, d.state_id, d.name, d.latitude, d.longitude,
                       s.name AS state_name, s.country_id,
                       COALESCE(co.name, 'India') AS country_name
                FROM districts d
                JOIN states s ON s.id = d.state_id
                LEFT JOIN countries co ON co.id = s.country_id
                WHERE d.id = ? AND d.is_active = 1
                """,
                (int(district_id),),
            ).fetchone()
            if d_row is None:
                raise ValueError("Selected district does not exist or is inactive.")
            if state_id is not None and int(d_row["state_id"]) != int(state_id):
                raise ValueError("Selected district does not belong to the chosen state.")
            if country_id is not None and d_row["country_id"] is not None and int(d_row["country_id"]) != int(country_id):
                raise ValueError("Selected district does not belong to the chosen country.")

        m_row = None
        if mandal_id is not None:
            m_row = conn.execute(
                "SELECT id, district_id, name FROM mandals WHERE id = ? AND is_active = 1",
                (int(mandal_id),),
            ).fetchone()
            if m_row is None:
                raise ValueError("Selected mandal/sub-district does not exist or is inactive.")
            if district_id is not None and int(m_row["district_id"]) != int(district_id):
                raise ValueError("Selected mandal/sub-district does not belong to the chosen district.")

        tv_row = None
        if town_village_id is not None:
            tv_row = conn.execute(
                "SELECT id, district_id, mandal_id, name, pincode FROM towns_villages WHERE id = ? AND is_active = 1",
                (int(town_village_id),),
            ).fetchone()
            if tv_row is None:
                raise ValueError("Selected town/village does not exist or is inactive.")
            if district_id is not None and int(tv_row["district_id"]) != int(district_id):
                raise ValueError("Selected town/village does not belong to the chosen district.")
            if mandal_id is not None and tv_row["mandal_id"] is not None and int(tv_row["mandal_id"]) != int(mandal_id):
                raise ValueError("Selected town/village does not belong to the chosen mandal.")

        a_row = None
        if area_id is not None:
            a_row = conn.execute(
                """
                SELECT a.id, a.district_id, a.mandal_id, a.town_village_id, a.name,
                       a.latitude, a.longitude,
                       d.state_id, d.name AS district_name, d.latitude AS d_lat, d.longitude AS d_lon,
                       s.name AS state_name, COALESCE(s.country_id, 1) AS country_id,
                       COALESCE(co.name, 'India') AS country_name,
                       (SELECT p.pincode FROM pincodes p WHERE p.area_id = a.id ORDER BY p.id LIMIT 1) AS area_pincode
                FROM areas a
                JOIN districts d ON d.id = a.district_id
                JOIN states s ON s.id = d.state_id
                LEFT JOIN countries co ON co.id = s.country_id
                WHERE a.id = ? AND a.is_active = 1
                """,
                (int(area_id),),
            ).fetchone()
            if a_row is None:
                raise ValueError("Selected area does not exist or is inactive.")
            if district_id is not None and int(a_row["district_id"]) != int(district_id):
                raise ValueError("Selected area does not belong to the chosen district.")
            if state_id is not None and int(a_row["state_id"]) != int(state_id):
                raise ValueError("Selected area does not belong to the chosen state.")
            if country_id is not None and int(a_row["country_id"]) != int(country_id):
                raise ValueError("Selected area does not belong to the chosen country.")
            if mandal_id is not None and a_row["mandal_id"] is not None and int(a_row["mandal_id"]) != int(mandal_id):
                raise ValueError("Selected area does not belong to the chosen mandal.")
            if town_village_id is not None:
                if a_row["town_village_id"] is not None and int(a_row["town_village_id"]) != int(town_village_id):
                    raise ValueError("Selected area does not belong to the chosen town/village.")

        # 2. Validate Pincode branch (strict cross-check against area / district / state / country)
        if clean_pin:
            if not _PINCODE_RE.match(clean_pin):
                raise ValueError("Pincode must be exactly 6 digits.")
            pin_info = lookup_pincode(clean_pin)
            if pin_info is None:
                raise ValueError(f"Pincode '{clean_pin}' was not found in the India Post directory.")

            if country_id is not None and pin_info["country_id"] != int(country_id):
                raise ValueError(f"Selected country does not match pincode '{clean_pin}'.")
            if state_id is not None and pin_info["state_id"] != int(state_id):
                raise ValueError(f"Selected state does not match pincode '{clean_pin}'.")
            if district_id is not None and pin_info["district_id"] != int(district_id):
                raise ValueError(f"Selected district does not match pincode '{clean_pin}'.")

            pin_areas = pin_info["areas"]
            if area_id is not None:
                chosen_area = next((a for a in pin_areas if a["area_id"] == int(area_id)), None)
                if chosen_area is None:
                    raise ValueError(f"Selected area does not belong to pincode '{clean_pin}'.")
            else:
                chosen_area = pin_areas[0]

            eff_lat = chosen_area["latitude"]
            eff_lon = chosen_area["longitude"]
            coord_source = "area_centroid" if (eff_lat is not None and eff_lon is not None) else "none"
            if eff_lat is None or eff_lon is None:
                dist_coords = conn.execute(
                    "SELECT latitude, longitude FROM districts WHERE id = ?",
                    (pin_info["district_id"],),
                ).fetchone()
                if dist_coords and dist_coords["latitude"] is not None and dist_coords["longitude"] is not None:
                    eff_lat = dist_coords["latitude"]
                    eff_lon = dist_coords["longitude"]
                    coord_source = "district_centroid"

            eff_tv_id = town_village_id if town_village_id is not None else chosen_area.get("town_village_id")
            eff_tv_name = tv_row["name"] if tv_row else (chosen_area.get("town_village_name") or "")
            eff_m_id = mandal_id if mandal_id is not None else chosen_area.get("mandal_id")
            eff_m_name = m_row["name"] if m_row else (chosen_area.get("mandal_name") or "")

            return {
                "country_id": pin_info["country_id"],
                "country_name": pin_info["country_name"],
                "state_id": pin_info["state_id"],
                "state_name": pin_info["state_name"],
                "district_id": pin_info["district_id"],
                "district_name": pin_info["district_name"],
                "mandal_id": eff_m_id,
                "mandal_name": eff_m_name,
                "town_village_id": eff_tv_id,
                "town_village_name": eff_tv_name,
                "area_id": chosen_area["area_id"],
                "area_name": chosen_area["area_name"],
                "pincode": clean_pin,
                "latitude": eff_lat,
                "longitude": eff_lon,
                "coord_source": coord_source,
            }

        # 3. Cascading resolution without explicit pincode -> auto-fill from area / town / district
        if a_row is not None:
            eff_lat = a_row["latitude"]
            eff_lon = a_row["longitude"]
            coord_source = "area_centroid" if (eff_lat is not None and eff_lon is not None) else "none"
            if eff_lat is None or eff_lon is None:
                if a_row["d_lat"] is not None and a_row["d_lon"] is not None:
                    eff_lat = a_row["d_lat"]
                    eff_lon = a_row["d_lon"]
                    coord_source = "district_centroid"

            eff_m_id = mandal_id if mandal_id is not None else a_row["mandal_id"]
            eff_tv_id = town_village_id if town_village_id is not None else a_row["town_village_id"]
            eff_m_name = m_row["name"] if m_row else ""
            if not eff_m_name and eff_m_id is not None:
                mr = conn.execute("SELECT name FROM mandals WHERE id = ?", (eff_m_id,)).fetchone()
                eff_m_name = mr["name"] if mr else ""
            eff_tv_name = tv_row["name"] if tv_row else ""
            if not eff_tv_name and eff_tv_id is not None:
                tr = conn.execute("SELECT name FROM towns_villages WHERE id = ?", (eff_tv_id,)).fetchone()
                eff_tv_name = tr["name"] if tr else ""

            return {
                "country_id": a_row["country_id"],
                "country_name": a_row["country_name"],
                "state_id": a_row["state_id"],
                "state_name": a_row["state_name"],
                "district_id": a_row["district_id"],
                "district_name": a_row["district_name"],
                "mandal_id": eff_m_id,
                "mandal_name": eff_m_name,
                "town_village_id": eff_tv_id,
                "town_village_name": eff_tv_name,
                "area_id": a_row["id"],
                "area_name": a_row["name"],
                "pincode": a_row["area_pincode"] or "",
                "latitude": eff_lat,
                "longitude": eff_lon,
                "coord_source": coord_source,
            }

        if d_row is not None:
            samp_pin = conn.execute(
                "SELECT p.pincode FROM pincodes p WHERE p.district_id = ? ORDER BY p.id LIMIT 1",
                (d_row["id"],),
            ).fetchone()
            eff_lat = d_row["latitude"]
            eff_lon = d_row["longitude"]
            coord_source = "district_centroid" if (eff_lat is not None and eff_lon is not None) else "none"
            return {
                "country_id": d_row["country_id"] or 1,
                "country_name": d_row["country_name"],
                "state_id": d_row["state_id"],
                "state_name": d_row["state_name"],
                "district_id": d_row["id"],
                "district_name": d_row["name"],
                "mandal_id": mandal_id,
                "mandal_name": m_row["name"] if m_row else "",
                "town_village_id": town_village_id,
                "town_village_name": tv_row["name"] if tv_row else "",
                "area_id": None,
                "area_name": "",
                "pincode": (tv_row["pincode"] if tv_row and tv_row["pincode"] else None)
                or (samp_pin["pincode"] if samp_pin else ""),
                "latitude": eff_lat,
                "longitude": eff_lon,
                "coord_source": coord_source,
            }

    return {
        "country_id": country_id,
        "country_name": "",
        "state_id": state_id,
        "state_name": "",
        "district_id": None,
        "district_name": "",
        "mandal_id": mandal_id,
        "mandal_name": "",
        "town_village_id": town_village_id,
        "town_village_name": "",
        "area_id": None,
        "area_name": "",
        "pincode": "",
        "latitude": None,
        "longitude": None,
        "coord_source": "none",
    }


def get_location_stats(actor: dict | None = None) -> dict[str, Any]:
    with closing(_db.get_connection()) as conn:
        counts = {
            "countries": conn.execute("SELECT COUNT(*) FROM countries").fetchone()[0],
            "states": conn.execute("SELECT COUNT(*) FROM states").fetchone()[0],
            "districts": conn.execute("SELECT COUNT(*) FROM districts").fetchone()[0],
            "mandals": conn.execute("SELECT COUNT(*) FROM mandals").fetchone()[0],
            "towns_villages": conn.execute("SELECT COUNT(*) FROM towns_villages").fetchone()[0],
            "areas": conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0],
            "pincodes": conn.execute("SELECT COUNT(*) FROM pincodes").fetchone()[0],
        }
        logs = conn.execute(
            "SELECT * FROM location_load_logs ORDER BY loaded_at DESC, id DESC LIMIT 15"
        ).fetchall()
    log_dicts = [dict(r) for r in logs]
    return {
        **counts,
        "counts": counts,
        "recent_loads": log_dicts,
        "load_logs": log_dicts,
    }


def reload_locations_admin(
    actor: dict | None,
    csv_path: str | Path | None = None,
    lgd_csv_path: str | Path | None = None,
    filter_to_default_state: bool = True,
) -> dict[str, Any]:
    require(actor, "manage_locations")
    ip_res = load_locations_from_csv(
        csv_path=csv_path,
        filter_to_default_state=filter_to_default_state,
    )
    lgd_src = Path(lgd_csv_path) if lgd_csv_path else DEFAULT_LGD_CSV
    lgd_res = None
    if lgd_src.exists():
        lgd_res = load_lgd_villages_csv(
            csv_path=lgd_src,
            filter_to_default_state=filter_to_default_state,
        )
    return {
        **ip_res,
        "india_post": ip_res,
        "lgd": lgd_res,
        "loaded_mandals": (lgd_res["loaded_mandals"] if lgd_res else ip_res["loaded_mandals"]),
        "loaded_towns_villages": (lgd_res["loaded_towns_villages"] if lgd_res else 0),
    }


def set_district_self_claim(
    actor: dict | None,
    district_id: int,
    allow_self_claim: bool,
) -> dict:
    """
    Enable or disable employee self-claim (claim_complaint) for a district (default False).
    Super Admin can update any district; Admin only their assigned district(s).
    """
    require(actor, "assign_task")
    flag = 1 if allow_self_claim else 0
    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT id, state_id, name, is_active, COALESCE(allow_self_claim, 0) AS allow_self_claim "
            "FROM districts WHERE id = ?",
            (int(district_id),),
        ).fetchone()
        if row is None:
            raise ValueError(f"District with id {district_id} not found.")
        if actor and actor.get("role") == "admin":
            j_rows = conn.execute(
                "SELECT district_id FROM admin_jurisdictions WHERE user_id = ?",
                (actor["id"],),
            ).fetchall()
            allowed_dids = {int(r["district_id"]) for r in j_rows}
            if int(district_id) not in allowed_dids:
                raise PermissionError("You do not have jurisdiction over this district.")

        old_flag = int(row["allow_self_claim"] or 0)
        conn.execute(
            "UPDATE districts SET allow_self_claim = ? WHERE id = ?",
            (flag, int(district_id)),
        )
        conn.execute(
            """
            INSERT INTO location_load_logs
                (source_path, source_dataset, default_state, total_rows, loaded_areas, loaded_pincodes,
                 skipped_rows, mandal_level_present, notes, loaded_at)
            VALUES ('admin_config', 'district_self_claim', ?, 0, 0, 0, 0, 0, ?, ?)
            """,
            (
                get_default_state_name(),
                f"Actor {actor.get('username', '') if actor else 'system'} changed self_claim for district "
                f"{row['name']} ({district_id}): {bool(old_flag)} -> {bool(flag)}",
                _db.utc_now_str(),
            ),
        )
        updated = conn.execute("SELECT * FROM districts WHERE id = ?", (int(district_id),)).fetchone()
    return dict(updated)
