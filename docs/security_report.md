# Security Assessment & Verification Report

**Project:** Citizen Grievance System  
**Automated Security Suite:** [`tests/test_security.py`](../tests/test_security.py) (10/10 security tests passing)

## 1. Automated Security Verification Matrix

| # | Attack / Threat Vector | Implemented Controls | Test Evidence (`tests/test_security.py`) | Status |
|---|---|---|---|---|
| 1 | SQL Injection in text, search filters & login | 100% parameterized queries (`?` bound params in SQLite, `:pN` bound params in `PostgresConnectionAdapter`) | `test_01_sql_injection_attempts_stay_inert` | Pass |
| 2 | Direct Role / Privilege Escalation | Service-layer `require(actor, perm)` on every core mutation, review action, transfer, export & user op | `test_02_direct_role_bypass_blocked` | Pass |
| 3 | Insecure Direct Object Reference (IDOR) | Ownership checks on complaints, history, attachments, internal notes, withdrawals, DPDP exports | `test_03_idor_isolation_between_citizens` | Pass |
| 4 | Cross-District Admin Access | `admin_jurisdictions` filtering + `_assert_admin_jurisdiction()` across view, assign, merge, transfer, export | `test_04_cross_district_admin_isolation` | Pass |
| 5 | Employee Field & Queue Tampering | Employees restricted to own assigned open tickets; blocked from dept/priority edits; `allow_self_claim=0` default | `test_05_employee_tampering_and_self_claim_disabled` | Pass |
| 6 | Malicious File Uploads | Extension/SVG/XML/HTML sniff rejection, Pillow `verify()` + raster re-encode stripping EXIF/GPS, 5 MB cap | `test_06_malicious_file_uploads_rejected` | Pass |
| 7 | Brute-Force Login & Session Reuse | PBKDF2-HMAC-SHA256 (`120k` iters), per-account failed-attempt lockout window, 30-min inactivity expiry | `test_07_brute_force_lockout_and_session_timeout` | Pass |
| 8 | Public Ticket Lookup Enumeration / Abuse | Single generic failure message, strict non-PII response projection, hourly rate-limit persisted even on failures | `test_08_public_status_tampering_and_rate_limit` | Pass |
| 9 | Stored XSS & Formula Injection | `html.escape()` across card/timeline HTML fragments; leading `=,+,-,@` neutralized with `'` in CSV/XLSX | `test_09_xss_escaping_and_formula_injection_neutralized` | Pass |
| 10 | Password Reset OTP Abuse | Cryptographic salted hash in `password_reset_codes`, 15-min TTL, single-use invalidation, hourly rate limit | `test_10_otp_reset_reuse_expiry_and_database_abstraction_health` | Pass |

## 2. Database Engine Abstraction (`DATABASE_URL`) Notes & Honest Gaps

`core/database.py` inspects `DATABASE_URL` (`get_database_backend()`):
- Default / `sqlite:///...` runs native SQLite (`WAL` mode, `busy_timeout=5000`, foreign keys enforced).
- `postgresql://...` / `postgres://...` routes queries through `PostgresConnectionAdapter` using SQLAlchemy Core (`translate_sql_placeholders()` converting `?` positional bind markers into `:p0, :p1, ...`, plus `translate_sqlite_ddl_for_postgres()`).

**Queries & Components Requiring PostgreSQL-Specific Adjustments on Live PostgreSQL:**
1. **Online Backup (`scripts/backup.py` / `scripts/restore.py`)**: Uses SQLite's `sqlite3.Connection.backup()` API; on PostgreSQL deployments, replace with `pg_dump` / `pg_restore`.
2. **Schema Inspection & DDL (`core/database.py` `_table_columns` & migrations)**: Startup migrations query SQLite `PRAGMA table_info(...)` and `sqlite_master`; on PostgreSQL these should use `information_schema.columns` / Alembic.
3. **Date Arithmetic (`substr(created_at, 1, 10)` vs SQL `DATE`)**: Standard string-stored ISO-8601 UTC timestamps keep query parity across both backends, though no live PostgreSQL server process was started during local verification on Windows (the adapter's URL detection, placeholder translation, and DDL translation are unit-tested cleanly).

## 3. Operational Hardening & Secrets Hygiene
- Secrets and credentials are never hardcoded; `.env.example` documents all runtime keys (`CGS_DB_PATH`, `DATABASE_URL`, `SMTP_*`, `CGS_BACKUP_KEY`).
- Structured rotating file logs (`core/logging_setup.py`, `RotatingFileHandler`, 2 MB x 3) record server events without plaintext passwords or reset OTPs.
- Container packaging ([`Dockerfile`](../Dockerfile), [`docker-compose.yml`](../docker-compose.yml)) runs Streamlit under non-root UID `10001` with a built-in Python `HEALTHCHECK` against `core.health.get_health_status()`.
