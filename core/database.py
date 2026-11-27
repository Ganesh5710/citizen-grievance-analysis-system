"""
Database bootstrap: connection factory, schema init, legacy migration, and demo seeding.
Importing this module initializes the SQLite database.
"""

from contextlib import closing
import os
from pathlib import Path
import sqlite3

from core.security import hash_password, verify_password

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH: Path = Path(__file__).parent.parent / "complaints.db"


def get_db_path() -> Path:
    """Resolve DB path dynamically (honors CGS_DB_PATH for test isolation)."""
    override = os.environ.get("CGS_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


DB_PATH: Path = get_db_path()

# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


def get_connection() -> sqlite3.Connection:
    """Return an open SQLite connection with row_factory and FK enforcement."""
    conn = sqlite3.connect(str(get_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    full_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('user','employee','admin','super_admin')),
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT UNIQUE NOT NULL,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    text        TEXT NOT NULL,
    location    TEXT DEFAULT '',
    department  TEXT NOT NULL,
    priority    TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
    status      TEXT NOT NULL DEFAULT 'Pending'
                     CHECK(status IN ('Pending','Assigned','In Progress','Resolved')),
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaint_updates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    employee_id  INTEGER NOT NULL REFERENCES users(id),
    old_status   TEXT,
    new_status   TEXT,
    remark       TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now'))
);
"""


def _migrate_legacy_complaints_if_needed(conn: sqlite3.Connection) -> None:
    """Preserve any Week 1-4 complaints table lacking the v2 schema before init."""
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(complaints)").fetchall()
    }
    if cols and "incident_id" not in cols:
        legacy_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='complaints_legacy_v1'"
        ).fetchone()
        if legacy_exists:
            conn.execute("DROP TABLE complaints")
        else:
            conn.execute("ALTER TABLE complaints RENAME TO complaints_legacy_v1")


def init_db() -> None:
    """Create all tables (idempotent — uses IF NOT EXISTS)."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(get_connection()) as conn, conn:
        _migrate_legacy_complaints_if_needed(conn)
        conn.executescript(_SCHEMA_SQL)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

DEFAULT_DEMO_PASSWORD = "Admin@123"

_DEMO_ACCOUNTS = [
    ("superadmin", "Super Admin",  "super_admin", DEFAULT_DEMO_PASSWORD),
    ("admin",      "Admin User",   "admin",       DEFAULT_DEMO_PASSWORD),
    ("employee",   "Employee One", "employee",    DEFAULT_DEMO_PASSWORD),
]


def seed_demo_accounts() -> bool:
    """
    Insert demo accounts only when the users table is empty.
    Returns True if seeding happened, False if skipped.
    """
    with closing(get_connection()) as conn, conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return False

        for username, full_name, role, password in _DEMO_ACCOUNTS:
            pw_hash, salt = hash_password(password)
            conn.execute(
                "INSERT INTO users (username, full_name, password_hash, salt, role) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, full_name, pw_hash, salt, role),
            )
        return True


def has_default_demo_passwords() -> bool:
    """Return True if any seeded demo account still uses DEFAULT_DEMO_PASSWORD."""
    usernames = [acct[0] for acct in _DEMO_ACCOUNTS]
    placeholders = ",".join("?" for _ in usernames)
    with closing(get_connection()) as conn:
        rows = conn.execute(
            f"SELECT password_hash, salt FROM users WHERE username IN ({placeholders})",
            tuple(usernames),
        ).fetchall()
    return any(
        verify_password(DEFAULT_DEMO_PASSWORD, row["password_hash"], row["salt"])
        for row in rows
    )


# ---------------------------------------------------------------------------
# Auto-init on import
# ---------------------------------------------------------------------------
init_db()
seed_demo_accounts()
