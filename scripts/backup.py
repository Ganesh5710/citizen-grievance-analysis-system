"""
Online SQLite backup script with timestamped snapshots, optional authenticated
encryption (CGS_BACKUP_KEY), and rolling retention rotation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.database as _db  # noqa: E402

_ENC_MAGIC = b"CGSBK1"


def _derive_stream_block(enc_key: bytes, nonce: bytes, counter: int) -> bytes:
    return hashlib.sha256(enc_key + nonce + counter.to_bytes(8, "big")).digest()


def _encrypt_payload(raw_bytes: bytes, passphrase: str) -> bytes:
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    master = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 120_000, dklen=64)
    enc_key, mac_key = master[:32], master[32:]
    out = bytearray(len(raw_bytes))
    for i in range(0, len(raw_bytes), 32):
        blk = _derive_stream_block(enc_key, nonce, i // 32)
        chunk = raw_bytes[i : i + 32]
        out[i : i + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, blk))
    header = _ENC_MAGIC + salt + nonce
    tag = hmac.new(mac_key, header + bytes(out), hashlib.sha256).digest()
    return header + tag + bytes(out)


def create_backup(
    *,
    db_path: str | None = None,
    backup_dir: str | None = None,
    keep: int | None = None,
    encryption_key: str | None = None,
) -> dict:
    """Create a consistent SQLite online backup, optionally encrypt, and rotate older backups."""
    src_path = Path(db_path or _db.get_db_path())
    if not src_path.exists():
        raise FileNotFoundError(f"Database file not found: {src_path}")

    out_dir = Path(backup_dir or os.getenv("CGS_BACKUP_DIR", "backups"))
    out_dir.mkdir(parents=True, exist_ok=True)
    keep_n = int(keep if keep is not None else os.getenv("CGS_BACKUP_KEEP", "7"))
    key_str = encryption_key if encryption_key is not None else os.getenv("CGS_BACKUP_KEY", "")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    tmp_db = out_dir / f"cgs_backup_{ts}.db"

    src_conn = sqlite3.connect(str(src_path))
    dst_conn = sqlite3.connect(str(tmp_db))
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()

    encrypted = False
    final_path = tmp_db
    if key_str and key_str.strip():
        raw = tmp_db.read_bytes()
        enc = _encrypt_payload(raw, key_str.strip())
        final_path = out_dir / f"cgs_backup_{ts}.db.enc"
        final_path.write_bytes(enc)
        tmp_db.unlink(missing_ok=True)
        encrypted = True

    # Rotate old backups keeping newest `keep_n`
    pruned = []
    if keep_n > 0:
        snaps = sorted(
            [p for p in out_dir.iterdir() if p.name.startswith("cgs_backup_") and (p.name.endswith(".db") or p.name.endswith(".db.enc"))],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in snaps[keep_n:]:
            old.unlink(missing_ok=True)
            pruned.append(old.name)

    return {
        "backup_path": str(final_path),
        "size_bytes": final_path.stat().st_size,
        "encrypted": encrypted,
        "pruned": pruned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an online SQLite backup with optional encryption & rotation.")
    parser.add_argument("--db", default=None, help="Source SQLite database path (default from CGS_DB_PATH)")
    parser.add_argument("--dir", default=None, help="Backup directory (default from CGS_BACKUP_DIR)")
    parser.add_argument("--keep", type=int, default=None, help="Number of recent backups to keep")
    args = parser.parse_args()
    res = create_backup(db_path=args.db, backup_dir=args.dir, keep=args.keep)
    print(f"Backup created: {res['backup_path']} ({res['size_bytes']} bytes, encrypted={res['encrypted']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
