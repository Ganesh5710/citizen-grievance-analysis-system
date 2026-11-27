# Citizen Grievance Analysis System

## Live Project Link

https://smart-governance.streamlit.app/

## Project Overview

An **AI-assisted, role-based civic grievance portal** built with Python, Streamlit, SQLite, Plotly Express, and Pillow. Citizens select the target department, attach optional EXIF-scrubbed photo evidence, and track status/history; employees manage complaints assigned to them within their department; administrators oversee assignments, escalations, and SLA performance; and super administrators manage users, departments, and SLA policies.

---

## Quick Start

```bash
cd citizen_grievance_project
pip install -r requirements.txt
streamlit run app.py
```

### Seeded Demo Accounts (change default passwords before going live!)

| Role | Username | Password | Department |
|---|---|---|---|
| Super Admin | `superadmin` | `Admin@123` | - |
| Admin | `admin` | `Admin@123` | - |
| Employee | `employee` | `Admin@123` | `Water` |

Public citizens self-register via the **Sign Up** tab (creates a `user` account only).

---

## Project Structure

```
citizen_grievance_project/
├── .streamlit/
│   └── config.toml             <- Native Light / Dark / System theme config
├── app.py                      <- Entry point, auth, forced password reset, role router
├── complaints.db               <- SQLite database (auto-initialized & migrated)
├── uploads/                    <- Local attachment storage (gitignored; served via Python bytes)
├── requirements.txt
├── README.md
│
├── core/
│   ├── permissions.py          <- PERMISSIONS table, can(), require()
│   ├── security.py             <- PBKDF2-HMAC-SHA256 hashing, temp passwords, text sanitization
│   ├── database.py             <- SQLite schema, migrations, demo & SLA seeding
│   ├── departments.py          <- Active/all department lookup and Super Admin CRUD
│   ├── auth.py                 <- Register, login, password reset, user lifecycle
│   ├── images.py               <- Pillow validation, EXIF/GPS stripping, downscaling, RBAC
│   ├── sla.py                  <- SLA policies, due_at calculation, escalations, auto-close
│   ├── complaints.py           <- Submission, auto-assign, claim, reassign, updates, feedback
│   ├── performance.py          <- Per-department, per-employee, and employee self metrics
│   └── classifier.py           <- Auto-detects priority (High / Medium / Low) from text
│
├── scripts/
│   └── run_escalations.py      <- Standalone CLI runner for cron / Task Scheduler
│
├── ui/
│   ├── shared.py               <- Theme-aware CSS, shared staff dashboard, escalations, performance
│   ├── citizen.py              <- 2-step department picker, photo upload, My Complaints & feedback
│   ├── employee.py             <- Department-scoped My Queue / Unassigned & self-metrics
│   ├── admin.py                <- Full complaint management, Escalations, Performance
│   └── super_admin.py          <- Complaints, Escalations, Performance, Users, Departments, SLA
│
└── tests/
    └── test_permissions.py     <- End-to-end permission, image, SLA, escalation & migration tests
```

---

## Roles & Permissions

| Permission | user | employee | admin | super_admin | Scope / Rules |
|---|---|---|---|---|---|
| `submit_complaint` | Yes | No | No | No | Citizen picks active department; auto-assigns least-loaded employee |
| `view_own` | Yes | - | - | - | Citizens see only their own complaints |
| `add_images` | Yes | No | No | No | Own open complaint only (not Resolved/Closed); max 3 photos |
| `submit_feedback` | Yes | No | No | No | Own Resolved complaint only, within 7 days, once per cycle |
| `view_all` | No | Yes | Yes | Yes | Employee sees own department only; Admin/Super Admin see all |
| `update_status_remark` | No | Yes | Yes | Yes | Employee only on complaints assigned to them |
| `claim_complaint` | No | Yes | No | No | Unassigned complaints in employee's own department |
| `edit_complaint_fields` | No | No | Yes | Yes | Change department (auto-reassigns) and priority (recalculates SLA) |
| `assign_complaint` | No | No | Yes | Yes | Assign/reassign to active employee of the complaint's department |
| `remove_image` | No | No | Yes | Yes | Soft delete with confirmation + audit row |
| `delete_complaint` | No | No | No | Yes | Soft delete with confirmation + audit row |
| `manage_users` | No | No | No | Yes | Create any role, deactivate/reactivate, change role, reset password |
| `manage_departments` | No | No | No | Yes | Add, rename, deactivate, reactivate (never hard-deleted) |
| `manage_sla` | No | No | No | Yes | Configure target resolution hours per department & priority |

> All permissions and scopes are enforced in the service layer (`core/`), not only in the UI.

---

## Citizen Submission Flow

1. **Step 1 — Select a Department:** Citizens choose from active departments (`Water`, `Roads`, `Electricity`, `Sanitation`, `Traffic`, `General / Other`, plus any departments added by Super Admin) rendered as a responsive card grid.
2. **Step 2 — File Complaint:** Citizens enter the required description (minimum 10 characters), required location/landmark, and optional photo evidence (with live preview and a "Change department" option).
3. **Auto-Routing & Tracking:** Priority (`High`, `Medium`, `Low`) is auto-detected from text via `core/classifier.py`, SLA `due_at` is calculated from `sla_policies`, the least-loaded active employee in that department is auto-assigned (or left unassigned if none), and the citizen receives their `CIV-XXXXXXXX` ticket ID.
4. **Resolution Confirmation & Reopen:** When a complaint is marked `Resolved` (requires a closing remark of at least 10 characters), the citizen has 7 days under **My Complaints** to submit a 1–5 star rating and either **Confirm resolved** (`Closed`) or **Not resolved** (comment required -> `Reopened`, SLA timer resets, `reopen_count` increments).

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

## Running Tests

```bash
python -m unittest discover -s tests -v
# or with pytest if installed:
python -m pytest tests/ -v
```

---

## Technology Stack

- Python
- Streamlit (native Light / Dark / System theme)
- SQLite (`complaints.db` with `PRAGMA foreign_keys = ON`)
- Pandas & Plotly Express (`theme="streamlit"`)
- Pillow (image verification & EXIF/GPS stripping)
- PBKDF2-HMAC-SHA256 (`hashlib` + `hmac`)

---

## Developed By

- Ganesh Bathula
- Freen shaik
- Ansha
