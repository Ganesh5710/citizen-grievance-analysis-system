"""
scripts/load_locations.py
Load official India Post pincode directory CSV (data.gov.in) AND optional
Local Government Directory (LGD) AP village/mandal CSV placed in data/ into:
countries, states, districts, mandals, towns_villages, areas, and pincodes tables.
Idempotent; normalizes names & AP 2022 district variants; logs skipped rows and source dates.

Usage:
    python scripts/load_locations.py
    python scripts/load_locations.py --csv data/india_post_pincodes_ap.csv --lgd data/lgd_ap_villages.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.locations import (  # noqa: E402
    DEFAULT_LGD_CSV,
    DEFAULT_LOCATION_CSV,
    get_default_country_name,
    get_default_state_name,
    load_lgd_villages_csv,
    load_locations_from_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed official India Post + optional LGD location hierarchy from CSVs."
    )
    parser.add_argument("--csv", default=str(DEFAULT_LOCATION_CSV), help="Path to India Post pincode CSV")
    parser.add_argument("--lgd", default=str(DEFAULT_LGD_CSV), help="Optional path to LGD district/mandal/village CSV")
    parser.add_argument("--country", default=get_default_country_name(), help="Default country (default: India)")
    parser.add_argument("--state", default=get_default_state_name(), help="Default state filter (default: AP)")
    parser.add_argument("--all-states", action="store_true", help="Load all states in CSVs instead of filtering.")
    args = parser.parse_args()

    ip_rep = load_locations_from_csv(
        csv_path=args.csv,
        default_country=args.country,
        default_state=args.state,
        filter_to_default_state=not args.all_states,
    )
    print(f"India Post Load Complete ({ip_rep['source_path']} | Date: {ip_rep['source_download_date']})")
    print(f"  Default Country/State : {ip_rep['default_country']} / {ip_rep['default_state']}")
    print(f"  Total / Valid rows    : {ip_rep['total_rows']} / {ip_rep['processed_valid_rows']}")
    print(f"  New states/districts  : {ip_rep['loaded_states']} / {ip_rep['loaded_districts']}")
    print(f"  New areas / pincodes  : {ip_rep['loaded_areas']} / {ip_rep['loaded_pincodes']}")
    print(f"  Skipped rows          : {ip_rep['skipped_rows']} {ip_rep['skipped_reasons']}")

    lgd_path = Path(args.lgd)
    if lgd_path.exists():
        lgd_rep = load_lgd_villages_csv(
            csv_path=lgd_path,
            default_country=args.country,
            default_state=args.state,
            filter_to_default_state=not args.all_states,
        )
        print(f"\nLGD Hierarchy Load Complete ({lgd_rep['source_path']} | Date: {lgd_rep['source_download_date']})")
        print(f"  Total / Valid rows    : {lgd_rep['total_rows']} / {lgd_rep['processed_valid_rows']}")
        print(f"  New mandals/villages  : {lgd_rep['loaded_mandals']} / {lgd_rep['loaded_towns_villages']}")
        print(f"  Linked India Post areas: {lgd_rep['linked_areas']}")
        print(f"  Skipped rows          : {lgd_rep['skipped_rows']} {lgd_rep['skipped_reasons']}")
    else:
        print(f"\nLGD CSV not found at {lgd_path}; skipped town/village level (Area remains lowest dropdown).")


if __name__ == "__main__":
    main()
