# Citizen Grievance Analysis System

[![CI](https://github.com/Ganesh5710/citizen-grievance-analysis-system/actions/workflows/ci.yml/badge.svg)](https://github.com/Ganesh5710/citizen-grievance-analysis-system/actions/workflows/ci.yml)

## Live Project Link

https://smart-governance.streamlit.app/

## Project Overview

An **AI-assisted, role-based civic grievance portal** built with Python, Streamlit, SQLite (`WAL`), optional PostgreSQL (`DATABASE_URL`), Plotly Express, scikit-learn, statsmodels, and Pillow. Citizens submit localized grievances (Andhra Pradesh pincode/hierarchy), view duplicate clusters within 300 m, track status publicly via QR code, and confirm/reopen resolutions; field employees accept assigned tasks and upload after-photos; jurisdiction-scoped District Admins assign officers, review ML/moderation flags, merge clusters, inspect demand forecasts/anomalies, and export CSV/Excel/PDF reports; Super Admins oversee Admin KPIs, manage locations, SLAs, YAML duration rules, ML promotion, backups, and spam blocks.

---

## Quick Start & Configuration (`.env` / Docker)

```bash
cd citizen_grievance_project
cp .env.example .env   # Configure optional SMTP, CGS_BACKUP_KEY, DATABASE_URL, etc.
pip install -r requirements.txt
streamlit run app.py
```

### Docker Compose Deployment

```bash
docker compose up --build                 # SQLite persistent volumes on port 8501
docker compose --profile postgres up -d   # Optional PostgreSQL 16 service
```

### Seeded Staff Accounts & First-Run Security

On an empty database, `core/database.py` creates initial `superadmin`, `admin`, and `employee` (`Water`) accounts using passwords from `.env` (`CGS_SEED_SUPERADMIN_PASSWORD`, `CGS_SEED_ADMIN_PASSWORD`, `CGS_SEED_EMPLOYEE_PASSWORD`, or `CGS_SEED_PASSWORD`). When unset, unique cryptographic one-time passwords are generated and printed once at startup, with mandatory password rotation (`must_change_password = 1`) before dashboard access.

Public citizens self-register via the **Sign Up** tab (creates a `user` account only).

---

## Project Structure

```
citizen_grievance_project/
├── .github/workflows/ci.yml    <- GitHub Actions: Ruff lint, compileall & full unittest suite
├── .streamlit/config.toml        <- Native Light / Dark / System theme config
├── .env.example                  <- Environment variable template (SMTP, ML, rate limits, DB, backups)
├── Dockerfile & docker-compose.yml <- Non-root container + optional PostgreSQL profile
├── pyproject.toml                <- Ruff configuration
├── app.py                        <- Entry point, ?health=1, ?ticket= QR route, auth, DPDP notice, router
│
├── core/
│   ├── permissions.py            <- Single-source PERMISSIONS table, can(), require()
│   ├── security.py               <- PBKDF2-HMAC-SHA256 hashing, temp passwords, text sanitization
│   ├── database.py               <- SQLite WAL + PostgreSQL DATABASE_URL adapter, indexes, migrations
│   ├── health.py                 <- DB/ML/uploads health check service
│   ├── logging_setup.py          <- RotatingFileHandler structured logs (logs/app.log)
│   ├── departments.py            <- Active/all department lookup and Super Admin CRUD
│   ├── locations.py              <- India Post CSV loader, pincode lookup, AP hierarchy & self-claim flag
│   ├── auth.py                   <- Register, login lockout, OTP reset, jurisdiction & admin lifecycle
│   ├── clusters.py               <- 300m duplicate detection (MiniLM / TF-IDF), support & merge/unmerge
│   ├── moderation.py             <- Gibberish, repeat & EN/HI/TE abuse screening, cooldown/limits, CAPTCHA
│   ├── privacy.py                <- DPDP Act 2023 notice, personal JSON/CSV export & account anonymization
│   ├── public_status.py          <- Rate-limited non-PII public ticket lookup & QR PNG builder
│   ├── reports.py                <- Jurisdiction-scoped CSV, OpenXML XLSX, PDF, Audit CSV & weekly digest
│   ├── images.py                 <- Pillow validation, EXIF/GPS stripping, initial/after stage RBAC
│   ├── sla.py                    <- SLA policies, due_at computation, escalations, 7d auto-close, aging
│   ├── complaints.py             <- Intake, assign_task, accept_task, withdraw, transfer, internal notes
│   ├── oversight.py              <- Super Admin metrics, >4h unassigned alerts, filtered audit log
│   ├── analytics_geo.py          <- District/mandal/pincode filters, hotspot map & <5 public suppression
│   ├── performance.py            <- Department & employee SLA KPI metrics
│   ├── priority_rules.py         <- Configurable YAML duration rules & audit log
│   └── ml_service.py             <- Model retraining promotion & Admin review queue resolution
│
├── ml/ & nlp/ & analytics/ & notifications/
│   ├── nlp/duration.py           <- Deterministic EN/HI/TE duration & recurrence extractor
│   ├── ml/classifier.py & trainer.py <- TF-IDF + Duration LinearSVC, LIME, stratified group CV & ablation
│   ├── analytics/forecast.py     <- 4-week rolling-origin SARIMA vs Seasonal-Naive demand forecast
│   ├── analytics/resolution_predictor.py <- HistGradientBoosting resolution-time estimator vs mean baseline
│   ├── analytics/anomaly.py      <- Rolling 2h Z-score + IsolationForest spike alerts
│   └── notifications/service.py  <- In-app inbox + verified-email 3-retry SMTP queue
│
├── scripts/
│   ├── load_locations.py         <- Idempotent official India Post CSV loader
│   ├── train.py / evaluate_priority.py / evaluate_phase2.py <- Offline ML & forecasting benchmarks
│   ├── inject_spike.py           <- Controlled demo spike generator & cleanup
│   ├── run_escalations.py        <- Cron SLA escalation, aging & auto-close worker
│   ├── weekly_admin_report.py    <- Cron zero-PII weekly jurisdiction digest emailer
│   ├── backup.py & restore.py    <- Online SQLite backup (PBKDF2+HMAC encrypted) & verified restore
│   └── benchmark_10k.py / load_test.py <- 10,500-row latency benchmark & Locust HTTP load script
│
├── docs/
│   ├── diagrams.md               <- Mermaid ER, Architecture, DFD 0/1, Use Case & Sequence diagrams
│   ├── evaluation_report.md      <- Department, Priority, Ablation & Phase 2 Forecasting metrics
│   ├── security_report.md        <- 10-category security test evidence & DATABASE_URL notes
│   ├── performance_report.md     <- 10,500-row SQLite timings, indexes & Locust readiness
│   ├── test_cases.md             <- Complete catalog of automated unit & security tests
│   ├── demo_script.md            <- 5-minute end-to-end panel walkthrough
│   ├── sus_questionnaire.md      <- System Usability Scale (SUS) evaluation form
│   └── limitations_and_future_work.md <- Honest appraisal & roadmap
│
└── tests/                        <- 64 automated unit, ML, workflow, privacy, backup & security tests
```

---

## Roles & Permissions

| Permission | user | employee | admin | super_admin | Scope / Rules |
|---|---|---|---|---|---|
| `submit_complaint` | Yes | No | No | No | Citizen picks active department + valid district/location; enters `Awaiting Assignment` with suggested officer |
| `view_own` | Yes | - | - | - | Citizens see only their own complaints |
| `add_images` | Yes | No | No | No | Own open complaint only (not Resolved/Closed); max 3 photos |
| `submit_feedback` | Yes | No | No | No | Own Resolved complaint only, within 7 days, once per cycle |
| `view_all` | No | Yes | Yes | Yes | Employee sees own department only; Admin/Super Admin see all |
| `assign_task` / `assign_complaint` | No | No | Yes | Yes | Assign/reassign with due date & instructions to matching dept + jurisdiction employee |
| `transfer_complaint` | No | No | Yes | Yes | Transfer complaint between departments (mandatory reason, resets SLA/assignment) & review post-assignment withdrawals |
| `add_internal_note` | No | Yes | Yes | Yes | Confidential staff-only notes hidden from citizens |
| `merge_complaints` | No | No | Yes | Yes | Merge/unmerge duplicate complaints into incident clusters within own jurisdiction (reason required, audit logged) |
| `accept_task` | No | Yes | No | No | Employee explicitly accepts an assigned task and logs acceptance |
| `update_task_status` / `update_status_remark` | No | Yes | Yes | Yes | Employee only on tasks assigned to them; Admin status override requires mandatory reason |
| `claim_complaint` | No | Yes | No | No | Disabled by default; works only when Admin enables `allow_self_claim` for that district & employee matches dept + district |
| `edit_complaint_fields` | No | No | Yes | Yes | Change department and priority (recalculates SLA); employees blocked (`PermissionError`) |
| `remove_image` | No | No | Yes | Yes | Soft delete with confirmation + audit row |
| `delete_complaint` | No | No | No | Yes | Soft delete with confirmation + audit row |
| `manage_users` | No | No | No | Yes | Create any role, deactivate/reactivate, change role, reset password |
| `manage_admins` | No | No | No | Yes | Create Admins, set/update district & department jurisdiction, hand off districts |
| `view_admin_oversight` | No | No | No | Yes | View Admin KPIs, >4h unassigned escalations, and filtered Admin audit logs |
| `manage_locations` | No | No | No | Yes | Inspect location stats and reload official India Post CSV mappings |
| `manage_departments` | No | No | No | Yes | Add, rename, deactivate, reactivate (never hard-deleted) |
| `manage_sla` | No | No | No | Yes | Configure target resolution hours per department & priority |
| `manage_priority_rules` | No | No | No | Yes | Edit `config/priority_rules.yaml` thresholds & aging (audit-logged) |
| `manage_ml` | No | No | No | Yes | Train candidate models (never auto-activated) and manually promote |
| `view_review_queue` | No | No | Yes | Yes | Inspect ML low-confidence, mismatch, and low-duration-confidence items |
| `resolve_review_item` | No | No | Yes | Yes | Accept ML/citizen department and record priority feedback |
| `export_reports` | No | No | Yes | Yes | Export filtered CSV, Excel (`.xlsx`), PDF reports & audit CSV within jurisdiction |
| `view_analytics` | No | Yes (own scope) | Yes | Yes | View complaint analytics, location filters, and hotspot maps |
| `support_complaint` | Yes | No | No | No | Citizens can support open local issues |
| `withdraw_complaint` | Yes | No | No | No | Citizens can withdraw pre-assignment or request withdrawal post-assignment |
| `export_own_data` | Yes | No | No | No | Citizens export personal data (JSON/CSV) & request account anonymization (DPDP 2023) |
| `view_public_dashboard` | Public | Public | Public | Public | Anonymized district transparency metrics & public ticket status lookup (rate-limited, zero PII) |

> All permissions and scopes are enforced in the service layer (`core/`), not only in the UI.

---

## Duration-Aware Priority Engine (`config/priority_rules.yaml`, `nlp/duration.py`)

### 1. Default Duration Thresholds & Essential Service Overrides

Configured in `config/priority_rules.yaml` and editable live by Super Admin in the **Priority Rules** tab (every change writes to `priority_rule_audit`):

| Scope | Low | Medium | High |
|---|---|---|---|
| **Default** | `< 24 hours` | `24 hours up to < 7 days (168h)` | `>= 7 days (168h)` |
| **Water & Electricity Overrides** | `< 24 hours` | `>= 24 hours (1 day / since yesterday)` | `>= 5 days (120h)` |

- **Duration Extraction (`nlp/duration.py`):** Deterministic multilingual extractor supporting English, Telugu, Hindi, and mixed transliterated text — including digits (ASCII, Devanagari `०-९`, Telugu `౦-౯`), number words up to `30+`, abbreviations/typos (`dys`, `wks`, `hrs`), ranges (`2 to 3 days`, `3-4 days` using upper bound), relative idioms (`since yesterday` = 24h, `since morning` = 8h, `since last night` = 12h, `since last week` = 168h, `from last month` = 720h, `for ages` = 168h low-confidence flagged for review), and context filtering to ignore irrelevant durations (`salary`, `ticket valid for`, `call me after`, `festival`, `"fixed X days ago but broke again"`).
- **Recurring Detection:** Patterns such as `"every week"`, `"twice a month"`, `"हर हफ्ते"`, `"ప్రతి రోజు"` set `is_recurring = True` and bump final priority by **+1 level** instead of converting to a single outage duration.
- **Open Priority Aging (`core/sla.py:run_priority_aging_check`):** Separate from text rules — unresolved complaints age upward automatically (`Low -> Medium` after **3 days**, `Medium -> High` after **7 days**), writing `actor = "system"` rows to `complaint_updates` and never lowering priority.

### 2. Final Priority Rule (Deterministic + ML Hybrid Precedence)

```text
final_priority = highest_priority_of([
  1. Emergency keyword override (fire, explosion, electrocution, flood, injury, death, gas leak, live wire) -> High
  2. Duration-based priority from config/priority_rules.yaml
  3. ML model priority (word + char TF-IDF + extracted duration/recurrence features)
  4. Keyword priority (core/classifier.py)
])
then apply:
  5. Recurring pattern escalation (+1 level if recurring, capped at High)
```

> **Safety Guarantee:** No ML model prediction can ever lower a priority established by Emergency Keywords (Rule 1) or Duration Thresholds (Rule 2). Staff see the extracted duration, confidence, and `priority_reason` directly on each ticket; low-confidence duration extractions (`< 0.55`) enter `review_queue`, and unparsed time cues log to `duration_extraction_logs`.

### 3. Design Assumptions

- **Water & Electricity 24h Override Harmony:** Because essential-service overrides are meant to escalate *faster* than defaults and unit expectations require `"no water since yesterday"` (`24h`) to evaluate to `Medium` for Water, Water and Electricity keep `medium_min_hours: 24.0` while lowering `high_min_hours` to `120.0` (5 days vs 7-day default).
- **Month / Year Normalization:** `"1 month"` / `"from last month"` normalizes to `720.0` hours (`30 * 24`), and `"1 year"` normalizes to `8760.0` hours (`365 * 24`).
- **Offline Zero-Crash Policy:** Text classification uses local TF-IDF + Duration features (`ml/models/`) and falls back cleanly to keyword/duration rules when no trained model artifact is present.
- **Synthetic Training & Gold Set Isolation:** `data/synthetic_complaints.csv` contains 8,700 100% synthetic rows (`is_synthetic=True`) generated with `template_group` grouping (`StratifiedGroupKFold`); `tests/gold_priority_cases.csv` (339 hand-written cases) is strictly held out for unbiased evaluation (`docs/priority_evaluation.md`).

---

## Hierarchical Workflow & Jurisdiction (Andhra Pradesh Default)

### Workflow State Machine

```mermaid
flowchart LR
    S[Submitted] --> A[Awaiting Assignment]
    A -->|Admin / Super Admin assign_task| AS[Assigned]
    A -->|"> 4h Unassigned (Configurable)"| ESC[Super Admin Escalation Alert]
    AS -->|Employee accept_task / update| IP[In Progress]
    IP -->|Employee Closing Note >= 10 chars + After Photos| R[Resolved]
    R -->|Citizen Confirms or 7-Day Auto-Close| C[Closed]
    R -->|Citizen Rejects within 7 Days| RO[Reopened]
    RO -->|Admin Reassigns| AS
    AS -.->|Admin Deactivated| A
    A -.->|Admin Override with Mandatory Reason| IP
```

### Location Data Source & Structure (`DEFAULT_STATE=Andhra Pradesh`)

- **Official Source:** India Post All-India Pincode Directory from **data.gov.in** (Open Government Data Platform India, resource ID `5c2f62fe-5afa-4119-a499-fec9d604d5bd`), filtered to `Andhra Pradesh` and saved to `data/india_post_pincodes_ap.csv`.
- **Download Date:** `2026-11-28` (`10,681` rows covering `26` official Andhra Pradesh districts, `10,457` normalized post offices/areas, and `8,404` rows with latitude/longitude centroids; zero synthetic location records).
- **Mandal Level Handling:** Because the official `data.gov.in` India Post CSV does not include a `mandal` / `taluk` column, `scripts/load_locations.py` and `core/locations.py` cleanly skip populating `mandals` when that column is absent (logging `"Skipped mandal level: source CSV lacks mandal/taluk column"` in `location_load_logs`) while supporting `mandal` seamlessly whenever a regional CSV supplies one.
- **Citizen Location Capture:** Supports both **6-digit Pincode Lookup** (auto-fills State & District, lets citizen confirm the specific area) and **Cascading Dropdowns** (State -> District -> optional Mandal -> Area, auto-filling pincode), plus optional map pin/landmark. Every submitted complaint stores `state_id`, `district_id`, `mandal_id` (if applicable), `area_id`, and validated `pincode`, falling back to area centroid coordinates when no pin is dropped.

### Jurisdiction, Oversight & Assumptions

- **Super Admin (Top Oversight):** Unscoped nationwide visibility, **Admin Oversight** metrics (`awaiting_assignment`, `avg_time_to_assign`, district overdue count, reassignments, status overrides), automatic **Unassigned Escalation Alerts** after `CGS_UNASSIGNED_ESCALATION_HOURS` (default `4`), district handoff between Admins, Admin deactivation (returns open district complaints cleanly to `Awaiting Assignment` while preserving full history), and filtered Audit Logs by Admin/District.
- **Admin (District + Optional Department Manager):** Scoped strictly via `admin_jurisdictions`. District complaints enter `Awaiting Assignment` (no silent auto-assign) with a suggested least-loaded active employee in the matching department + jurisdiction. Admins can `assign_task` (with due date and instructions), edit department/priority, and override status ONLY with a mandatory audit-logged reason (`>= 5` chars).
- **Employee (Assigned Task Execution Only):** Scoped by department and optional `district_id` / `mandal_id`. District employees see only tasks assigned to them; they can click **Accept Task** (`accept_task`), add remarks and `"after"` resolution photos (`add_after_images`), and resolve with a closing note (`>= 10` chars). They cannot assign, reassign, or edit department/priority (`PermissionError`).
- **Public Transparency Privacy Rule:** The pre-login **Public Transparency** dashboard aggregates metrics by district only and suppresses any district group with fewer than **5** complaints, with zero personal details exposed.
- **Duplicate Detection & Primary Cluster Resolution Rule:** Before filing a new complaint, `core/clusters.py` checks open same-department issues within `CGS_DUPLICATE_RADIUS_M` (default `300` m) using multilingual sentence embeddings (`paraphrase-multilingual-MiniLM-L12-v2`) with automatic TF-IDF cosine fallback. Citizens can support an existing issue (`complaint_supports`, unique per `(complaint_id, user_id)`, blocked on own complaints) or continue submitting; Admins can merge/unmerge clusters within their jurisdiction (`merge_complaints`, audit-logged). When total cluster reports cross configurable thresholds (`3 -> Medium`, `10 -> High`), `system` raises open complaint priorities (never lowers). Resolving a cluster's `primary_complaint_id` automatically transitions remaining open cluster members to `Resolved` (`actor = "system"`) and notifies linked reporters and supporters.
- **Mandatory Jurisdiction Routing (Phase 0 Hardened):** Citizen `submit_complaint()` always requires a valid district/location and enters `Awaiting Assignment`, displaying only a suggested officer to the District Admin (no silent `Pending` bypass or silent auto-assignment). Legacy `allow_self_claim` remains disabled by default per district unless explicitly enabled by Super Admin.

---

## Citizen Submission Flow

1. **Step 1 — Select a Department:** Citizens choose from active departments (`Water`, `Roads`, `Electricity`, `Sanitation`, `Traffic`, `General / Other`, plus any departments added by Super Admin) rendered as a responsive card grid.
2. **Step 2 — Location & Complaint Details:** Citizens select location via **6-digit Pincode Lookup** or **Cascading AP Dropdowns** (State -> District -> Area), optionally drop a map pin/landmark, enter description (min 10 chars), and attach up to 3 initial EXIF-scrubbed photos.
3. **Hierarchical Intake & Tracking:** Priority (`High`, `Medium`, `Low`) and SLA `due_at` are computed; district complaints enter `Awaiting Assignment` for their District Admin to review and assign (`CIV-XXXXXXXX` ticket ID returned). Unassigned tickets older than 4 hours escalate to Super Admin.
4. **Resolution Confirmation & Reopen:** When an employee resolves a complaint (closing remark >= 10 chars + optional `"after"` photos), the citizen has 7 days under **My Complaints** to submit a 1–5 star rating and either **Confirm resolved** (`Closed`) or **Not resolved** (comment required -> `Reopened`, SLA timer resets, `reopen_count` increments).

---

## Image Upload Limits & Security

- **Allowed formats:** `JPG`, `JPEG`, `PNG`, `WEBP` (validated strictly by binary content with Pillow `verify()` + format inspection; renamed non-images and SVG/HTML payloads are rejected).
- **Limits:** Maximum **3 photos** per complaint and **5 MB** per file (enforced server-side). Citizens can attach photos on creation and add more later while status is not `Resolved` or `Closed`.
- **Privacy & Sanitization:** Photos are oriented via `exif_transpose`, downscaled to a max dimension of **1600 px**, and re-encoded from raw pixels to **strip all EXIF and GPS metadata**.
- **Storage & Access:** Stored under `uploads/<complaint_id>/<uuid>.<ext>` outside any static URL path, and rendered strictly by reading bytes in Python (`st.image(bytes)`) after passing `get_images()` scope checks.

---

## SLA, Escalations & Scheduled Runner

- **Default SLA Targets:** `High` = `8` hours, `Medium` = `48` hours, `Low` = `120` hours (configurable per department & priority in Super Admin -> **SLA Settings**).
- **Escalation Levels:**
  - `Level 1`: Complaint is open and past `due_at`.
  - `Level 2`: Complaint is open and more than 24 hours past `due_at`.
- **7-Day Auto-Close:** Resolved complaints with no citizen response after 7 days are automatically moved to `Closed`.
- **Automated Audit Actor:** Escalations and 7-day auto-closures are written to `complaint_updates` with `actor = "system"`.
- **Execution:** Runs automatically when staff dashboards load, and can be scheduled independently via `scripts/run_escalations.py`:

### Cron (Linux / macOS)

```cron
*/15 * * * * cd /path/to/citizen_grievance_project && /path/to/.venv/bin/python scripts/run_escalations.py >> escalations.log 2>&1
```

### Windows Task Scheduler

```powershell
# Trigger: Repeat every 15 minutes
# Action: Start a program
Program/script: C:\path\to\citizen_grievance_project\.venv\Scripts\python.exe
Arguments:      scripts\run_escalations.py
Start in:       C:\path\to\citizen_grievance_project
```

---

## Scheduled Maintenance, Training & Encrypted Backups

```bash
# 1. Load / refresh official Andhra Pradesh India Post pincode hierarchy
python scripts/load_locations.py

# 2. Train & evaluate offline ML department/priority models
python scripts/train.py --db complaints.db

# 3. Run SLA escalation, >4h unassigned alert, priority aging & 7-day auto-close
python scripts/run_escalations.py

# 4. Send weekly zero-PII jurisdiction summary emails to verified opted-in Admins
python scripts/weekly_admin_report.py --days 7

# 5. Online SQLite backup (PBKDF2+HMAC encrypted when CGS_BACKUP_KEY is set) & restore
python scripts/backup.py --keep 7
python scripts/restore.py --file backups/cgs_backup_YYYYMMDD_HHMMSS.db.enc --yes
```

---

## Running Tests & Linting

```bash
ruff check core nlp ml analytics notifications ui scripts tests app.py
python -m unittest discover -s tests -v
python scripts/benchmark_10k.py
```

---

## Technology Stack

- **Runtime & UI:** Python 3.12+, Streamlit (native Light / Dark / System theme), QR Code (`qrcode`)
- **Database:** SQLite (`WAL` mode + `PRAGMA foreign_keys = ON`) with unified `DATABASE_URL` SQLAlchemy adapter for PostgreSQL
- **AI / NLP / Forecasting:** scikit-learn (`LinearSVC_Calibrated`, `HistGradientBoostingRegressor`, `IsolationForest`), `statsmodels` (`SARIMA`), LIME, deterministic multilingual duration extractor (`nlp/duration.py`)
- **Visualization & Exports:** Pandas, Plotly Express, Folium (`streamlit-folium`), OpenXML `.xlsx` writer, `matplotlib` PDF export
- **Security:** PBKDF2-HMAC-SHA256 (`120k` rounds), Pillow binary image validation & EXIF/GPS stripping, DPDP Act 2023 export & erasure
- **DevOps & QA:** Docker, Docker Compose, GitHub Actions CI, Ruff, Locust, `unittest`

---

## Developed By

- Ganesh Bathula
- Freen shaik
- Ansha
