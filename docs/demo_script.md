# 5-Minute Live Demonstration Script

**Total Runtime:** ~5 minutes  
**Objective:** Demonstrate security enforcement, Andhra Pradesh jurisdiction workflow, multilingual duration-aware AI triage, analytics, and citizen transparency end-to-end.

## Minute 0:00 – 0:45 | Public Entry, Transparency & Health Check
1. Open `http://localhost:8501/?health=1` briefly to show live database/ML/upload health verification, then return to `http://localhost:8501`.
2. Show the **Public Transparency** tab: district aggregation automatically suppresses districts with `< 5` complaints (`zero PII`).
3. Switch to **Sign Up** (note optional arithmetic CAPTCHA and Citizen-only role creation) and demonstrate **Track Ticket (Public Status)** requiring `Ticket ID + last 4 digits of contact`.

## Minute 0:45 – 2:00 | Citizen Intake, Duplicate Cluster & Multilingual AI Triage
1. Log in as a Citizen (`p_citizen`). Select **Water** department.
2. Enter AP pincode `522002` — district (`Guntur`) and areas auto-populate from the official `data.gov.in` India Post dataset.
3. Submit an issue such as *"Drinking water pipeline leaking outside school since 3 days"*:
   - Point out the immediate **Nearby Similar Complaints (300m radius)** duplicate check option to **Support Existing**.
   - Proceed with submission to show **detected Medium priority (`>= 24h` rule for Water)**, **estimated resolution time**, EXIF/GPS photo scrubbing notice, and generated **QR Code + Tracking Link**.
4. Also demonstrate uploading/typing a short Telugu phrase (`"గత వారం రోజులుగా నీరు రావడం లేదు"`) showing deterministic duration recognition (`1 week -> High`).

## Minute 2:00 – 3:00 | District Admin Workflow & Operational Analytics
1. Log in as a District Admin (`adm_guntur`). Open **Assigned Queue**:
   - Confirm complaints arrive as **Awaiting Assignment** (no silent auto-assign bypass) with a **Suggested Employee** chip showing current workload.
   - Assign the ticket to an active Water officer in Guntur.
2. Briefly inspect **ML & Moderation Review Queue** (showing low-confidence or suspected spam tickets), **Transfer Department**, and **Internal Staff Notes** (confidential from citizens).
3. Open **Forecast and Predictions** to show the 4-week volume forecast (`SARIMA` vs Seasonal-Naive) and rolling spike anomaly status, and trigger an **Excel (`.xlsx`) / PDF / Audit CSV** export from **Performance Metrics**.

## Minute 3:00 – 4:00 | Employee Execution & Citizen Reopen / Confirmation
1. Log in as the assigned Guntur Water officer (`emp_water`):
   - Note that unrelated district/department tickets and department/priority fields are inaccessible (`PermissionError` in service layer).
   - Click **Accept Task**, attach an `"after"` repair photo + closing remark (`>= 10` chars), and mark **Resolved**.
2. Switch back to Citizen (**My Complaints**) -> submit 5-star confirmation (**Confirm resolved** -> `Closed`), OR demonstrate **Not resolved** -> `Reopened` + recalculated SLA.

## Minute 4:00 – 5:00 | Super Admin Oversight, Rules & Engineering Evidence
1. Log in as **Super Admin** (`superadmin`).
2. Walk through **Admin Oversight KPIs** (unassigned >4h alerts, district handoff), **SLA Settings**, **Priority Rules** YAML editor (with live audit log), and **ML Model Management** (versioned candidate evaluation, never auto-promoted).
3. Summarize test verification (`python -m unittest discover -s tests -v` — 64 automated unit/security tests, `ruff check` passing, and `scripts/benchmark_10k.py` 10,500-row timings).
