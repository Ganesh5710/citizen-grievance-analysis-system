"""
Structured rotating-file logging setup.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.config import PROJECT_ROOT, get_env_str

_CONFIGURED = False


def get_logger(name: str = "cgs") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        log_dir = Path(get_env_str("CGS_LOG_DIR", str(PROJECT_ROOT / "logs")))
        if not log_dir.is_absolute():
            log_dir = PROJECT_ROOT / log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "app.log"

        level_name = get_env_str("CGS_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

        root_logger = logging.getLogger("cgs")
        root_logger.setLevel(level)
        if not root_logger.handlers:
            fmt = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
            )
            fh = RotatingFileHandler(
                str(log_file), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            root_logger.addHandler(fh)
        _CONFIGURED = True

    return logging.getLogger(name if name.startswith("cgs") else f"cgs.{name}")
