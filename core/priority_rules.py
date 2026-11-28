"""
core/priority_rules.py
Configurable YAML-backed priority rules, duration-to-priority pure mapping,
final hybrid priority combination, priority aging, Super Admin updates with audit
logging, and continuous-improvement feedback & failed-extraction logging.
"""

from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
from typing import Any

import yaml

from core.config import PROJECT_ROOT
from core.database import get_connection, utc_now_str
from core.logging_setup import get_logger
from core.permissions import require

logger = get_logger("core.priority_rules")

PRIORITY_RANK: dict[str, int] = {"Low": 1, "Medium": 2, "High": 3}
RANK_TO_PRIORITY: dict[int, str] = {1: "Low", 2: "Medium", 3: "High"}

DEFAULT_RULES_DICT: dict[str, Any] = {
    "default": {"medium_min_hours": 24.0, "high_min_hours": 168.0},
    "department_overrides": {
        "Water": {"medium_min_hours": 24.0, "high_min_hours": 120.0},
        "Electricity": {"medium_min_hours": 24.0, "high_min_hours": 120.0},
    },
    "aging": {
        "enabled": True,
        "low_to_medium_days": 3.0,
        "medium_to_high_days": 7.0,
    },
    "review_confidence_threshold": 0.55,
}


def get_priority_rules_path() -> Path:
    override = os.environ.get("CGS_PRIORITY_RULES_PATH", "").strip()
    if override:
        return Path(override)
    return PROJECT_ROOT / "config" / "priority_rules.yaml"


def load_priority_rules(path: Path | str | None = None) -> dict[str, Any]:
    """Load config/priority_rules.yaml dynamically on every call so edits apply immediately."""
    target = Path(path) if path else get_priority_rules_path()
    if not target.exists():
        return json.loads(json.dumps(DEFAULT_RULES_DICT))
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Failed reading %s, using defaults: %s", target, exc)
        return json.loads(json.dumps(DEFAULT_RULES_DICT))

    cfg = json.loads(json.dumps(DEFAULT_RULES_DICT))
    if isinstance(raw.get("default"), dict):
        cfg["default"]["medium_min_hours"] = float(
            raw["default"].get("medium_min_hours", cfg["default"]["medium_min_hours"])
        )
        cfg["default"]["high_min_hours"] = float(
            raw["default"].get("high_min_hours", cfg["default"]["high_min_hours"])
        )
    if isinstance(raw.get("department_overrides"), dict):
        overrides = {}
        for dept_name, vals in raw["department_overrides"].items():
            if isinstance(vals, dict):
                overrides[str(dept_name)] = {
                    "medium_min_hours": float(vals.get("medium_min_hours", cfg["default"]["medium_min_hours"])),
                    "high_min_hours": float(vals.get("high_min_hours", cfg["default"]["high_min_hours"])),
                }
        cfg["department_overrides"] = overrides
    if isinstance(raw.get("aging"), dict):
        cfg["aging"]["enabled"] = bool(raw["aging"].get("enabled", True))
        cfg["aging"]["low_to_medium_days"] = float(
            raw["aging"].get("low_to_medium_days", cfg["aging"]["low_to_medium_days"])
        )
        cfg["aging"]["medium_to_high_days"] = float(
            raw["aging"].get("medium_to_high_days", cfg["aging"]["medium_to_high_days"])
        )
    if "review_confidence_threshold" in raw:
        cfg["review_confidence_threshold"] = float(raw["review_confidence_threshold"])
    return cfg


def map_duration_to_priority(
    hours: float | None,
    department: str | None = None,
    rules: dict[str, Any] | None = None,
) -> str | None:
    """
    Pure duration-to-priority mapping:
      hours is None -> None
      hours >= high_min_hours -> 'High'
      hours >= medium_min_hours -> 'Medium'
      else -> 'Low'
    Honors per-department overrides from config/priority_rules.yaml.
    """
    if hours is None:
        return None
    cfg = rules if rules is not None else load_priority_rules()
    thresh = cfg["default"]
    if department:
        for dept_key, vals in cfg.get("department_overrides", {}).items():
            if dept_key.strip().lower() == department.strip().lower():
                thresh = vals
                break

    h = float(hours)
    if h >= float(thresh["high_min_hours"]):
        return "High"
    if h >= float(thresh["medium_min_hours"]):
        return "Medium"
    return "Low"


def max_priority(*levels: str | None) -> str:
    """Return the highest priority level among inputs (defaults to 'Low')."""
    best_rank = 1
    for lvl in levels:
        if lvl in PRIORITY_RANK and PRIORITY_RANK[lvl] > best_rank:
            best_rank = PRIORITY_RANK[lvl]
    return RANK_TO_PRIORITY[best_rank]


def bump_priority(level: str, steps: int = 1) -> str:
    rank = PRIORITY_RANK.get(level, 1)
    return RANK_TO_PRIORITY[min(3, max(1, rank + int(steps)))]


def compute_final_priority(
    *,
    department: str | None,
    emergency_override: bool,
    emergency_keyword: str | None = None,
    duration_hours: float | None,
    duration_matched_text: str = "",
    duration_confidence: float | None = None,
    ml_priority: str,
    keyword_priority: str,
    is_recurring: bool = False,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Combine all signals according to the documented precedence:
      1. Emergency keyword override -> always High
      2. Duration-based priority from config/priority_rules.yaml
      3. ML model priority
      4. Keyword priority
      5. Recurring flag (+1 level)
    Never allows any model to lower a priority set by rules 1 or 2.
    """
    cfg = rules if rules is not None else load_priority_rules()
    dur_priority = map_duration_to_priority(duration_hours, department=department, rules=cfg)

    candidates: dict[str, int] = {}
    if emergency_override:
        candidates["emergency"] = 3
    if dur_priority is not None:
        candidates["duration"] = PRIORITY_RANK[dur_priority]
    if ml_priority in PRIORITY_RANK:
        candidates["ml"] = PRIORITY_RANK[ml_priority]
    if keyword_priority in PRIORITY_RANK:
        candidates["keyword"] = PRIORITY_RANK[keyword_priority]

    base_rank = max(candidates.values()) if candidates else 1
    base_priority = RANK_TO_PRIORITY[base_rank]

    final_rank = base_rank
    recurring_bumped = False
    if is_recurring and final_rank < 3:
        final_rank = min(3, final_rank + 1)
        recurring_bumped = True
    final_priority = RANK_TO_PRIORITY[final_rank]

    # Construct clear staff-facing explanation
    reasons: list[str] = []
    if emergency_override:
        kw_note = f" ('{emergency_keyword}')" if emergency_keyword else ""
        reasons.append(f"emergency keyword{kw_note} override")
    if dur_priority is not None and duration_hours is not None:
        label_txt = duration_matched_text or f"{duration_hours:.1f}h"
        reasons.append(f"duration {label_txt} -> {dur_priority} (rule)")
    if ml_priority:
        reasons.append(f"ML said {ml_priority}")
    if keyword_priority and keyword_priority != "Low":
        reasons.append(f"keyword {keyword_priority}")
    if recurring_bumped:
        reasons.append(f"recurring pattern (+1 from {base_priority})")
    elif is_recurring:
        reasons.append("recurring pattern")

    reason_str = f"{final_priority}: " + (", ".join(reasons) if reasons else "default rule") + "."
    low_dur_conf = (
        duration_hours is not None
        and duration_confidence is not None
        and float(duration_confidence) < float(cfg.get("review_confidence_threshold", 0.55))
    )

    return {
        "final_priority": final_priority,
        "duration_priority": dur_priority,
        "base_priority": base_priority,
        "recurring_bumped": recurring_bumped,
        "low_duration_confidence": low_dur_conf,
        "reason": reason_str,
    }


def save_priority_rules(
    actor: dict | None,
    new_rules: dict[str, Any],
    note: str = "",
) -> dict[str, Any]:
    """Validate and persist updated priority rules to YAML and write an audit log row."""
    require(actor, "manage_priority_rules")

    default_block = new_rules.get("default", {})
    med_h = float(default_block.get("medium_min_hours", 24.0))
    high_h = float(default_block.get("high_min_hours", 168.0))
    if med_h <= 0 or high_h <= med_h:
        raise ValueError("Default high_min_hours must be strictly greater than medium_min_hours (> 0).")

    cleaned_overrides: dict[str, dict[str, float]] = {}
    for dname, dvals in (new_rules.get("department_overrides") or {}).items():
        name_clean = str(dname).strip()
        if not name_clean:
            continue
        dm = float(dvals.get("medium_min_hours", med_h))
        dh = float(dvals.get("high_min_hours", high_h))
        if dm <= 0 or dh <= dm:
            raise ValueError(f"Override for {name_clean}: high_min_hours must exceed medium_min_hours (> 0).")
        cleaned_overrides[name_clean] = {"medium_min_hours": dm, "high_min_hours": dh}

    aging_raw = new_rules.get("aging", {})
    low_med_days = float(aging_raw.get("low_to_medium_days", 3.0))
    med_high_days = float(aging_raw.get("medium_to_high_days", 7.0))
    if low_med_days <= 0 or med_high_days <= low_med_days:
        raise ValueError("Aging medium_to_high_days must be greater than low_to_medium_days (> 0).")

    rev_thresh = float(new_rules.get("review_confidence_threshold", 0.55))
    if not (0.0 < rev_thresh < 1.0):
        raise ValueError("review_confidence_threshold must be between 0 and 1.")

    validated = {
        "default": {"medium_min_hours": med_h, "high_min_hours": high_h},
        "department_overrides": cleaned_overrides,
        "aging": {
            "enabled": bool(aging_raw.get("enabled", True)),
            "low_to_medium_days": low_med_days,
            "medium_to_high_days": med_high_days,
        },
        "review_confidence_threshold": rev_thresh,
    }

    target = get_priority_rules_path()
    old_rules = load_priority_rules(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(validated, sort_keys=False), encoding="utf-8")

    now = utc_now_str()
    with closing(get_connection()) as conn, conn:
        conn.execute(
            """
            INSERT INTO priority_rule_audit
                (actor_id, actor_username, old_config_json, new_config_json, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                actor["id"],
                actor["username"],
                json.dumps(old_rules),
                json.dumps(validated),
                note.strip() or "Priority rules updated",
                now,
            ),
        )
    return validated


def get_priority_rule_audit_logs(actor: dict | None, limit: int = 50) -> list[dict]:
    require(actor, "manage_priority_rules")
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM priority_rule_audit ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def log_duration_extraction_event(
    *,
    complaint_id: int | None,
    incident_id: str,
    text: str,
    found_hours: float | None,
    matched_text: str,
    confidence: float | None,
    status: str,
    reason: str,
) -> None:
    """Record failed or low-confidence duration extractions for rule improvement."""
    now = utc_now_str()
    with closing(get_connection()) as conn, conn:
        conn.execute(
            """
            INSERT INTO duration_extraction_logs
                (complaint_id, incident_id, text, found_hours, matched_text,
                 confidence, status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                complaint_id,
                incident_id,
                text,
                found_hours,
                matched_text,
                confidence,
                status,
                reason,
                now,
            ),
        )


def log_priority_feedback(
    *,
    complaint_id: int,
    incident_id: str,
    text: str,
    duration_hours: float | None,
    matched_text: str,
    corrected_priority: str,
    corrected_by: int,
) -> None:
    """Save admin-corrected priority examples for future retraining."""
    now = utc_now_str()
    with closing(get_connection()) as conn, conn:
        conn.execute(
            """
            INSERT INTO priority_feedback_log
                (complaint_id, incident_id, text, duration_hours, matched_text,
                 corrected_priority, corrected_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                complaint_id,
                incident_id,
                text,
                duration_hours,
                matched_text,
                corrected_priority,
                corrected_by,
                now,
            ),
        )


def get_duration_extraction_logs(actor: dict | None, limit: int = 100) -> list[dict]:
    require(actor, "view_review_queue")
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM duration_extraction_logs ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_priority_feedback_logs(actor: dict | None, limit: int = 100) -> list[dict]:
    require(actor, "view_review_queue")
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT p.*, COALESCE(u.username, '') AS corrected_by_username
            FROM priority_feedback_log p
            LEFT JOIN users u ON u.id = p.corrected_by
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]
