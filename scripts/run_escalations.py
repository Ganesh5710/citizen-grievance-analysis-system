"""
Standalone CLI runner for SLA breach escalations and 7-day auto-close.
Suitable for cron (Linux/macOS) or Windows Task Scheduler.

Usage:
    python scripts/run_escalations.py
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.database as _db  # noqa: F401 — ensure schema initialized
from core.sla import run_escalation_check


def main() -> int:
    summary = run_escalation_check()
    print(
        f"Escalation check complete: "
        f"level_1={summary['escalated_to_1']}, "
        f"level_2={summary['escalated_to_2']}, "
        f"auto_closed={summary['auto_closed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
