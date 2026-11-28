"""
System health-check helper for uptime monitoring, Docker HEALTHCHECK, and ?health=1 UI route.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT
import core.database as _db


def get_health_status() -> dict[str, Any]:
    """Verify database connectivity, active ML model presence, and upload directory writability."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    checks: dict[str, Any] = {}
    overall_ok = True

    # 1. Database connectivity + table check
    try:
        with closing(_db.get_connection()) as conn:
            conn.execute("SELECT 1").fetchone()
            c_cnt = conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
            l_cnt = conn.execute("SELECT COUNT(*) FROM pincodes").fetchone()[0]
        checks["database"] = {
            "status": "ok",
            "backend": _db.get_database_backend(),
            "complaints": int(c_cnt),
            "pincodes": int(l_cnt),
        }
    except Exception as exc:
        overall_ok = False
        checks["database"] = {"status": "error", "detail": str(exc)}

    # 2. ML model artifact availability
    active_meta = PROJECT_ROOT / "ml" / "models" / "active.json"
    if active_meta.exists():
        checks["ml_model"] = {"status": "ok", "mode": "trained_active"}
    else:
        checks["ml_model"] = {"status": "degraded_fallback", "mode": "rule_based_fallback"}

    # 3. Upload directory writable
    up_dir = Path(os.environ.get("CGS_UPLOADS_DIR", str(PROJECT_ROOT / "uploads")))
    try:
        up_dir.mkdir(parents=True, exist_ok=True)
        checks["uploads_dir"] = {"status": "ok", "writable": os.access(up_dir, os.W_OK)}
    except Exception as exc:
        overall_ok = False
        checks["uploads_dir"] = {"status": "error", "detail": str(exc)}

    return {
        "status": "ok" if overall_ok else "error",
        "timestamp_utc": now_str,
        "checks": checks,
    }
