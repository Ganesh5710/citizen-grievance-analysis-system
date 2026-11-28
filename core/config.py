"""
Centralized configuration loaded from .env via python-dotenv and environment variables.
All secrets, thresholds, and service settings come from here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_env_str(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    return default if val is None or val == "" else val


def get_env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def get_env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def get_env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")
