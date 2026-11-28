"""
core/ml_service.py
Service-layer operations for Review Queue triage and ML Model version management.
Every operation enforces permissions via require() and records audit logs.
"""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from core.config import PROJECT_ROOT, get_env_str
from core.database import get_connection, utc_now_str
from core.logging_setup import get_logger
from core.permissions import require

logger = get_logger("core.ml_service")


def _insert_audit_in_conn(
    conn,
    complaint_id: int,
    actor_id: int | None,
    actor_label: str,
    field_name: str,
    old_value: str,
    new_value: str,
    source: str,
    remark: str = "",
    created_at: str | None = None,
) -> None:
    ts = created_at or utc_now_str()
    conn.execute(
        """
        INSERT INTO complaint_updates
            (complaint_id, employee_id, actor_label, field_name,
             old_status, new_status, old_value, new_value, source, remark, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            complaint_id,
            actor_id,
            actor_label,
            field_name,
            old_value if field_name == "status" else "",
            new_value if field_name == "status" else "",
            old_value,
            new_value,
            source,
            remark,
            ts,
        ),
    )


def get_review_queue(
    actor: dict | None,
    status_filter: str | None = "Pending",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List review queue items joined with complaint & department info."""
    require(actor, "view_review_queue")
    where = ["c.is_deleted = 0"]
    params: list[Any] = []
    if status_filter in ("Pending", "Resolved"):
        where.append("rq.status = ?")
        params.append(status_filter)

    sql = f"""
        SELECT rq.*,
               c.incident_id,
               c.text AS complaint_text,
               c.priority AS complaint_priority,
               c.status AS complaint_status,
               c.department_id AS current_department_id,
               d.name AS current_department_name,
               c.ml_top_terms,
               COALESCE(u.username, '') AS citizen_username,
               COALESCE(rb.username, '') AS resolved_by_username
        FROM review_queue rq
        JOIN complaints c ON c.id = rq.complaint_id
        JOIN departments d ON d.id = c.department_id
        LEFT JOIN users u ON u.id = c.user_id
        LEFT JOIN users rb ON rb.id = rq.resolved_by
        WHERE {' AND '.join(where)}
        ORDER BY CASE WHEN rq.status = 'Pending' THEN 0 ELSE 1 END, rq.created_at DESC, rq.id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([int(limit), int(offset)])
    with closing(get_connection()) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_pending_review_items(actor: dict | None) -> int:
    require(actor, "view_review_queue")
    with closing(get_connection()) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM review_queue rq
            JOIN complaints c ON c.id = rq.complaint_id
            WHERE rq.status = 'Pending' AND c.is_deleted = 0
            """
        ).fetchone()
    return int(row["cnt"]) if row else 0


def resolve_review_item(
    actor: dict | None,
    review_id: int,
    action: str,
    target_department_id: int | None = None,
    note: str = "",
) -> None:
    """
    Resolve a review queue item as Admin / Super Admin.
    Allowed actions:
      - 'Keep Citizen': keep current citizen department and mark reviewed
      - 'Accept ML': update complaint department to ML suggested department
      - 'Custom Department': update complaint department to target_department_id
      - 'Marked Valid': mark spam/photo alert valid without changing department
      - 'Confirmed Spam': mark review item confirmed spam (soft-notes audit log)
    """
    require(actor, "resolve_review_item")
    valid_actions = {"Keep Citizen", "Accept ML", "Custom Department", "Marked Valid", "Confirmed Spam"}
    if action not in valid_actions:
        raise ValueError(f"Unsupported review resolution action: {action}")

    now = utc_now_str()
    actor_label = f"{actor['username']} ({actor['role']})"

    with closing(get_connection()) as conn, conn:
        rq = conn.execute("SELECT * FROM review_queue WHERE id = ?", (int(review_id),)).fetchone()
        if rq is None:
            raise ValueError("Review item not found.")
        if rq["status"] == "Resolved":
            raise ValueError("Review item is already resolved.")

        comp = conn.execute(
            """
            SELECT c.*, d.name AS dept_name
            FROM complaints c
            JOIN departments d ON d.id = c.department_id
            WHERE c.id = ? AND c.is_deleted = 0
            """,
            (rq["complaint_id"],),
        ).fetchone()
        if comp is None:
            raise ValueError("Associated complaint not found.")

        new_dept_name = comp["dept_name"]
        new_dept_id = comp["department_id"]

        if action == "Accept ML":
            suggested = (rq["ml_suggested_department"] or "").strip()
            if not suggested:
                raise ValueError("No ML suggested department is available for this item.")
            target_dept = conn.execute(
                "SELECT id, name, is_active FROM departments WHERE lower(name) = lower(?)",
                (suggested,),
            ).fetchone()
            if target_dept is None:
                raise ValueError(f"Department '{suggested}' does not exist.")
            new_dept_id = target_dept["id"]
            new_dept_name = target_dept["name"]
        elif action == "Custom Department":
            if target_department_id is None:
                raise ValueError("Select a target department.")
            dept_info = conn.execute(
                "SELECT id, name, is_active FROM departments WHERE id = ?",
                (int(target_department_id),),
            ).fetchone()
            if dept_info is None:
                raise ValueError("Selected department does not exist.")
            new_dept_id = dept_info["id"]
            new_dept_name = dept_info["name"]

        if new_dept_id != comp["department_id"]:
            conn.execute(
                "UPDATE complaints SET department_id = ?, updated_at = ? WHERE id = ?",
                (new_dept_id, now, comp["id"]),
            )
            _insert_audit_in_conn(
                conn,
                comp["id"],
                actor["id"],
                actor_label,
                "department",
                comp["dept_name"],
                new_dept_name,
                "review_queue",
                remark=note.strip() or f"Review queue resolved: {action}",
                created_at=now,
            )
        else:
            _insert_audit_in_conn(
                conn,
                comp["id"],
                actor["id"],
                actor_label,
                "review_queue",
                rq["reason"],
                action,
                "review_queue",
                remark=note.strip() or f"Review queue resolved: {action}",
                created_at=now,
            )

        conn.execute(
            """
            UPDATE review_queue
            SET status = 'Resolved',
                resolution_action = ?,
                resolved_department = ?,
                resolved_by = ?,
                resolution_note = ?,
                resolved_at = ?
            WHERE id = ?
            """,
            (action, new_dept_name, actor["id"], note.strip(), now, int(review_id)),
        )


def sync_ml_models_registry() -> None:
    """Sync versioned metadata JSON files on disk into the ml_models table."""
    models_dir = Path(get_env_str("ML_MODELS_DIR", str(PROJECT_ROOT / "ml" / "models")))
    if not models_dir.exists():
        return

    pointer_path = models_dir / "active_model.json"
    active_version = None
    if pointer_path.exists():
        try:
            active_version = json.loads(pointer_path.read_text(encoding="utf-8")).get("version")
        except Exception:
            active_version = None

    with closing(get_connection()) as conn, conn:
        for meta_file in sorted(models_dir.glob("grievance_ml_*.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            version = str(meta.get("version", "")).strip()
            if not version:
                continue
            dept_info = meta.get("department", {})
            prio_info = meta.get("priority", {})
            dept_metrics = dept_info.get("best_metrics", {})
            prio_metrics = prio_info.get("best_metrics", {})
            is_act = 1 if active_version and version == active_version else 0
            conn.execute(
                """
                INSERT INTO ml_models
                    (version, artifact_path, metadata_path, dept_model_name,
                     dept_test_f1, priority_model_name, priority_test_f1,
                     training_rows, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version) DO UPDATE SET
                    artifact_path = excluded.artifact_path,
                    metadata_path = excluded.metadata_path,
                    dept_model_name = excluded.dept_model_name,
                    dept_test_f1 = excluded.dept_test_f1,
                    priority_model_name = excluded.priority_model_name,
                    priority_test_f1 = excluded.priority_test_f1,
                    training_rows = excluded.training_rows,
                    is_active = CASE WHEN ? = 1 THEN 1 ELSE 0 END
                """,
                (
                    version,
                    str(meta.get("artifact_path", "")),
                    str(meta.get("metadata_path", str(meta_file))),
                    str(dept_info.get("best_model", "")),
                    dept_metrics.get("test_macro_f1"),
                    str(prio_info.get("best_model", "")),
                    prio_metrics.get("test_macro_f1"),
                    int(meta.get("dataset_rows", 0)),
                    is_act,
                    str(meta.get("trained_at", utc_now_str())).replace(" UTC", ""),
                    is_act,
                ),
            )


def list_ml_models(actor: dict | None) -> list[dict]:
    require(actor, "manage_ml")
    sync_ml_models_registry()
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT m.*, COALESCE(u.username, 'CLI') AS created_by_username
            FROM ml_models m
            LEFT JOIN users u ON u.id = m.created_by
            ORDER BY m.created_at DESC, m.id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def retrain_ml_models(actor: dict | None) -> dict[str, Any]:
    """
    Retrain ML models and save a new candidate version without auto-activating it.
    Only super_admin can invoke this.
    """
    require(actor, "manage_ml")
    from core.database import get_db_path
    from ml.trainer import train_and_evaluate

    meta = train_and_evaluate(
        db_path=get_db_path(),
        activate_if_none=False,
    )
    now = utc_now_str()
    dept_info = meta["department"]
    prio_info = meta["priority"]
    with closing(get_connection()) as conn, conn:
        conn.execute(
            """
            INSERT INTO ml_models
                (version, artifact_path, metadata_path, dept_model_name,
                 dept_test_f1, priority_model_name, priority_test_f1,
                 training_rows, is_active, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (
                meta["version"],
                meta["artifact_path"],
                meta["metadata_path"],
                dept_info["best_model"],
                dept_info["best_metrics"]["test_macro_f1"],
                prio_info["best_model"],
                prio_info["best_metrics"]["test_macro_f1"],
                meta["dataset_rows"],
                actor["id"],
                now,
            ),
        )
    logger.info("User %s retrained ML model version %s (not activated)", actor["username"], meta["version"])
    return meta


def activate_ml_model(actor: dict | None, version: str) -> dict[str, Any]:
    """Manually activate a saved ML model version and reload the inference service."""
    require(actor, "manage_ml")
    sync_ml_models_registry()
    models_dir = Path(get_env_str("ML_MODELS_DIR", str(PROJECT_ROOT / "ml" / "models")))
    now = utc_now_str()

    with closing(get_connection()) as conn, conn:
        row = conn.execute("SELECT * FROM ml_models WHERE version = ?", (version,)).fetchone()
        if row is None:
            raise ValueError(f"Model version '{version}' not found.")
        art_path = Path(row["artifact_path"])
        if not art_path.is_absolute():
            art_path = PROJECT_ROOT / art_path
        if not art_path.exists():
            raise FileNotFoundError(f"Artifact file missing on disk: {art_path}")

        conn.execute("UPDATE ml_models SET is_active = 0")
        conn.execute(
            "UPDATE ml_models SET is_active = 1, activated_at = ? WHERE version = ?",
            (now, version),
        )

    pointer = models_dir / "active_model.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        json.dumps(
            {
                "version": version,
                "artifact_path": str(row["artifact_path"]),
                "metadata_path": str(row["metadata_path"]),
                "activated_at": f"{now} UTC",
                "activated_by": actor["username"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    from ml.classifier import reload_classifier

    reload_classifier()
    logger.info("User %s activated ML model version %s", actor["username"], version)
    return dict(row)
