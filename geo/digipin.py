"""
geo/digipin.py
Official India Post DIGIPIN (Digital Postal Index Number) encoder, decoder, and validator.
Derived strictly from the published open-source CEPT / India Post specification
(`INDIAPOST-gov/digipin/src/digipin.js`):
  - Bounding Box: Latitude [2.5, 38.5], Longitude [63.5, 99.5]
  - 4x4 hierarchical grid across 10 levels (~3.8m x 3.8m finest cell)
  - Allowed character set (16 symbols): 2, 3, 4, 5, 6, 7, 8, 9, C, F, J, K, L, M, P, T
"""

from __future__ import annotations

import math
import re
from typing import Any

from core.config import get_env_float

DIGIPIN_GRID: tuple[tuple[str, ...], ...] = (
    ("F", "C", "9", "8"),
    ("J", "3", "2", "7"),
    ("K", "4", "5", "6"),
    ("L", "M", "P", "T"),
)

MIN_LAT = 2.5
MAX_LAT = 38.5
MIN_LON = 63.5
MAX_LON = 99.5

ALLOWED_CHARS = frozenset("23456789CFJKLMPT")
_PLAIN_RE = re.compile(r"^[23456789CFJKLMPT]{10}$")
_HYPHEN_RE = re.compile(r"^[23456789CFJKLMPT]{3}-[23456789CFJKLMPT]{3}-[23456789CFJKLMPT]{4}$")


def format_digipin(plain: str) -> str:
    """Format a validated 10-character DIGIPIN as XXX-XXX-XXXX."""
    return f"{plain[:3]}-{plain[3:6]}-{plain[6:]}"


def normalize_digipin(digipin: str | None, *, hyphenated: bool = False) -> str:
    """
    Validate and normalize an India Post DIGIPIN code.
    Accepts either plain 10-char form ('39J49LL8T4') or standard hyphenated form ('39J-49L-L8T4'),
    case-insensitive, and rejects wrong lengths, invalid symbols, or malformed punctuation.
    """
    if not isinstance(digipin, str):
        raise ValueError("DIGIPIN must be a string.")
    raw = digipin.strip().upper()
    if not raw:
        raise ValueError("DIGIPIN cannot be empty.")

    if "-" in raw:
        if not _HYPHEN_RE.match(raw):
            raise ValueError(
                "Invalid DIGIPIN format. Use 10 approved characters (e.g. 39J49LL8T4 or 39J-49L-L8T4)."
            )
        plain = raw.replace("-", "")
    else:
        if not _PLAIN_RE.match(raw):
            raise ValueError(
                "Invalid DIGIPIN. Must be 10 characters from [2-9, C, F, J, K, L, M, P, T]."
            )
        plain = raw

    return format_digipin(plain) if hyphenated else plain


def encode_digipin(latitude: float, longitude: float, *, hyphenated: bool = False) -> str:
    """
    Encode (latitude, longitude) into a 10-character India Post DIGIPIN.
    Raises ValueError if coordinates lie outside the India DIGIPIN bounding box.
    """
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("Latitude and longitude must be valid numbers.") from exc

    if not (MIN_LAT <= lat <= MAX_LAT):
        raise ValueError(f"Latitude {lat} is out of DIGIPIN supported range [{MIN_LAT}, {MAX_LAT}].")
    if not (MIN_LON <= lon <= MAX_LON):
        raise ValueError(f"Longitude {lon} is out of DIGIPIN supported range [{MIN_LON}, {MAX_LON}].")

    min_lat, max_lat = MIN_LAT, MAX_LAT
    min_lon, max_lon = MIN_LON, MAX_LON
    chars: list[str] = []

    for _ in range(10):
        lat_div = (max_lat - min_lat) / 4.0
        lon_div = (max_lon - min_lon) / 4.0

        row = 3 - int(math.floor((lat - min_lat) / lat_div))
        col = int(math.floor((lon - min_lon) / lon_div))

        row = max(0, min(row, 3))
        col = max(0, min(col, 3))

        chars.append(DIGIPIN_GRID[row][col])

        max_lat = min_lat + lat_div * (4 - row)
        min_lat = min_lat + lat_div * (3 - row)
        min_lon = min_lon + lon_div * col
        max_lon = min_lon + lon_div

    plain = "".join(chars)
    return format_digipin(plain) if hyphenated else plain


def decode_digipin(digipin: str) -> dict[str, Any]:
    """
    Decode a valid 10-character DIGIPIN (plain or XXX-XXX-XXXX) into its central
    (latitude, longitude) and cell bounds. Validates India bounding box containment.
    """
    plain = normalize_digipin(digipin, hyphenated=False)

    min_lat, max_lat = MIN_LAT, MAX_LAT
    min_lon, max_lon = MIN_LON, MAX_LON

    pos_map = {
        DIGIPIN_GRID[r][c]: (r, c)
        for r in range(4)
        for c in range(4)
    }

    for ch in plain:
        ri, ci = pos_map[ch]
        lat_div = (max_lat - min_lat) / 4.0
        lon_div = (max_lon - min_lon) / 4.0

        next_min_lat = max_lat - lat_div * (ri + 1)
        next_max_lat = max_lat - lat_div * ri
        next_min_lon = min_lon + lon_div * ci
        next_max_lon = min_lon + lon_div * (ci + 1)

        min_lat, max_lat = next_min_lat, next_max_lat
        min_lon, max_lon = next_min_lon, next_max_lon

    center_lat = (min_lat + max_lat) / 2.0
    center_lon = (min_lon + max_lon) / 2.0

    if not (MIN_LAT <= center_lat <= MAX_LAT and MIN_LON <= center_lon <= MAX_LON):
        raise ValueError("Decoded DIGIPIN falls outside supported India bounding box.")

    return {
        "digipin": plain,
        "digipin_formatted": format_digipin(plain),
        "latitude": round(center_lat, 6),
        "longitude": round(center_lon, 6),
        "bounds": {
            "min_lat": round(min_lat, 8),
            "max_lat": round(max_lat, 8),
            "min_lon": round(min_lon, 8),
            "max_lon": round(max_lon, 8),
        },
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_km = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * r_km * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def are_digipin_neighbors(pin_a: str | None, pin_b: str | None, *, max_meters: float = 18.0) -> bool:
    """
    Return True if two DIGIPIN codes represent the same ~3.8m cell, share the same
    ~15m level-9 parent cell, or lie adjacent within `max_meters` (default 18m across borders).
    """
    if not pin_a or not pin_b:
        return False
    try:
        a = normalize_digipin(pin_a, hyphenated=False)
        b = normalize_digipin(pin_b, hyphenated=False)
    except ValueError:
        return False
    if a == b or a[:9] == b[:9]:
        return True
    da = decode_digipin(a)
    db = decode_digipin(b)
    dist_m = _haversine_km(da["latitude"], da["longitude"], db["latitude"], db["longitude"]) * 1000.0
    return dist_m <= float(max_meters)


def check_digipin_against_location(
    digipin: str | None,
    ref_lat: float | None,
    ref_lon: float | None,
    *,
    max_distance_km: float | None = None,
) -> dict[str, Any]:
    """
    Non-blocking consistency check for complaint submission:
    - If `digipin` is blank -> not provided, not flagged.
    - If `digipin` is invalid -> flagged=True with format warning (caller may still save).
    - If decoded point lies > `max_distance_km` (CGS_DIGIPIN_MISMATCH_KM, default 35 km) from
      selected area/district centroid -> flagged=True with gentle user warning.
    """
    raw = str(digipin or "").strip()
    if not raw:
        return {
            "provided": False,
            "valid": False,
            "normalized": "",
            "latitude": None,
            "longitude": None,
            "flagged": False,
            "distance_km": None,
            "warning": "",
        }

    try:
        dec = decode_digipin(raw)
    except ValueError as exc:
        return {
            "provided": True,
            "valid": False,
            "normalized": raw.upper()[:20],
            "latitude": None,
            "longitude": None,
            "flagged": True,
            "distance_km": None,
            "warning": f"DIGIPIN could not be verified ({exc}); saved for staff review.",
        }

    limit_km = float(
        max_distance_km if max_distance_km is not None else get_env_float("CGS_DIGIPIN_MISMATCH_KM", 35.0)
    )
    flagged = False
    dist_km: float | None = None
    warning = ""
    if ref_lat is not None and ref_lon is not None:
        dist_km = round(_haversine_km(dec["latitude"], dec["longitude"], float(ref_lat), float(ref_lon)), 2)
        if dist_km > limit_km:
            flagged = True
            warning = "This DIGIPIN seems to be outside the selected area. Please check."

    return {
        "provided": True,
        "valid": True,
        "normalized": dec["digipin"],
        "formatted": dec["digipin_formatted"],
        "latitude": dec["latitude"],
        "longitude": dec["longitude"],
        "flagged": flagged,
        "distance_km": dist_km,
        "warning": warning,
    }
