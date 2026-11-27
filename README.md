# 🏛️ Citizen Grievance Analysis System — v2.0

## Live Project Link

https://smart-governance.streamlit.app/

## Project Overview

An **AI-powered, role-based civic complaint portal** built with Python, Streamlit, and SQLite. Citizens submit complaints and track their status, complaints are auto-classified by department and priority, staff view complaints from a persistent database, and only authorized employees can update complaint statuses and add remarks.

---

## 🚀 Quick Start

```bash
cd citizen_grievance_project
pip install -r requirements.txt
streamlit run app.py
```

### Demo Accounts (change default passwords before going live!)

| Role | Username | Password |
|---|---|---|
| Super Admin | `superadmin` | `Admin@123` |
| Admin | `admin` | `Admin@123` |
| Employee | `employee` | `Admin@123` |

Public citizens can self-register on the login screen (creates a `user` role automatically).

---

## 🗂️ Project Structure

```
citizen_grievance_project/
├── app.py                      ← Entry point, auth & routing by role
├── complaints.db               ← SQLite database (auto-created on startup)
├── requirements.txt
├── README.md
│
├── core/
│   ├── permissions.py          ← PERMISSIONS dict, can(), require()
│   ├── security.py             ← PBKDF2-HMAC-SHA256 hashing & constant-time compare
│   ├── database.py             ← SQLite connection, schema init, demo seed
│   ├── auth.py                 ← register, login, create_staff_account, get_all_users
│   ├── complaints.py           ← submit, get, detail, history, update_complaint
│   └── classifier.py           ← classify(text) → (department, priority)
│
├── ui/
│   ├── shared.py               ← Shared CSS theme, badges, history & chart helpers
│   ├── citizen.py              ← Submit Complaint + My Complaints
│   ├── employee.py             ← Full dashboard + search/filters + edit controls
│   ├── staff_readonly.py       ← Read-only dashboard (admin)
│   └── super_admin.py          ← Read-only dashboard + User Management
│
├── tests/
│   └── test_permissions.py     ← Acceptance & persistence test suite
│
├── data/
│   └── complaints.csv          ← Original dataset (Week 1)
├── model/
│   ├── preprocessing.py        ← NLP text cleaner (NLTK)
│   ├── complaint_classifier.pkl
│   ├── vectorizer.pkl
│   └── label_encoder.pkl
└── eda_files/
    ├── week1_eda.ipynb
    └── week2_model.ipynb
```

---

## 🔐 Roles & Permissions

| Action | user | employee | admin | super_admin |
|---|---|---|---|---|
| Sign up / Login | ✅ | ✅ | ✅ | ✅ |
| Submit complaint | ✅ | ❌ | ❌ | ❌ |
| View own complaints + status | ✅ | — | — | — |
| View ALL complaints | ❌ | ✅ | ✅ | ✅ |
| Update status / add remarks | ❌ | ✅ | ❌ | ❌ |
| Create staff accounts | ❌ | ❌ | ❌ | ✅ |

> **Permissions are enforced at the data/service layer** (`require(user, permission)`) — not only in the UI. Any non-employee calling `update_complaint()` raises `PermissionError`.

---

## 🛡️ Security

- **Salted PBKDF2-HMAC-SHA256** password hashing (260,000 iterations, 32-byte random salt per user)
- **Constant-time comparison** via `hmac.compare_digest`
- **Parameterized SQL** everywhere — zero string-formatted queries
- **Input validation & HTML escaping**: username ≥ 3 chars (`[A-Za-z0-9_]`), password ≥ 8 chars, sanitized text inputs
- Login failure returns a **generic error** (never reveals whether username or password was wrong)
- `PRAGMA foreign_keys = ON` enforced on every connection
- Session-based login; logout clears session state

---

## 🤖 Classification

`core/classifier.py` → `classify(text)` returns `(department, priority)`:

- **Departments**: `Water`, `Roads`, `Electricity`, `Sanitation`, `Traffic`, `General`
- **Priority**: `High` (danger keywords), `Medium` (issue keywords), `Low` (default)

The classifier is **fully isolated** so the trained TF-IDF + Naive Bayes `.pkl` model can replace it later without modifying any other module.

---

## 🧪 Running Tests

```bash
python -m unittest discover -s tests -v
# or with pytest if installed:
python -m pytest tests/ -v
```

Tests verify all acceptance criteria:
1. A user cannot see another user's complaints.
2. A `user`, `admin`, and `super_admin` calling `update_complaint()` raises `PermissionError`.
3. An `employee` can update status, and a `complaint_updates` audit row is created.
4. Public sign-up cannot create a non-`user` role.
5. Only `super_admin` can call `create_staff_account()`.
6. A user cannot read the history of someone else's complaint.
7. Data persists across DB restarts/reloads.

---

## 📅 Development Timeline

| Week | Milestone |
|---|---|
| Week 1 | Dataset EDA, text cleaning, word frequency analysis |
| Week 2 | TF-IDF + Naive Bayes classifier |
| Week 3 | Streamlit AI Command Center (keyword classification) |
| Week 4 | Final dashboard & analytics polish |
| **v2.0** | **Role-based portal, SQLite persistence, audit log, security hardening** |

---

## 🖥️ Technology Stack

- Python
- Streamlit
- SQLite (`complaints.db`)
- Pandas
- Plotly Express
- PBKDF2-HMAC-SHA256 (`hashlib` + `hmac`)

---

## 👥 Developed By

- Ganesh Bathula
- Freen shaik
- Ansha
