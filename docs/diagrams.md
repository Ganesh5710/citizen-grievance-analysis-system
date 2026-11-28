# System Architecture & Design Diagrams

## 1. System Architecture

```mermaid
flowchart TB
    Citizen["Citizen Browser / QR Scanner"] --> UI["Streamlit Multi-Role App (app.py + ui/)"]
    Staff["Employee / Admin / Super Admin"] --> UI
    Cron["Scheduled Tasks (Escalations, Weekly Digest, Backups)"] --> Services

    UI --> Services["Service Layer (core/ RBAC require() & Scope Checks)"]
    Services --> NLP["NLP Engine (nlp/duration.py + preprocessor)"]
    Services --> ML["ML Classifier (ml/classifier.py TF-IDF + LinearSVC + LIME)"]
    Services --> Analytics["Analytics (analytics/forecast.py, resolution_predictor.py, anomaly.py)"]
    Services --> Notif["Notifications Queue (notifications/service.py + SMTP)"]
    Services --> Storage["Uploads & Encrypted Backups (uploads/, backups/)"]
    Services --> DB["Database Abstraction (core/database.py SQLite WAL / PostgreSQL)"]
```

## 2. Entity-Relationship Diagram (Core Tables)

```mermaid
erDiagram
    USERS ||--o{ COMPLAINTS : files
    USERS ||--o{ ADMIN_JURISDICTIONS : manages
    DEPARTMENTS ||--o{ COMPLAINTS : categorizes
    DEPARTMENTS ||--o{ SLA_POLICIES : defines
    STATES ||--o{ DISTRICTS : contains
    DISTRICTS ||--o{ MANDALS : contains
    DISTRICTS ||--o{ AREAS : contains
    AREAS ||--o{ PINCODES : maps
    COMPLAINTS ||--o{ COMPLAINT_UPDATES : audits
    COMPLAINTS ||--o{ COMPLAINT_IMAGES : attaches
    COMPLAINTS ||--o{ COMPLAINT_FEEDBACK : collects
    COMPLAINTS ||--o{ COMPLAINT_NOTES : internal_notes
    COMPLAINTS ||--o{ COMPLAINT_SUPPORTS : supports
    INCIDENT_GROUPS ||--o{ COMPLAINTS : clusters
    USERS ||--o{ NOTIFICATIONS : receives
    COMPLAINTS ||--o{ REVIEW_QUEUE : flags

    USERS {
        int id PK
        string username UK
        string role
        int department_id FK
        int district_id FK
        int is_active
        string spam_blocked_until
        string anonymized_at
    }
    COMPLAINTS {
        int id PK
        string incident_id UK
        int user_id FK
        int department_id FK
        int district_id FK
        int area_id FK
        int incident_group_id FK
        string priority
        string status
        int assigned_to FK
        string due_at
        int escalation_level
        int is_spam
    }
    INCIDENT_GROUPS {
        int id PK
        int department_id FK
        int district_id FK
        int primary_complaint_id FK
        int member_count
        int support_count
        string status
    }
```

## 3. Data Flow Diagram — Level 0 (Context Diagram)

```mermaid
flowchart LR
    Citizen["Citizen"] -->|"Grievance, Location, Photos, Feedback"| System["0.0 Citizen Grievance Platform"]
    System -->|"Ticket ID, QR Code, Notifications, Status"| Citizen
    Public["Unauthenticated Public"] -->|"Ticket ID + Last 4 / District Query"| System
    System -->|"Sanitized Status / Suppressed Metrics (<5)"| Public
    Admin["District Admin"] -->|"Assign, Merge, Transfer, Override, Export"| System
    System -->|"Work Queues, Forecasts, Escalations, PDF/XLSX"| Admin
    Emp["Field Employee"] -->|"Accept Task, Remarks, Resolution Photos"| System
    System -->|"Assigned Queue & SLA Deadlines"| Emp
    SA["Super Admin"] -->|"Users, Jurisdictions, SLA, YAML Rules, ML Promote"| System
    System -->|"Admin Oversight, Alerts, Moderation Logs"| SA
```

## 4. Data Flow Diagram — Level 1

```mermaid
flowchart TD
    P1["1.0 Auth, Brute-Force & DPDP Self-Service"] --> D1[("users, reset_codes")]
    P2["2.0 Intake, Moderation & Duplicate Check"] --> D2[("complaints, complaint_supports, incident_groups")]
    P2 --> P3["3.0 Hybrid NLP + ML Classification & SLA Compute"]
    P3 --> D3[("sla_policies, review_queue")]
    P4["4.0 District Jurisdiction Routing & Workflow"] --> D2
    P4 --> D4[("complaint_updates, complaint_notes")]
    P5["5.0 Escalation, Aging & Notifications Worker"] --> D4
    P5 --> D5[("notifications")]
    P6["6.0 Analytics, Forecast, Public Lookup & Reports"] --> D2
```

## 5. Role & Use Case Overview

```mermaid
flowchart LR
    subgraph CitizenRole["Citizen (user)"]
        UC1["Submit Grievance (AP Pincode / Area + Photos)"]
        UC2["Support Nearby Cluster Issue"]
        UC3["Track via QR / Public Status"]
        UC4["Confirm Resolution or Reopen (<7d)"]
        UC5["Withdraw & DPDP Data Export / Erasure"]
    end
    subgraph EmployeeRole["Employee"]
        UC6["Accept Assigned Task"]
        UC7["Add Progress Remarks & Internal Notes"]
        UC8["Resolve with Closing Note + After Photos"]
    end
    subgraph AdminRole["Admin (Jurisdiction Scoped)"]
        UC9["Assign / Reassign Suggested Officer"]
        UC10["Merge / Unmerge Clusters & Transfer Dept"]
        UC11["Resolve ML / Moderation Review Queue"]
        UC12["Export CSV / XLSX / PDF Reports"]
    end
    subgraph SuperAdminRole["Super Admin"]
        UC13["Manage Staff, Jurisdictions & Deactivate Admins"]
        UC14["Configure SLA, YAML Priority Rules & Retrain ML"]
        UC15["Lift Spam Blocks & Inspect Global Audit"]
    end
```

## 6. Sequence Diagrams

### 6.1 Complaint Submission & Duplicate Detection
```mermaid
sequenceDiagram
    participant C as Citizen
    participant UI as Citizen UI
    participant CL as core/clusters.py
    participant SUB as core/complaints.py
    participant ML as ml/classifier.py
    participant DB as SQLite/Postgres

    C->>UI: Enter text, AP location & photos
    UI->>CL: find_similar_open_complaints(dept, text, coords)
    alt Similar complaints within 300m found
        CL-->>UI: Match list (similarity + distance)
        C->>UI: Choose Support Existing OR Continue Submitting
    end
    UI->>SUB: submit_complaint(user, dept, text, location)
    SUB->>ML: predict_complaint(text, dept)
    ML-->>SUB: ml_dept, confidence, duration_hours, priority
    SUB->>DB: Insert complaint (status=Awaiting Assignment) + audit row
    SUB-->>UI: CIV-XXXXXXXX + QR URL
```

### 6.2 Admin Assignment & Employee Lifecycle
```mermaid
sequenceDiagram
    participant A as District Admin
    participant S as core/complaints.py
    participant N as notifications/service.py
    participant E as Employee

    A->>S: assign_task(admin, complaint_id, emp_id, instructions)
    S->>S: require('assign_task') & check district/dept match
    S->>N: emit_complaint_event('complaint_assigned')
    E->>S: accept_task(emp, complaint_id)
    E->>S: update_complaint(emp, id, 'Resolved', closing_remark, after_photos)
    S->>S: Resolve sibling cluster members (if primary)
    S->>N: emit_complaint_event('complaint_resolved')
```

### 6.3 Scheduled Escalation & Priority Aging
```mermaid
sequenceDiagram
    participant CRON as scripts/run_escalations.py
    participant SLA as core/sla.py
    participant DB as SQLite/Postgres
    participant N as notifications/service.py

    CRON->>SLA: run_escalation_check() + run_priority_aging_check()
    SLA->>DB: Query open overdue or >4h unassigned tickets
    SLA->>DB: Update escalation_level / priority (actor='system')
    SLA->>N: Notify District Admin & Super Admin
```

### 6.4 Citizen Resolution Reopen Flow
```mermaid
sequenceDiagram
    participant C as Citizen
    participant SUB as core/complaints.py
    participant DB as SQLite/Postgres
    participant N as notifications/service.py

    C->>SUB: submit_feedback(citizen, id, accepted=False, rating, comment)
    SUB->>SUB: Verify ownership, Resolved status & <=7 day window
    SUB->>DB: Insert complaint_feedback; set status='Reopened', new due_at, reopen_count+1
    SUB->>N: Notify assigned Employee & District Admin
```
