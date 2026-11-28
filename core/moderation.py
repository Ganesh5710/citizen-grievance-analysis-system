"""
core/moderation.py
Phase 4: Abuse control, multilingual profanity/abuse detection (EN/HI/TE via YAML),
gibberish & duplicate-spam detection, per-user daily submission limit & cooldown,
optional arithmetic CAPTCHA challenge, temporary spam block & Super Admin lift.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import re
from typing import Any

import yaml

from core.config import PROJECT_ROOT, get_env_bool, get_env_int, get_env_str
import core.database as _db
from core.permissions import require
from core.security import sanitize_text

_DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "moderation_rules.yaml"
_LATIN_VOWELS = set("aeiouy")
_UNICODE_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_NON_LATIN_INDIC_RE = re.compile(r"[\u0900-\u097F\u0C00-\u0C7F]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+")


def _utc_now(now: datetime | None = None) -> datetime:
    dt = now or datetime.now(timezone.utc)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_moderation_rules_path() -> Path:
    return Path(get_env_str("CGS_MODERATION_RULES_PATH", str(_DEFAULT_RULES_PATH)))


def load_moderation_rules() -> dict[str, Any]:
    path = get_moderation_rules_path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    return {
        "abuse_words": data.get("abuse_words") or {"en": [], "hi": [], "te": []},
        "gibberish": data.get("gibberish") or {
            "max_repeat_char_run": 5,
            "min_latin_vowel_ratio": 0.14,
            "min_unique_token_ratio": 0.30,
            "min_alpha_char_ratio": 0.35,
        },
        "spam_thresholds": data.get("spam_thresholds") or {
            "daily_limit": 10,
            "cooldown_seconds": 5,
            "confirmed_spam_block_threshold": 3,
            "block_duration_days": 7,
        },
    }


def detect_abuse_words(text: str, rules: dict[str, Any] | None = None) -> list[str]:
    cfg = rules or load_moderation_rules()
    abuse_map = cfg.get("abuse_words") or {}
    lower_txt = str(text or "").lower()
    matched: list[str] = []
    for lang, words in abuse_map.items():
        for w in words or []:
            w_clean = str(w).strip().lower()
            if not w_clean:
                continue
            if w_clean.isascii():
                if re.search(rf"\b{re.escape(w_clean)}\b", lower_txt):
                    matched.append(f"{lang}:{w_clean}")
            else:
                if w_clean in lower_txt:
                    matched.append(f"{lang}:{w_clean}")
    return matched


def detect_gibberish(text: str, rules: dict[str, Any] | None = None) -> str | None:
    """
    Detect gibberish (repeated char runs, excessive consonant noise in Latin text,
    low alphabetic density, or heavy token repetition) without false-positiving on Telugu/Hindi script.
    """
    cfg = rules or load_moderation_rules()
    gib = cfg.get("gibberish") or {}
    max_run = int(gib.get("max_repeat_char_run", 5))
    min_vowel_ratio = float(gib.get("min_latin_vowel_ratio", 0.14))
    min_unique_tok = float(gib.get("min_unique_token_ratio", 0.30))
    min_alpha_ratio = float(gib.get("min_alpha_char_ratio", 0.35))

    s = str(text or "").strip()
    if not s:
        return "Empty complaint text"

    if re.search(rf"(.)\1{{{max_run},}}", s, re.IGNORECASE):
        return "Repeated character spam"

    non_space = re.sub(r"\s+", "", s)
    if non_space:
        alpha_chars = _UNICODE_LETTER_RE.findall(non_space)
        alpha_ratio = len(alpha_chars) / len(non_space)
        if alpha_ratio < min_alpha_ratio:
            return "Low alphabetic/meaningful character ratio"

    tokens = re.findall(r"\S+", s.lower())
    if len(tokens) >= 5:
        uniq_ratio = len(set(tokens)) / len(tokens)
        if uniq_ratio < min_unique_tok:
            return "Excessive repeated words"

    # Latin-script vowel check ONLY when complaint is not predominantly Telugu/Hindi script
    has_indic = bool(_NON_LATIN_INDIC_RE.search(s))
    latin_letters = "".join(_LATIN_WORD_RE.findall(s.lower()))
    if not has_indic and len(latin_letters) >= 10:
        vowels = sum(1 for ch in latin_letters if ch in _LATIN_VOWELS)
        if (vowels / len(latin_letters)) < min_vowel_ratio:
            return "Unreadable Latin consonant gibberish"

        # Check high proportion of vowelless long Latin tokens
        latin_words = [w for w in _LATIN_WORD_RE.findall(s.lower()) if len(w) >= 4]
        if latin_words:
            no_vowel_cnt = sum(1 for w in latin_words if not any(c in _LATIN_VOWELS for c in w))
            if (no_vowel_cnt / len(latin_words)) >= 0.65:
                return "Low dictionary/vowel structure across words"

    return None


def _norm_for_dup(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def assess_submission_safety(
    conn,
    actor: dict,
    clean_text: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Enforce user spam block, daily limit, cooldown, and inspect text for gibberish,
    identical repeats by the same user, and abusive language.
    Raises PermissionError if user is spam-blocked, ValueError if rate/cooldown exceeded.
    Returns {'is_flagged': bool, 'reasons': list[str], 'queue_reason': str}.
    """
    now_dt = _utc_now(now)
    now_str = _fmt_dt(now_dt)
    uid = int(actor["id"])
    rules = load_moderation_rules()
    thresh = rules.get("spam_thresholds") or {}

    u_row = conn.execute(
        "SELECT spam_blocked_until, confirmed_spam_count FROM users WHERE id = ?",
        (uid,),
    ).fetchone()
    if u_row and u_row["spam_blocked_until"]:
        if str(u_row["spam_blocked_until"]) > now_str:
            raise PermissionError(
                f"Your account is temporarily blocked from submitting until {u_row['spam_blocked_until']} UTC "
                "due to repeated confirmed spam. Contact a Super Admin."
            )

    daily_limit = get_env_int("CGS_DAILY_SUBMISSION_LIMIT", int(thresh.get("daily_limit", 10)))
    if daily_limit > 0:
        day_start = now_dt.strftime("%Y-%m-%d 00:00:00")
        today_cnt = int(
            conn.execute(
                "SELECT COUNT(*) FROM complaints WHERE user_id = ? AND created_at >= ?",
                (uid, day_start),
            ).fetchone()[0]
        )
        if today_cnt >= daily_limit:
            raise ValueError(
                f"Daily submission limit reached ({daily_limit} complaints per day). Please try again tomorrow."
            )

    cooldown_sec = get_env_int("CGS_SUBMISSION_COOLDOWN_SECONDS", int(thresh.get("cooldown_seconds", 5)))
    if cooldown_sec > 0:
        last_row = conn.execute(
            "SELECT created_at FROM complaints WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (uid,),
        ).fetchone()
        if last_row and last_row["created_at"]:
            try:
                last_dt = datetime.strptime(last_row["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                elapsed = (now_dt - last_dt).total_seconds()
                if 0 <= elapsed < cooldown_sec:
                    wait_s = int(cooldown_sec - elapsed) + 1
                    raise ValueError(
                        f"Please wait {wait_s} second(s) before submitting another complaint."
                    )
            except ValueError as ve:
                if "Please wait" in str(ve):
                    raise

    reasons: list[str] = []
    gib_reason = detect_gibberish(clean_text, rules)
    if gib_reason:
        reasons.append(f"Gibberish ({gib_reason})")

    abuse_hits = detect_abuse_words(clean_text, rules)
    if abuse_hits:
        reasons.append("Abusive language detected")

    # Identical repeated submission check (same user)
    norm_new = _norm_for_dup(clean_text)
    prev_rows = conn.execute(
        "SELECT text FROM complaints WHERE user_id = ? AND is_deleted = 0 ORDER BY id DESC LIMIT 25",
        (uid,),
    ).fetchall()
    if any(_norm_for_dup(r["text"]) == norm_new for r in prev_rows):
        reasons.append("Identical repeated submission")

    queue_reason = f"Suspected spam: {'; '.join(reasons)}" if reasons else ""
    return {
        "is_flagged": bool(reasons),
        "reasons": reasons,
        "queue_reason": queue_reason,
    }


def resolve_spam_in_conn(
    conn,
    actor: dict,
    complaint_id: int,
    confirm_spam: bool,
    reason: str = "",
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Called by Admin / Super Admin when reviewing suspected spam:
      - confirm_spam=True: marks complaint is_spam=2, is_deleted=1, increments user's
        confirmed_spam_count, and auto-applies temporary spam block if threshold reached.
      - confirm_spam=False: clears is_spam=0 and moves Submitted -> Awaiting Assignment.
    """
    now_dt = _utc_now(now)
    now_str = _fmt_dt(now_dt)
    rules = load_moderation_rules()
    thresh = rules.get("spam_thresholds") or {}
    block_threshold = get_env_int(
        "CGS_SPAM_BLOCK_THRESHOLD", int(thresh.get("confirmed_spam_block_threshold", 3))
    )
    block_days = get_env_int("CGS_SPAM_BLOCK_DAYS", int(thresh.get("block_duration_days", 7)))

    comp = conn.execute(
        "SELECT id, incident_id, user_id, district_id, department_id, status FROM complaints WHERE id = ?",
        (int(complaint_id),),
    ).fetchone()
    if comp is None:
        raise ValueError(f"Complaint {complaint_id} not found.")

    reporter = conn.execute(
        "SELECT id, username, COALESCE(confirmed_spam_count, 0) AS spam_cnt FROM users WHERE id = ?",
        (comp["user_id"],),
    ).fetchone()
    rep_username = reporter["username"] if reporter else ""

    if confirm_spam:
        conn.execute(
            "UPDATE complaints SET is_spam = 2, is_deleted = 1, updated_at = ? WHERE id = ?",
            (now_str, comp["id"]),
        )
        new_cnt = int(reporter["spam_cnt"] if reporter else 0) + 1
        blocked_until = None
        if new_cnt >= block_threshold:
            blocked_until = _fmt_dt(now_dt + timedelta(days=block_days))
            conn.execute(
                "UPDATE users SET confirmed_spam_count = ?, spam_blocked_until = ? WHERE id = ?",
                (new_cnt, blocked_until, comp["user_id"]),
            )
            conn.execute(
                """
                INSERT INTO moderation_audit_log
                    (actor_id, actor_username, target_user_id, target_username, complaint_id, action, reason, created_at)
                VALUES (?, ?, ?, ?, ?, 'auto_spam_block', ?, ?)
                """,
                (
                    None,
                    "system",
                    comp["user_id"],
                    rep_username,
                    comp["id"],
                    f"Confirmed spam count reached {new_cnt} (>= {block_threshold}); blocked until {blocked_until} UTC",
                    now_str,
                ),
            )
        else:
            conn.execute(
                "UPDATE users SET confirmed_spam_count = ? WHERE id = ?",
                (new_cnt, comp["user_id"]),
            )

        conn.execute(
            """
            INSERT INTO moderation_audit_log
                (actor_id, actor_username, target_user_id, target_username, complaint_id, action, reason, created_at)
            VALUES (?, ?, ?, ?, ?, 'confirm_spam', ?, ?)
            """,
            (
                actor["id"],
                actor.get("username", ""),
                comp["user_id"],
                rep_username,
                comp["id"],
                reason or "Confirmed suspected spam in review queue",
                now_str,
            ),
        )
        return {
            "action": "Confirmed Spam",
            "confirmed_spam_count": new_cnt,
            "spam_blocked_until": blocked_until,
        }

    # Release valid complaint into normal Awaiting Assignment workflow
    new_status = "Awaiting Assignment" if comp["status"] == "Submitted" else comp["status"]
    conn.execute(
        "UPDATE complaints SET is_spam = 0, status = ?, updated_at = ? WHERE id = ?",
        (new_status, now_str, comp["id"]),
    )
    if new_status != comp["status"]:
        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'status', ?, ?, ?, ?, 'review_queue', ?, ?)
            """,
            (
                comp["id"],
                actor["id"],
                actor.get("username", ""),
                comp["status"],
                new_status,
                comp["status"],
                new_status,
                reason or "Released from suspected spam review to Awaiting Assignment",
                now_str,
            ),
        )
    conn.execute(
        """
        INSERT INTO moderation_audit_log
            (actor_id, actor_username, target_user_id, target_username, complaint_id, action, reason, created_at)
        VALUES (?, ?, ?, ?, ?, 'release_spam', ?, ?)
        """,
        (
            actor["id"],
            actor.get("username", ""),
            comp["user_id"],
            rep_username,
            comp["id"],
            reason or "Marked valid and released",
            now_str,
        ),
    )
    return {"action": "Released", "status": new_status}


def lift_spam_block(actor: dict | None, target_user_id: int, reason: str) -> dict:
    """Super Admin lifts a temporary spam block on a user with mandatory reason and audit log."""
    require(actor, "manage_users")
    clean_reason = sanitize_text(reason or "", max_length=500, field_name="Unblock reason")
    if len(clean_reason) < 5:
        raise ValueError("Reason for lifting spam block is required (at least 5 characters).")

    now_str = _fmt_dt(_utc_now())
    with closing(_db.get_connection()) as conn, conn:
        u = conn.execute(
            "SELECT id, username, spam_blocked_until, confirmed_spam_count FROM users WHERE id = ?",
            (int(target_user_id),),
        ).fetchone()
        if u is None:
            raise ValueError(f"User {target_user_id} not found.")
        conn.execute(
            "UPDATE users SET spam_blocked_until = NULL, confirmed_spam_count = 0 WHERE id = ?",
            (int(target_user_id),),
        )
        conn.execute(
            """
            INSERT INTO moderation_audit_log
                (actor_id, actor_username, target_user_id, target_username, complaint_id, action, reason, created_at)
            VALUES (?, ?, ?, ?, NULL, 'lift_spam_block', ?, ?)
            """,
            (
                actor["id"],
                actor.get("username", ""),
                int(target_user_id),
                u["username"],
                clean_reason,
                now_str,
            ),
        )
    return {"user_id": int(target_user_id), "username": u["username"], "spam_blocked_until": None}


def get_moderation_audit_logs(actor: dict | None, limit: int = 100) -> list[dict]:
    require(actor, "manage_users")
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM moderation_audit_log ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def is_captcha_enabled() -> bool:
    return get_env_bool("CGS_CAPTCHA_ENABLED", False)


def generate_captcha_challenge() -> tuple[str, int]:
    a = random.randint(2, 9)
    b = random.randint(1, 9)
    return f"Security check: What is {a} + {b}?", a + b


def verify_captcha_answer(expected: int, provided: str | int | None) -> bool:
    try:
        return int(str(provided).strip()) == int(expected)
    except Exception:
        return False
