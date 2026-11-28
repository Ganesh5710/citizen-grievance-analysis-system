"""
scripts/load_locations.py
Load official India Post pincode directory CSV (data.gov.in) placed in data/
into states, districts, optional mandals, areas, and pincodes tables.
Defaults to DEFAULT_STATE ('Andhra Pradesh') from .env / core.config.

Usage:
    python scripts/load_locations.py
    python scripts/load_locations.py --csv data/india_post_pincodes_ap.csv --all-states
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.locations import (  # noqa: E402
    DEFAULT_LOCATION_CSV,
    get_default_state_name,
    load_locations_from_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed official India Post location hierarchy from CSV.")
    parser.add_argument("--csv", default=str(DEFAULT_LOCATION_CSV), help="Path to India Post pincode CSV in data/")
    parser.add_argument("--state", default=get_default_state_name(), help="Default state filter (default: AP)")
    parser.add_argument("--all-states", action="store_true", help="Load all states in the CSV instead of filtering.")
    args = parser.parse_args()

    report = load_locations_from_csv(
        csv_path=args.csv,
        default_state=args.state,
        filter_to_default_state=not args.all_states,
    )
    print(f"Location Load Complete ({report['source_path']})")
    print(f"  Default State         : {report['default_state']}")
    print(f"  Total CSV rows        : {report['total_rows']}")
    print(f"  Valid rows processed  : {report['processed_valid_rows']}")
    print(f"  New states / districts: {report['loaded_states']} / {report['loaded_districts']}")
    print(f"  Mandal level present  : {report['mandal_level_present']} ({report['loaded_mandals']} new mandals)")
    print(f"  New areas / pincodes  : {report['loaded_areas']} / {report['loaded_pincodes']}")
    print(f"  Skipped rows          : {report['skipped_rows']} {report['skipped_reasons']}")
    print(f"  Notes                 : {report['notes']}")


if __name__ == "__main__":
    main()
