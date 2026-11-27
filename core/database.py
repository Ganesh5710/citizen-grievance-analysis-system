"""
Database bootstrap: connection factory, schema init, migrations, and seeding.
Importing this module initializes the SQLite database.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
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
CREATE TABLE IF NOT EXISTS departments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT UNIQUE NOT NULL,
    full_name            TEXT NOT NULL,
    password_hash        TEXT NOT NULL,
    salt                 TEXT NOT NULL,
    role                 TEXT NOT NULL CHECK(role IN ('user','employee','admin','super_admin')),
    department_id        INTEGER REFERENCES departments(id),
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sla_policies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    priority      TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
    target_hours  INTEGER NOT NULL CHECK(target_hours > 0),
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(department_id, priority)
);

CREATE TABLE IF NOT EXISTS complaints (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id      TEXT UNIQUE NOT NULL,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    text             TEXT NOT NULL,
    location         TEXT NOT NULL DEFAULT '',
    department_id    INTEGER NOT NULL REFERENCES departments(id),
    priority         TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
    status           TEXT NOT NULL DEFAULT 'Pending'
                         CHECK(status IN ('Pending','Assigned','In Progress','Resolved','Reopened','Closed')),
    assigned_to      INTEGER REFERENCES users(id),
    assigned_at      TEXT,
    due_at           TEXT,
    escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level IN (0, 1, 2)),
    resolved_at      TEXT,
    reopen_count     INTEGER NOT NULL DEFAULT 0,
    is_deleted       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
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
"""

_DEFAULT_DEPARTMENTS = [
    ("Water",           "Water supply, pipelines, leakage, and tap connections"),
    ("Roads",           "Road damage, potholes, pavements, and resurfacing"),
    ("Electricity",     "Power outages, transformers, streetlights, and exposed wires"),
    ("Sanitation",      "Garbage collection, drainage overflow, sewage, and waste"),
    ("Traffic",         "Traffic signals, road junctions, signage, and congestion"),
    ("General / Other", "Civic grievances not covered by specialized departments"),
]

DEFAULT_SLA_HOURS: dict[str, int] = {
    "High":   8,
    "Medium": 48,
    "Low":    120,
}

DEFAULT_DEMO_PASSWORD = "Admin@123"

_DEMO_ACCOUNTS = [
    ("superadmin", "Super Admin",  "super_admin", DEFAULT_DEMO_PASSWORD, None),
    ("admin",      "Admin User",   "admin",       DEFAULT_DEMO_PASSWORD, None),
    ("employee",   "Employee One", "employee",    DEFAULT_DEMO_PASSWORD, "Water"),
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
    return (row["sql"] if row and row["sql"] else "")


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
    if "department_id" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN department_id INTEGER REFERENCES departments(id)")
    if "is_active" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    if "must_change_password" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
        )
    # Assign seeded demo employee to Water if still unassigned
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
    needs_rebuild = "employee_id  INTEGER NOT NULL" in ddl or "employee_id INTEGER NOT NULL" in ddl or "actor_label" not in cols
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
                r["id"],
                r["complaint_id"],
                r["employee_id"],
                actor,
                field_name,
                old_status,
                new_status,
                old_val,
                new_val,
                src,
                r["remark"] or "",
                r["created_at"],
            ),
        )

    conn.execute("DROP TABLE complaint_updates")
    conn.execute("ALTER TABLE complaint_updates_new RENAME TO complaint_updates")


def _migrate_complaints(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "complaints")
    if not cols:
        return

    # Week 1-4 legacy table (no incident_id column)
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
        "department_id", "assigned_to", "assigned_at", "due_at",
        "escalation_level", "resolved_at", "reopen_count", "is_deleted",
    }
    needs_rebuild = (
        not required_cols.issubset(cols)
        or "'Reopened'" not in ddl
        or "'Closed'" not in ddl
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
            priority         TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
            status           TEXT NOT NULL DEFAULT 'Pending'
                                 CHECK(status IN ('Pending','Assigned','In Progress','Resolved','Reopened','Closed')),
            assigned_to      INTEGER REFERENCES users(id),
            assigned_at      TEXT,
            due_at           TEXT,
            escalation_level INTEGER NOT NULL DEFAULT 0 CHECK(escalation_level IN (0, 1, 2)),
            resolved_at      TEXT,
            reopen_count     INTEGER NOT NULL DEFAULT 0,
            is_deleted       INTEGER NOT NULL DEFAULT 0,
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
                (id, incident_id, user_id, text, location, department_id, priority, status,
                 assigned_to, assigned_at, due_at, escalation_level, resolved_at, reopen_count,
                 is_deleted, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["id"],
                r["incident_id"],
                r["user_id"],
                r["text"],
                r["location"] or "",
                mapped_dept_id,
                prio,
                r["status"],
                assigned_to,
                assigned_at,
                due_at,
                esc_level,
                resolved_at,
                reopen_count,
                is_del,
                r["created_at"],
                r["updated_at"],
            ),
        )

    conn.execute("DROP TABLE complaints")
    conn.execute("ALTER TABLE complaints_new RENAME TO complaints")


def init_db() -> None:
    """Create all tables and run schema migrations idempotently."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
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
            _seed_sla_policies_in_conn(conn)
            _migrate_users(conn)
            _migrate_complaints(conn)
            _migrate_complaint_updates(conn)
            conn.executescript(_SCHEMA_SQL)
            _seed_departments_in_conn(conn)
            _seed_sla_policies_in_conn(conn)
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def seed_demo_accounts() -> bool:
    """
    Insert demo accounts only when the users table is empty.
    Returns True if seeding happened, False if skipped.
    """
    with closing(get_connection()) as conn, conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return False

        dept_rows = conn.execute("SELECT id, name FROM departments").fetchall()
        dept_map = {r["name"]: r["id"] for r in dept_rows}

        for username, full_name, role, password, dept_name in _DEMO_ACCOUNTS:
            pw_hash, salt = hash_password(password)
            dept_id = dept_map.get(dept_name) if dept_name else None
            conn.execute(
                "INSERT INTO users "
                "(username, full_name, password_hash, salt, role, department_id, is_active, must_change_password) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, 0)",
                (username, full_name, pw_hash, salt, role, dept_id),
            )
        return True


def seed_departments() -> bool:
    """Ensure default departments and SLA policies exist when departments table is empty."""
    with closing(get_connection()) as conn, conn:
        count = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if count > 0:
            return False
        _seed_departments_in_conn(conn)
        _seed_sla_policies_in_conn(conn)
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
