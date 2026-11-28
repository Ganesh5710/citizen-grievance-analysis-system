"""
Verified SQLite restore script supporting plain `.db` and encrypted `.db.enc` backups.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.database as _db  # noqa: E402
from scripts.backup import _ENC_MAGIC, _derive_stream_block  # noqa: E402


def _decrypt_payload(blob: bytes, passphrase: str) -> bytes:
    header_len = len(_ENC_MAGIC) + 16 + 16
    if len(blob) < header_len + 32 or not blob.startswith(_ENC_MAGIC):
        raise ValueError("Invalid encrypted backup format.")
    salt = blob[len(_ENC_MAGIC) : len(_ENC_MAGIC) + 16]
    nonce = blob[len(_ENC_MAGIC) + 16 : header_len]
    tag = blob[header_len : header_len + 32]
    cipher = blob[header_len + 32 :]
    master = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 120_000, dklen=64)
    enc_key, mac_key = master[:32], master[32:]
    expected_tag = hmac.new(mac_key, blob[:header_len] + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Backup decryption failed: wrong key or corrupted file.")
    out = bytearray(len(cipher))
    for i in range(0, len(cipher), 32):
        blk = _derive_stream_block(enc_key, nonce, i // 32)
        chunk = cipher[i : i + 32]
        out[i : i + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, blk))
    return bytes(out)


def restore_backup(
    backup_file: str,
    *,
    target_db: str | None = None,
    decryption_key: str | None = None,
) -> dict:
    """Verify integrity of `backup_file` (decrypting if `.enc`) and restore safely into `target_db`."""
    bk_path = Path(backup_file)
    if not bk_path.exists():
        raise FileNotFoundError(f"Backup file not found: {bk_path}")

    dst_path = Path(target_db or _db.get_db_path())
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    key_str = decryption_key if decryption_key is not None else os.getenv("CGS_BACKUP_KEY", "")

    with tempfile.TemporaryDirectory(prefix="cgs_restore_") as tmpdir:
        verify_path = Path(tmpdir) / "staged_restore.db"
        raw_blob = bk_path.read_bytes()
        if bk_path.name.endswith(".enc") or raw_blob.startswith(_ENC_MAGIC):
            if not key_str or not key_str.strip():
                raise ValueError("Backup is encrypted; provide CGS_BACKUP_KEY or --key.")
            raw_blob = _decrypt_payload(raw_blob, key_str.strip())
        verify_path.write_bytes(raw_blob)

        src_conn = sqlite3.connect(str(verify_path))
        try:
            chk = src_conn.execute("PRAGMA integrity_check").fetchone()
            if not chk or chk[0] != "ok":
                raise ValueError(f"Backup failed SQLite integrity check: {chk}")
            dst_conn = sqlite3.connect(str(dst_path))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

    return {"restored_to": str(dst_path), "integrity": "ok"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a SQLite backup cleanly after integrity verification.")
    parser.add_argument("--file", required=True, help="Path to .db or .db.enc backup file")
    parser.add_argument("--target", default=None, help="Target SQLite path (default CGS_DB_PATH)")
    parser.add_argument("--yes", action="store_true", help="Confirm overwrite of target database")
    args = parser.parse_args()
    if not args.yes:
        print("Aborted: pass --yes to confirm restoring over the target database.")
        return 2
    res = restore_backup(args.file, target_db=args.target)
    print(f"Restored cleanly to {res['restored_to']} (integrity={res['integrity']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
