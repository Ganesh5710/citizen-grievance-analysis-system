"""
Database bootstrap: SQLite WAL + busy_timeout connection factory, schema init,
migrations, Hyderabad wards seeding, SLA seeding, and hardened one-time account seeding.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

from core.config import get_env_str
from core.logging_setup import get_logger
from core.security import generate_temp_password, hash_password

_logger = get_logger("database")

# ---------------------------------------------------------------------------
# Path & DATABASE_URL abstraction
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH: Path = Path(__file__).parent.parent / "complaints.db"


def get_database_url() -> str:
    """Return configured DATABASE_URL, defaulting to local SQLite file path."""
    override_path = os.environ.get("CGS_DB_PATH")
    if override_path:
        return f"sqlite:///{override_path}"
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url:
        return env_url
    return f"sqlite:///{DEFAULT_DB_PATH}"


def get_database_backend(url: str | None = None) -> str:
    """Return 'postgres' for postgresql:// / postgres:// URLs, else 'sqlite'."""
    u = (url if url is not None else get_database_url()).strip().lower()
    if u.startswith(("postgresql://", "postgres://", "postgresql+")):
        return "postgres"
    return "sqlite"


def get_db_path() -> Path:
    """Resolve SQLite DB path dynamically (honors CGS_DB_PATH and sqlite:/// URLs)."""
    override = os.environ.get("CGS_DB_PATH")
    if override:
        return Path(override)
    env_url = os.environ.get("DATABASE_URL", "").strip()
    if env_url.lower().startswith("sqlite:///"):
        return Path(env_url[len("sqlite:///"):])
    return DEFAULT_DB_PATH


def translate_sql_placeholders(sql: str, params: tuple | list = ()) -> tuple[str, dict[str, Any]]:
    """Convert sqlite '?' positional placeholders to SQLAlchemy ':p0, :p1, ...' named parameters."""
    idx = 0
    param_map: dict[str, Any] = {}
    out: list[str] = []
    in_single = False
    for ch in sql:
        if ch == "'":
            in_single = not in_single
            out.append(ch)
        elif ch == "?" and not in_single:
            key = f"p{idx}"
            out.append(f":{key}")
            param_map[key] = params[idx] if idx < len(params) else None
            idx += 1
        else:
            out.append(ch)
    return "".join(out), param_map


def translate_sqlite_ddl_for_postgres(ddl: str) -> str:
    """Convert core SQLite DDL constructs into PostgreSQL-compatible DDL."""
    out = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "SERIAL PRIMARY KEY",
        ddl,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"\bdatetime\s*\(\s*'now'\s*\)", "CURRENT_TIMESTAMP", out, flags=re.IGNORECASE)
    out = re.sub(r"\bREAL\b", "DOUBLE PRECISION", out, flags=re.IGNORECASE)
    return out


def utc_now_str(now: datetime | None = None) -> str:
    """Return current UTC timestamp formatted as 'YYYY-MM-DD HH:MM:SS'."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


DB_PATH: Path = get_db_path()

# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


class _PgCursorProxy:
    """Minimal DB-API cursor shim around SQLAlchemy Result for unified callers."""

    def __init__(self, result: Any):
        self._result = result
        self.lastrowid = getattr(result, "lastrowid", None)
        self.rowcount = result.rowcount

    def fetchone(self):
        r = self._result.mappings().first()
        return dict(r) if r is not None else None

    def fetchall(self):
        return [dict(r) for r in self._result.mappings().all()]


class PostgresConnectionAdapter:
    """
    Unified DB connection adapter wrapping SQLAlchemy Core when DATABASE_URL
    points to PostgreSQL. Translates '?' positional placeholders cleanly.
    """

    def __init__(self, url: str):
        try:
            from sqlalchemy import create_engine, text  # noqa: WPS433
        except ImportError as exc:
            raise RuntimeError("SQLAlchemy + psycopg driver required for PostgreSQL DATABASE_URL.") from exc
        self._text = text
        self._engine = create_engine(url, pool_pre_ping=True, future=True)
        self._conn = self._engine.connect()

    def execute(self, sql: str, params: tuple | list = ()):
        if sql.strip().upper().startswith("PRAGMA"):
            return _PgCursorProxy(self._conn.execute(self._text("SELECT 1")))
        pg_sql, bound = translate_sql_placeholders(sql, params)
        return _PgCursorProxy(self._conn.execute(self._text(pg_sql), bound))

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()
        self._engine.dispose()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


def get_connection() -> sqlite3.Connection | PostgresConnectionAdapter:
    """
    Return a database connection according to DATABASE_URL (SQLite default or PostgreSQL).
    SQLite enables WAL mode, busy_timeout, Row factory, and FK enforcement.
    """
    url = get_database_url()
    if get_database_backend(url) == "postgres":
        return PostgresConnectionAdapter(url)

    conn = sqlite3.connect(str(get_db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS departments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS wards (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT UNIQUE NOT NULL,
    lat     REAL NOT NULL,
    lon     REAL NOT NULL,
    polygon TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS states (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT UNIQUE NOT NULL,
    code      TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS districts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    state_id         INTEGER NOT NULL REFERENCES states(id),
    name             TEXT NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1,
    allow_self_claim INTEGER NOT NULL DEFAULT 0,
    UNIQUE(state_id, name)
);

CREATE TABLE IF NOT EXISTS mandals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    district_id INTEGER NOT NULL REFERENCES districts(id),
    name        TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    UNIQUE(district_id, name)
);

CREATE TABLE IF NOT EXISTS areas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    district_id INTEGER NOT NULL REFERENCES districts(id),
    mandal_id   INTEGER REFERENCES mandals(id),
    name        TEXT NOT NULL,
    latitude    REAL,
    longitude   REAL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    UNIQUE(district_id, name)
);

CREATE TABLE IF NOT EXISTS pincodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pincode     TEXT NOT NULL CHECK(length(pincode) = 6),
    area_id     INTEGER NOT NULL REFERENCES areas(id),
    district_id INTEGER NOT NULL REFERENCES districts(id),
    state_id    INTEGER NOT NULL REFERENCES states(id),
    UNIQUE(pincode, area_id)
);

CREATE INDEX IF NOT EXISTS idx_pincodes_code ON pincodes(pincode);

CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT UNIQUE NOT NULL,
    full_name             TEXT NOT NULL,
    password_hash         TEXT NOT NULL,
    salt                  TEXT NOT NULL,
    role                  TEXT NOT NULL CHECK(role IN ('user','employee','admin','super_admin')),
    department_id         INTEGER REFERENCES departments(id),
    district_id           INTEGER REFERENCES districts(id),
    mandal_id             INTEGER REFERENCES mandals(id),
    is_active             INTEGER NOT NULL DEFAULT 1,
    must_change_password  INTEGER NOT NULL DEFAULT 0,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until          TEXT,
    preferred_language    TEXT NOT NULL DEFAULT 'en',
    phone                 TEXT DEFAULT '',
    email                 TEXT DEFAULT '',
    email_verified        INTEGER NOT NULL DEFAULT 0,
    email_notifications   INTEGER NOT NULL DEFAULT 1,
    in_app_notifications  INTEGER NOT NULL DEFAULT 1,
    confirmed_spam_count  INTEGER NOT NULL DEFAULT 0,
    spam_blocked_until    TEXT,
    anonymized_at         TEXT,
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS admin_jurisdictions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    district_id   INTEGER NOT NULL REFERENCES districts(id),
    department_id INTEGER REFERENCES departments(id),
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, district_id, department_id)
);

CREATE TABLE IF NOT EXISTS login_attempts (
    username        TEXT PRIMARY KEY,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    last_attempt_at TEXT
);

CREATE TABLE IF NOT EXISTS sla_policies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    priority      TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
    target_hours  INTEGER NOT NULL CHECK(target_hours > 0),
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(department_id, priority)
);

CREATE TABLE IF NOT EXISTS incident_groups (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    title                TEXT NOT NULL DEFAULT '',
    department_id        INTEGER NOT NULL REFERENCES departments(id),
    district_id          INTEGER REFERENCES districts(id),
    primary_complaint_id INTEGER,
    member_count         INTEGER NOT NULL DEFAULT 1,
    support_count        INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'Open',
    created_at           TEXT DEFAULT (datetime('now')),
    updated_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaint_supports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    created_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(complaint_id, user_id)
);

CREATE TABLE IF NOT EXISTS complaints (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id      TEXT UNIQUE NOT NULL,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    text             TEXT NOT NULL,
    location         TEXT NOT NULL DEFAULT '',
    department_id    INTEGER NOT NULL REFERENCES departments(id),
    ward_id          INTEGER REFERENCES wards(id),
    state_id         INTEGER REFERENCES states(id),
    district_id      INTEGER REFERENCES districts(id),
    mandal_id        INTEGER REFERENCES mandals(id),
    area_id          INTEGER REFERENCES areas(id),
    pincode          TEXT DEFAULT '',
    latitude         REAL,
    longitude        REAL,
    priority         TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
    status           TEXT NOT NULL DEFAULT 'Pending'
                         CHECK(status IN ('Submitted','Awaiting Assignment','Pending','Assigned','In Progress','Resolved','Reopened','Closed','Withdrawn')),
    assigned_to      INTEGER REFERENCES users(id),
    assigned_by      INTEGER REFERENCES users(id),
    assigned_at      TEXT,
    accepted_at      TEXT,
    assignment_instructions TEXT DEFAULT '',
    due_at           TEXT,
    escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level IN (0, 1, 2)),
    unassigned_escalated INTEGER NOT NULL DEFAULT 0,
    unassigned_escalated_at TEXT,
    resolved_at             TEXT,
    reopen_count            INTEGER NOT NULL DEFAULT 0,
    is_deleted              INTEGER NOT NULL DEFAULT 0,
    is_spam                 INTEGER NOT NULL DEFAULT 0,
    is_demo_spike           INTEGER NOT NULL DEFAULT 0,
    ml_suggested_department TEXT,
    ml_confidence           REAL,
    ml_top_terms            TEXT DEFAULT '[]',
    duration_hours          REAL,
    duration_matched_text   TEXT DEFAULT '',
    duration_confidence     REAL,
    is_recurring            INTEGER NOT NULL DEFAULT 0,
    priority_reason         TEXT DEFAULT '',
    incident_group_id       INTEGER REFERENCES incident_groups(id),
    withdrawal_requested    INTEGER NOT NULL DEFAULT 0,
    withdrawal_request_reason TEXT DEFAULT '',
    withdrawal_requested_at TEXT,
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaint_updates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    employee_id  INTEGER REFERENCES users(id),
    actor_label  TEXT NOT NULL DEFAULT '',
    field_name   TEXT NOT NULL DEFAULT 'status',
    old_status   TEXT DEFAULT '',
    new_status   TEXT DEFAULT '',
    old_value    TEXT DEFAULT '',
    new_value    TEXT DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'staff_update',
    remark       TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaint_images (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id      INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    uploaded_by       INTEGER NOT NULL REFERENCES users(id),
    stored_filename   TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    mime_type         TEXT NOT NULL,
    file_size         INTEGER NOT NULL,
    photo_stage       TEXT NOT NULL DEFAULT 'initial',
    created_at        TEXT DEFAULT (datetime('now')),
    is_removed        INTEGER NOT NULL DEFAULT 0,
    removed_by        INTEGER REFERENCES users(id),
    removed_at        TEXT
);

CREATE TABLE IF NOT EXISTS complaint_feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id     INTEGER NOT NULL REFERENCES complaints(id),
    user_id          INTEGER NOT NULL REFERENCES users(id),
    resolution_cycle INTEGER NOT NULL DEFAULT 0,
    rating           INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment          TEXT NOT NULL DEFAULT '',
    accepted         INTEGER NOT NULL CHECK(accepted IN (0, 1)),
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(complaint_id, resolution_cycle)
);

CREATE TABLE IF NOT EXISTS review_queue (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id            INTEGER NOT NULL REFERENCES complaints(id),
    reason                  TEXT NOT NULL,
    ml_suggested_department TEXT,
    ml_confidence           REAL,
    citizen_department      TEXT DEFAULT '',
    status                  TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Resolved')),
    resolution_action       TEXT DEFAULT '',
    resolved_department     TEXT DEFAULT '',
    resolved_by             INTEGER REFERENCES users(id),
    resolution_note         TEXT DEFAULT '',
    created_at              TEXT DEFAULT (datetime('now')),
    resolved_at             TEXT
);

CREATE TABLE IF NOT EXISTS ml_models (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    version            TEXT UNIQUE NOT NULL,
    artifact_path      TEXT NOT NULL,
    metadata_path      TEXT NOT NULL,
    dept_model_name    TEXT DEFAULT '',
    dept_test_f1       REAL,
    priority_model_name TEXT DEFAULT '',
    priority_test_f1   REAL,
    training_rows      INTEGER DEFAULT 0,
    is_active          INTEGER NOT NULL DEFAULT 0,
    created_by         INTEGER REFERENCES users(id),
    created_at         TEXT DEFAULT (datetime('now')),
    activated_at       TEXT
);

CREATE TABLE IF NOT EXISTS priority_rule_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id        INTEGER REFERENCES users(id),
    actor_username  TEXT NOT NULL DEFAULT '',
    old_config_json TEXT NOT NULL DEFAULT '{}',
    new_config_json TEXT NOT NULL DEFAULT '{}',
    note            TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS priority_feedback_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id       INTEGER NOT NULL REFERENCES complaints(id),
    incident_id        TEXT NOT NULL DEFAULT '',
    text               TEXT NOT NULL,
    duration_hours     REAL,
    matched_text       TEXT DEFAULT '',
    corrected_priority TEXT NOT NULL CHECK(corrected_priority IN ('High','Medium','Low')),
    corrected_by       INTEGER REFERENCES users(id),
    created_at         TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS duration_extraction_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER REFERENCES complaints(id),
    incident_id  TEXT DEFAULT '',
    text         TEXT NOT NULL,
    found_hours  REAL,
    matched_text TEXT DEFAULT '',
    confidence   REAL,
    status       TEXT NOT NULL DEFAULT 'Failed',
    reason       TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER REFERENCES users(id),
    target_role  TEXT DEFAULT '',
    complaint_id INTEGER REFERENCES complaints(id),
    incident_id  TEXT DEFAULT '',
    type         TEXT NOT NULL DEFAULT 'general',
    title        TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    message      TEXT NOT NULL DEFAULT '',
    is_read      INTEGER NOT NULL DEFAULT 0,
    channel      TEXT NOT NULL DEFAULT 'in_app',
    status       TEXT NOT NULL DEFAULT 'sent',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS location_load_logs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path          TEXT NOT NULL,
    default_state        TEXT NOT NULL,
    total_rows           INTEGER NOT NULL DEFAULT 0,
    loaded_areas         INTEGER NOT NULL DEFAULT 0,
    loaded_pincodes      INTEGER NOT NULL DEFAULT 0,
    skipped_rows         INTEGER NOT NULL DEFAULT 0,
    mandal_level_present INTEGER NOT NULL DEFAULT 0,
    notes                TEXT DEFAULT '',
    loaded_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS moderation_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id        INTEGER REFERENCES users(id),
    actor_username  TEXT NOT NULL DEFAULT '',
    target_user_id  INTEGER REFERENCES users(id),
    target_username TEXT NOT NULL DEFAULT '',
    complaint_id    INTEGER REFERENCES complaints(id),
    action          TEXT NOT NULL,
    reason          TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaint_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id    INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    author_id       INTEGER NOT NULL REFERENCES users(id),
    author_username TEXT NOT NULL DEFAULT '',
    author_role     TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS password_reset_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email      TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    code_salt  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS password_reset_rate_limits (
    identifier    TEXT PRIMARY KEY,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    window_start  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public_status_rate_limits (
    client_key    TEXT PRIMARY KEY,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    window_start  TEXT NOT NULL
);
"""

_DEFAULT_DEPARTMENTS = [
    ("Water",           "Water supply, pipelines, leakage, and tap connections"),
    ("Roads",           "Road damage, potholes, pavements, and resurfacing"),
    ("Electricity",     "Power outages, transformers, streetlights, and exposed wires"),
    ("Sanitation",      "Garbage collection, drainage overflow, sewage, and waste"),
    ("Traffic",         "Traffic signals, road junctions, signage, and congestion"),
    ("General / Other", "Civic grievances not covered by specialized departments"),
]

_HYDERABAD_WARDS = [
    ("Banjara Hills", 17.4156, 78.4347),
    ("Jubilee Hills", 17.4326, 78.4071),
    ("Madhapur",      17.4486, 78.3908),
    ("Gachibowli",    17.4401, 78.3489),
    ("Kondapur",      17.4622, 78.3568),
    ("Kukatpally",    17.4948, 78.3996),
    ("Miyapur",       17.4968, 78.3614),
    ("Secunderabad",  17.4399, 78.4983),
    ("Begumpet",      17.4447, 78.4664),
    ("Ameerpet",      17.4375, 78.4483),
    ("Abids",         17.3914, 78.4765),
    ("Charminar",     17.3616, 78.4747),
    ("Dilsukhnagar",  17.3688, 78.5247),
    ("LB Nagar",      17.3457, 78.5522),
    ("Uppal",         17.4058, 78.5591),
]

DEFAULT_SLA_HOURS: dict[str, int] = {
    "High":   8,
    "Medium": 48,
    "Low":    120,
}

_DEMO_ACCOUNT_SPECS = [
    ("superadmin", "Super Admin",  "super_admin", None,    "CGS_SEED_SUPERADMIN_PASSWORD"),
    ("admin",      "Admin User",   "admin",       None,    "CGS_SEED_ADMIN_PASSWORD"),
    ("employee",   "Employee One", "employee",    "Water", "CGS_SEED_EMPLOYEE_PASSWORD"),
]


# ---------------------------------------------------------------------------
# Migrations & Seeding
# ---------------------------------------------------------------------------


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _table_sql(conn: sqlite3.Connection, table_name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row["sql"] if row and row["sql"] else ""


def _seed_departments_in_conn(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
    if count == 0:
        for name, description in _DEFAULT_DEPARTMENTS:
            conn.execute(
                "INSERT INTO departments (name, description, is_active) VALUES (?, ?, 1)",
                (name, description),
            )
    else:
        fallback = conn.execute(
            "SELECT id FROM departments WHERE lower(name) = 'general / other'"
        ).fetchone()
        if fallback is None:
            conn.execute(
                "INSERT INTO departments (name, description, is_active) VALUES (?, ?, 1)",
                _DEFAULT_DEPARTMENTS[-1],
            )


def _seed_wards_in_conn(conn: sqlite3.Connection) -> None:
    for name, lat, lon in _HYDERABAD_WARDS:
        conn.execute(
            "INSERT OR IGNORE INTO wards (name, lat, lon, polygon) VALUES (?, ?, ?, '')",
            (name, lat, lon),
        )


def _seed_sla_policies_in_conn(conn: sqlite3.Connection) -> None:
    depts = conn.execute("SELECT id FROM departments").fetchall()
    for dept in depts:
        for priority, hours in DEFAULT_SLA_HOURS.items():
            conn.execute(
                "INSERT OR IGNORE INTO sla_policies (department_id, priority, target_hours) "
                "VALUES (?, ?, ?)",
                (dept["id"], priority, hours),
            )


def _compute_due_at_from(created_at_str: str | None, target_hours: int) -> str:
    base_dt = datetime.now(timezone.utc)
    if created_at_str:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                base_dt = datetime.strptime(created_at_str, fmt).replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue
    return (base_dt + timedelta(hours=target_hours)).strftime("%Y-%m-%d %H:%M:%S")


def _migrate_users(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "users")
    if not cols:
        return
    add_cols = [
        ("department_id",         "INTEGER REFERENCES departments(id)"),
        ("district_id",           "INTEGER REFERENCES districts(id)"),
        ("mandal_id",             "INTEGER REFERENCES mandals(id)"),
        ("is_active",             "INTEGER NOT NULL DEFAULT 1"),
        ("must_change_password",  "INTEGER NOT NULL DEFAULT 0"),
        ("failed_login_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("locked_until",          "TEXT"),
        ("preferred_language",    "TEXT NOT NULL DEFAULT 'en'"),
        ("phone",                 "TEXT DEFAULT ''"),
        ("email",                 "TEXT DEFAULT ''"),
        ("email_verified",        "INTEGER NOT NULL DEFAULT 0"),
        ("email_notifications",   "INTEGER NOT NULL DEFAULT 1"),
        ("in_app_notifications",  "INTEGER NOT NULL DEFAULT 1"),
        ("confirmed_spam_count",  "INTEGER NOT NULL DEFAULT 0"),
        ("spam_blocked_until",    "TEXT"),
        ("anonymized_at",         "TEXT"),
    ]
    for col_name, col_def in add_cols:
        if col_name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")

    water = conn.execute("SELECT id FROM departments WHERE name = 'Water'").fetchone()
    if water:
        conn.execute(
            "UPDATE users SET department_id = ? "
            "WHERE username = 'employee' AND role = 'employee' AND department_id IS NULL",
            (water["id"],),
        )


def _migrate_complaint_updates(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "complaint_updates")
    if not cols:
        return
    ddl = _table_sql(conn, "complaint_updates")
    needs_rebuild = (
        "employee_id  INTEGER NOT NULL" in ddl
        or "employee_id INTEGER NOT NULL" in ddl
        or "actor_label" not in cols
    )
    if not needs_rebuild:
        if "field_name" not in cols:
            conn.execute(
                "ALTER TABLE complaint_updates ADD COLUMN field_name TEXT NOT NULL DEFAULT 'status'"
            )
        if "old_value" not in cols:
            conn.execute("ALTER TABLE complaint_updates ADD COLUMN old_value TEXT DEFAULT ''")
            conn.execute("UPDATE complaint_updates SET old_value = COALESCE(old_status, '')")
        if "new_value" not in cols:
            conn.execute("ALTER TABLE complaint_updates ADD COLUMN new_value TEXT DEFAULT ''")
            conn.execute("UPDATE complaint_updates SET new_value = COALESCE(new_status, '')")
        if "source" not in cols:
            conn.execute(
                "ALTER TABLE complaint_updates ADD COLUMN source TEXT NOT NULL DEFAULT 'staff_update'"
            )
        return

    conn.execute("""
        CREATE TABLE complaint_updates_new (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL REFERENCES complaints(id),
            employee_id  INTEGER REFERENCES users(id),
            actor_label  TEXT NOT NULL DEFAULT '',
            field_name   TEXT NOT NULL DEFAULT 'status',
            old_status   TEXT DEFAULT '',
            new_status   TEXT DEFAULT '',
            old_value    TEXT DEFAULT '',
            new_value    TEXT DEFAULT '',
            source       TEXT NOT NULL DEFAULT 'staff_update',
            remark       TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)

    rows = conn.execute("""
        SELECT cu.*, u.username AS uname
        FROM complaint_updates cu
        LEFT JOIN users u ON u.id = cu.employee_id
        ORDER BY cu.id
    """).fetchall()
    for r in rows:
        field_name = r["field_name"] if "field_name" in cols else "status"
        old_status = (r["old_status"] if "old_status" in cols else "") or ""
        new_status = (r["new_status"] if "new_status" in cols else "") or ""
        old_val = (r["old_value"] if "old_value" in cols else old_status) or ""
        new_val = (r["new_value"] if "new_value" in cols else new_status) or ""
        src = (r["source"] if "source" in cols else "staff_update") or "staff_update"
        actor = (r["actor_label"] if "actor_label" in cols else None) or r["uname"] or "system"
        conn.execute(
            """
            INSERT INTO complaint_updates_new
                (id, complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["id"], r["complaint_id"], r["employee_id"], actor, field_name,
                old_status, new_status, old_val, new_val, src, r["remark"] or "", r["created_at"],
            ),
        )

    conn.execute("DROP TABLE complaint_updates")
    conn.execute("ALTER TABLE complaint_updates_new RENAME TO complaint_updates")


def _migrate_complaints(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "complaints")
    if not cols:
        return

    if "incident_id" not in cols:
        legacy_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='complaints_legacy_v1'"
        ).fetchone()
        if legacy_exists:
            conn.execute("DROP TABLE complaints")
        else:
            conn.execute("ALTER TABLE complaints RENAME TO complaints_legacy_v1")
        return

    ddl = _table_sql(conn, "complaints")
    required_cols = {
        "department_id", "ward_id", "latitude", "longitude", "assigned_to", "assigned_at",
        "due_at", "escalation_level", "resolved_at", "reopen_count", "is_deleted",
    }
    needs_rebuild = (
        not required_cols.issubset(cols)
        or "'Reopened'" not in ddl
        or "'Closed'" not in ddl
        or "'Awaiting Assignment'" not in ddl
        or "'Submitted'" not in ddl
        or "'Withdrawn'" not in ddl
    )
    if not needs_rebuild:
        return

    _seed_departments_in_conn(conn)
    _seed_sla_policies_in_conn(conn)

    dept_rows = conn.execute("SELECT id, name FROM departments").fetchall()
    name_to_id = {r["name"].strip().lower(): r["id"] for r in dept_rows}
    fallback_id = name_to_id["general / other"]

    sla_rows = conn.execute("SELECT department_id, priority, target_hours FROM sla_policies").fetchall()
    sla_map = {(r["department_id"], r["priority"]): r["target_hours"] for r in sla_rows}

    conn.execute("""
        CREATE TABLE complaints_new (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id      TEXT UNIQUE NOT NULL,
            user_id          INTEGER NOT NULL REFERENCES users(id),
            text             TEXT NOT NULL,
            location         TEXT NOT NULL DEFAULT '',
            department_id    INTEGER NOT NULL REFERENCES departments(id),
            ward_id          INTEGER REFERENCES wards(id),
            state_id         INTEGER REFERENCES states(id),
            district_id      INTEGER REFERENCES districts(id),
            mandal_id        INTEGER REFERENCES mandals(id),
            area_id          INTEGER REFERENCES areas(id),
            pincode          TEXT DEFAULT '',
            latitude         REAL,
            longitude        REAL,
            priority         TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
            status           TEXT NOT NULL DEFAULT 'Pending'
                                 CHECK(status IN ('Submitted','Awaiting Assignment','Pending','Assigned','In Progress','Resolved','Reopened','Closed','Withdrawn')),
            assigned_to      INTEGER REFERENCES users(id),
            assigned_by      INTEGER REFERENCES users(id),
            assigned_at      TEXT,
            accepted_at      TEXT,
            assignment_instructions TEXT DEFAULT '',
            due_at           TEXT,
            escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level IN (0, 1, 2)),
            unassigned_escalated INTEGER NOT NULL DEFAULT 0,
            unassigned_escalated_at TEXT,
            resolved_at      TEXT,
            reopen_count     INTEGER NOT NULL DEFAULT 0,
            is_deleted       INTEGER NOT NULL DEFAULT 0,
            ml_suggested_department TEXT,
            ml_confidence           REAL,
            ml_top_terms            TEXT DEFAULT '[]',
            duration_hours          REAL,
            duration_matched_text   TEXT DEFAULT '',
            duration_confidence     REAL,
            is_recurring            INTEGER NOT NULL DEFAULT 0,
            priority_reason         TEXT DEFAULT '',
            created_at       TEXT DEFAULT (datetime('now')),
            updated_at       TEXT DEFAULT (datetime('now'))
        )
    """)

    old_rows = conn.execute("SELECT * FROM complaints ORDER BY id").fetchall()
    for r in old_rows:
        if "department_id" in cols and r["department_id"] is not None:
            mapped_dept_id = r["department_id"]
        else:
            old_dept = (r["department"] if "department" in cols else "") or ""
            mapped_dept_id = name_to_id.get(old_dept.strip().lower(), fallback_id)

        prio = r["priority"]
        target_hours = sla_map.get((mapped_dept_id, prio), DEFAULT_SLA_HOURS.get(prio, 48))
        due_at = (r["due_at"] if "due_at" in cols else None) or _compute_due_at_from(
            r["created_at"], target_hours
        )
        ward_id = r["ward_id"] if "ward_id" in cols else None
        lat = r["latitude"] if "latitude" in cols else None
        lon = r["longitude"] if "longitude" in cols else None
        assigned_to = r["assigned_to"] if "assigned_to" in cols else None
        assigned_at = r["assigned_at"] if "assigned_at" in cols else None
        esc_level = (r["escalation_level"] if "escalation_level" in cols else 0) or 0
        resolved_at = r["resolved_at"] if "resolved_at" in cols else None
        if resolved_at is None and r["status"] in ("Resolved", "Closed"):
            resolved_at = r["updated_at"] or r["created_at"]
        reopen_count = (r["reopen_count"] if "reopen_count" in cols else 0) or 0
        is_del = (r["is_deleted"] if "is_deleted" in cols else 0) or 0

        conn.execute(
            """
            INSERT INTO complaints_new
                (id, incident_id, user_id, text, location, department_id, ward_id,
                 state_id, district_id, mandal_id, area_id, pincode,
                 latitude, longitude, priority, status, assigned_to, assigned_by, assigned_at,
                 accepted_at, assignment_instructions, due_at, escalation_level,
                 unassigned_escalated, unassigned_escalated_at,
                 resolved_at, reopen_count, is_deleted,
                 ml_suggested_department, ml_confidence, ml_top_terms,
                 duration_hours, duration_matched_text, duration_confidence, is_recurring, priority_reason,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["id"], r["incident_id"], r["user_id"], r["text"], r["location"] or "",
                mapped_dept_id, ward_id,
                r["state_id"] if "state_id" in cols else None,
                r["district_id"] if "district_id" in cols else None,
                r["mandal_id"] if "mandal_id" in cols else None,
                r["area_id"] if "area_id" in cols else None,
                (r["pincode"] if "pincode" in cols else "") or "",
                lat, lon, prio, r["status"],
                assigned_to,
                r["assigned_by"] if "assigned_by" in cols else None,
                assigned_at,
                r["accepted_at"] if "accepted_at" in cols else None,
                (r["assignment_instructions"] if "assignment_instructions" in cols else "") or "",
                due_at, esc_level,
                (r["unassigned_escalated"] if "unassigned_escalated" in cols else 0) or 0,
                r["unassigned_escalated_at"] if "unassigned_escalated_at" in cols else None,
                resolved_at, reopen_count, is_del,
                r["ml_suggested_department"] if "ml_suggested_department" in cols else None,
                r["ml_confidence"] if "ml_confidence" in cols else None,
                (r["ml_top_terms"] if "ml_top_terms" in cols else "[]") or "[]",
                r["duration_hours"] if "duration_hours" in cols else None,
                (r["duration_matched_text"] if "duration_matched_text" in cols else "") or "",
                r["duration_confidence"] if "duration_confidence" in cols else None,
                (r["is_recurring"] if "is_recurring" in cols else 0) or 0,
                (r["priority_reason"] if "priority_reason" in cols else "") or "",
                r["created_at"], r["updated_at"],
            ),
        )

    conn.execute("DROP TABLE complaints")
    conn.execute("ALTER TABLE complaints_new RENAME TO complaints")


def init_db() -> None:
    """Create all tables and run schema migrations idempotently."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = OFF")
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS departments (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT UNIQUE NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    is_active   INTEGER NOT NULL DEFAULT 1,
                    created_at  TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS wards (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    name    TEXT UNIQUE NOT NULL,
                    lat     REAL NOT NULL,
                    lon     REAL NOT NULL,
                    polygon TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sla_policies (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL REFERENCES departments(id),
                    priority      TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
                    target_hours  INTEGER NOT NULL CHECK(target_hours > 0),
                    updated_at    TEXT DEFAULT (datetime('now')),
                    UNIQUE(department_id, priority)
                )
            """)
            _seed_departments_in_conn(conn)
            _seed_wards_in_conn(conn)
            _seed_sla_policies_in_conn(conn)
            _migrate_users(conn)
            _migrate_complaints(conn)
            _migrate_complaint_updates(conn)
            conn.executescript(_SCHEMA_SQL)
            d_cols = _table_columns(conn, "districts")
            if d_cols and "allow_self_claim" not in d_cols:
                conn.execute(
                    "ALTER TABLE districts ADD COLUMN allow_self_claim INTEGER NOT NULL DEFAULT 0"
                )
            c_cols = _table_columns(conn, "complaints")
            for col_name, col_def in (
                ("state_id", "INTEGER REFERENCES states(id)"),
                ("district_id", "INTEGER REFERENCES districts(id)"),
                ("mandal_id", "INTEGER REFERENCES mandals(id)"),
                ("area_id", "INTEGER REFERENCES areas(id)"),
                ("pincode", "TEXT DEFAULT ''"),
                ("assigned_by", "INTEGER REFERENCES users(id)"),
                ("accepted_at", "TEXT"),
                ("assignment_instructions", "TEXT DEFAULT ''"),
                ("unassigned_escalated", "INTEGER NOT NULL DEFAULT 0"),
                ("unassigned_escalated_at", "TEXT"),
                ("ml_suggested_department", "TEXT"),
                ("ml_confidence", "REAL"),
                ("ml_top_terms", "TEXT DEFAULT '[]'"),
                ("duration_hours", "REAL"),
                ("duration_matched_text", "TEXT DEFAULT ''"),
                ("duration_confidence", "REAL"),
                ("is_recurring", "INTEGER NOT NULL DEFAULT 0"),
                ("priority_reason", "TEXT DEFAULT ''"),
                ("incident_group_id", "INTEGER REFERENCES incident_groups(id)"),
                ("is_spam", "INTEGER NOT NULL DEFAULT 0"),
                ("is_demo_spike", "INTEGER NOT NULL DEFAULT 0"),
                ("withdrawal_requested", "INTEGER NOT NULL DEFAULT 0"),
                ("withdrawal_request_reason", "TEXT DEFAULT ''"),
                ("withdrawal_requested_at", "TEXT"),
            ):
                if col_name not in c_cols:
                    conn.execute(f"ALTER TABLE complaints ADD COLUMN {col_name} {col_def}")
            img_cols = _table_columns(conn, "complaint_images")
            if img_cols and "photo_stage" not in img_cols:
                conn.execute(
                    "ALTER TABLE complaint_images ADD COLUMN photo_stage TEXT NOT NULL DEFAULT 'initial'"
                )
            n_cols = _table_columns(conn, "notifications")
            if n_cols:
                for col_name, col_def in (
                    ("type", "TEXT NOT NULL DEFAULT 'general'"),
                    ("body", "TEXT NOT NULL DEFAULT ''"),
                    ("channel", "TEXT NOT NULL DEFAULT 'in_app'"),
                    ("status", "TEXT NOT NULL DEFAULT 'sent'"),
                    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
                    ("last_error", "TEXT DEFAULT ''"),
                ):
                    if col_name not in n_cols:
                        conn.execute(f"ALTER TABLE notifications ADD COLUMN {col_name} {col_def}")
            _seed_departments_in_conn(conn)
            _seed_wards_in_conn(conn)
            _seed_sla_policies_in_conn(conn)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_complaints_scope_created
                    ON complaints(is_deleted, district_id, department_id, status, created_at);
                CREATE INDEX IF NOT EXISTS idx_complaints_assigned_status
                    ON complaints(is_deleted, assigned_to, status);
                CREATE INDEX IF NOT EXISTS idx_complaints_user_created
                    ON complaints(user_id, is_deleted, created_at);
                CREATE INDEX IF NOT EXISTS idx_updates_complaint_id
                    ON complaint_updates(complaint_id, id);
                CREATE INDEX IF NOT EXISTS idx_notifs_user_chan_read
                    ON notifications(user_id, channel, is_read, id);
                """
            )
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def seed_demo_accounts() -> dict[str, str]:
    """
    Insert initial staff accounts ONLY when the users table is empty.
    Reads optional password overrides from environment variables; otherwise generates a
    random password per account and prints it once. Always forces password change on first login.
    Returns {username: generated_password} for freshly seeded accounts (empty dict if skipped).
    """
    with closing(get_connection()) as conn, conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return {}

        dept_rows = conn.execute("SELECT id, name FROM departments").fetchall()
        dept_map = {r["name"]: r["id"] for r in dept_rows}
        fallback_env = get_env_str("CGS_SEED_PASSWORD", "")

        seeded_creds: dict[str, str] = {}
        for username, full_name, role, dept_name, env_key in _DEMO_ACCOUNT_SPECS:
            raw_pw = get_env_str(env_key, "") or fallback_env
            generated = False
            if not raw_pw:
                raw_pw = generate_temp_password(14)
                generated = True

            pw_hash, salt = hash_password(raw_pw)
            dept_id = dept_map.get(dept_name) if dept_name else None
            conn.execute(
                """
                INSERT INTO users
                    (username, full_name, password_hash, salt, role, department_id,
                     is_active, must_change_password)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1)
                """,
                (username, full_name, pw_hash, salt, role, dept_id),
            )
            seeded_creds[username] = raw_pw
            if generated:
                _logger.info("Seeded initial account '%s' with generated one-time password.", username)

        if any(not get_env_str(spec[4], "") and not fallback_env for spec in _DEMO_ACCOUNT_SPECS):
            print("[FIRST-RUN SEED] Generated one-time passwords (password change forced on first login):")
            for uname, pw in seeded_creds.items():
                print(f"  {uname}: {pw}")

        return seeded_creds


def seed_departments() -> bool:
    """Ensure default departments and SLA policies exist when departments table is empty."""
    with closing(get_connection()) as conn, conn:
        count = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if count > 0:
            return False
        _seed_departments_in_conn(conn)
        _seed_sla_policies_in_conn(conn)
        return True


def has_pending_seed_password_reset() -> bool:
    """Return True if any seeded staff account still has must_change_password = 1."""
    usernames = [spec[0] for spec in _DEMO_ACCOUNT_SPECS]
    placeholders = ",".join("?" for _ in usernames)
    with closing(get_connection()) as conn:
        count = conn.execute(
            f"SELECT COUNT(*) FROM users WHERE username IN ({placeholders}) AND must_change_password = 1",
            tuple(usernames),
        ).fetchone()[0]
    return count > 0


# ---------------------------------------------------------------------------
# Auto-init on import
# ---------------------------------------------------------------------------
init_db()
seed_demo_accounts()
