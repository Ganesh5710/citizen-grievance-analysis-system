"""
core/clusters.py
Phase 1: Duplicate Detection, Sentence-Embedding / TF-IDF Similarity, Citizen Support,
Incident Clusters (incident_groups), Priority Bump on Cluster Counts, and Cluster Resolution.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import threading
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.pipeline import FeatureUnion

from core.config import get_env_bool, get_env_float, get_env_int, get_env_str
import core.database as _db
from core.locations import resolve_location_selection
from core.logging_setup import get_logger
from core.permissions import require
from core.security import sanitize_text
from core.sla import compute_due_at
from core.wards import haversine_meters

_logger = get_logger("clusters")

_EMBED_LOCK = threading.Lock()
_EMBED_MODEL: Any = None
_EMBED_TRIED = False

_PRIO_RANK = {"Low": 1, "Medium": 2, "High": 3}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _load_embedding_model() -> Any | None:
    """
    Attempt to load multilingual SentenceTransformer ('paraphrase-multilingual-MiniLM-L12-v2').
    Returns None gracefully if package is absent or model is not cached offline.
    """
    global _EMBED_MODEL, _EMBED_TRIED
    with _EMBED_LOCK:
        if _EMBED_TRIED:
            return _EMBED_MODEL
        _EMBED_TRIED = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            model_name = get_env_str(
                "CGS_DUPLICATE_EMBEDDING_MODEL",
                "paraphrase-multilingual-MiniLM-L12-v2",
            )
            allow_dl = get_env_bool("CGS_ALLOW_MODEL_DOWNLOAD", False)
            _EMBED_MODEL = SentenceTransformer(
                model_name,
                local_files_only=not allow_dl,
            )
        except Exception as exc:
            _logger.info("Sentence embedding model unavailable (%s); using TF-IDF cosine fallback.", exc)
            _EMBED_MODEL = None
        return _EMBED_MODEL


def compute_similarity_scores(
    query_text: str,
    candidate_texts: list[str],
    *,
    force_tfidf: bool = False,
) -> tuple[list[float], str]:
    """
    Compute semantic/lexical cosine similarity in [0.0, 1.0] between query_text and candidate_texts.
    Prefers paraphrase-multilingual-MiniLM-L12-v2 when available, else falls back cleanly to TF-IDF cosine.
    """
    if not candidate_texts or not str(query_text or "").strip():
        return ([0.0] * len(candidate_texts), "tfidf_fallback")

    model = None if force_tfidf else _load_embedding_model()
    if model is not None:
        try:
            q_emb = model.encode([query_text], normalize_embeddings=True)
            c_emb = model.encode(candidate_texts, normalize_embeddings=True)
            sims = np.clip(np.dot(c_emb, q_emb.T).reshape(-1), 0.0, 1.0)
            return ([round(float(s), 4) for s in sims], "embedding")
        except Exception as exc:
            _logger.warning("Embedding inference failed (%s); falling back to TF-IDF cosine.", exc)

    corpus = [query_text, *candidate_texts]
    vec = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1)),
    ])
    mat = vec.fit_transform(corpus)
    sims = cosine_similarity(mat[0:1], mat[1:]).reshape(-1)
    sims = np.clip(sims, 0.0, 1.0)
    return ([round(float(s), 4) for s in sims], "tfidf_fallback")


def _check_admin_jurisdiction_in_conn(conn, actor: dict, district_id: int | None, department_id: int) -> None:
    if actor.get("role") == "super_admin":
        return
    if actor.get("role") != "admin":
        raise PermissionError("Admin or Super Admin role required.")
    j_rows = conn.execute(
        "SELECT district_id, department_id FROM admin_jurisdictions WHERE user_id = ?",
        (actor["id"],),
    ).fetchall()
    if not j_rows:
        if district_id is not None:
            raise PermissionError("Admin lacks jurisdiction for district complaints.")
        return
    if district_id is None or not any(
        int(r["district_id"]) == int(district_id)
        and (r["department_id"] is None or int(r["department_id"]) == int(department_id))
        for r in j_rows
    ):
        raise PermissionError("You do not have jurisdiction over complaints in this district/department.")


def find_similar_open_complaints(
    department_id: int,
    text: str,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    pincode: str | None = None,
    state_id: int | None = None,
    district_id: int | None = None,
    mandal_id: int | None = None,
    area_id: int | None = None,
    radius_m: float | None = None,
    threshold: float | None = None,
    exclude_complaint_id: int | None = None,
    force_tfidf: bool = False,
) -> dict[str, Any]:
    """
    Search open complaints in the same department within `radius_m` (default 300 m)
    using multilingual sentence embeddings (fallback TF-IDF cosine similarity).
    """
    clean_q = sanitize_text(text or "", max_length=2000, field_name="Complaint text")
    if len(clean_q) < 4:
        return {"matches": [], "method": "none", "radius_m": 0.0, "threshold": 0.0}

    eff_radius = float(radius_m if radius_m is not None else get_env_float("CGS_DUPLICATE_RADIUS_M", 300.0))

    lat = float(latitude) if latitude is not None else None
    lon = float(longitude) if longitude is not None else None
    if lat is None or lon is None:
        try:
            loc_info = resolve_location_selection(
                pincode=pincode,
                state_id=state_id,
                district_id=district_id,
                mandal_id=mandal_id,
                area_id=area_id,
            )
            if lat is None:
                lat = loc_info.get("latitude")
            if lon is None:
                lon = loc_info.get("longitude")
        except ValueError:
            pass

    if lat is None or lon is None:
        return {"matches": [], "method": "no_coordinates", "radius_m": eff_radius, "threshold": 0.0}

    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.incident_id, c.user_id, c.text, c.status, c.priority,
                   c.district_id, c.incident_group_id,
                   COALESCE(c.latitude, ar.latitude) AS eff_lat,
                   COALESCE(c.longitude, ar.longitude) AS eff_lon,
                   COALESCE(ig.member_count, 1) AS member_count,
                   COALESCE(ig.support_count, (
                       SELECT COUNT(*) FROM complaint_supports cs WHERE cs.complaint_id = c.id
                   )) AS support_count,
                   c.created_at
            FROM complaints c
            LEFT JOIN areas ar ON ar.id = c.area_id
            LEFT JOIN incident_groups ig ON ig.id = c.incident_group_id
            WHERE c.is_deleted = 0
              AND c.department_id = ?
              AND c.status NOT IN ('Resolved', 'Closed')
            ORDER BY c.created_at DESC, c.id DESC
            """,
            (int(department_id),),
        ).fetchall()

    nearby: list[tuple[Any, float]] = []
    for r in rows:
        if exclude_complaint_id is not None and int(r["id"]) == int(exclude_complaint_id):
            continue
        r_lat = r["eff_lat"]
        r_lon = r["eff_lon"]
        if r_lat is None or r_lon is None:
            continue
        dist = haversine_meters(float(lat), float(lon), float(r_lat), float(r_lon))
        if dist <= eff_radius:
            nearby.append((r, round(dist, 1)))

    if not nearby:
        return {"matches": [], "method": "tfidf_fallback", "radius_m": eff_radius, "threshold": 0.0}

    candidate_texts = [str(r["text"]) for r, _ in nearby]
    scores, method = compute_similarity_scores(clean_q, candidate_texts, force_tfidf=force_tfidf)
    if threshold is not None:
        eff_thresh = float(threshold)
    elif method == "embedding":
        eff_thresh = get_env_float("CGS_DUPLICATE_EMBED_THRESHOLD", 0.72)
    else:
        eff_thresh = get_env_float("CGS_DUPLICATE_SIM_THRESHOLD", 0.55)

    matches = []
    for (r, dist_m), sim in zip(nearby, scores):
        if sim >= eff_thresh:
            sup_cnt = int(r["support_count"] or 0)
            mem_cnt = int(r["member_count"] or 1)
            matches.append({
                "complaint_id": int(r["id"]),
                "incident_id": r["incident_id"],
                "user_id": int(r["user_id"]),
                "text": r["text"],
                "text_preview": r["text"][:140],
                "status": r["status"],
                "priority": r["priority"],
                "distance_m": dist_m,
                "similarity": sim,
                "incident_group_id": r["incident_group_id"],
                "support_count": sup_cnt,
                "member_count": mem_cnt,
                "report_count": mem_cnt + sup_cnt,
                "created_at": r["created_at"],
            })

    matches.sort(key=lambda m: (-m["similarity"], m["distance_m"]))
    return {
        "matches": matches,
        "method": method,
        "radius_m": eff_radius,
        "threshold": eff_thresh,
    }


def _recompute_group_and_escalate_in_conn(conn, group_id: int, now_str: str) -> dict[str, Any]:
    grp = conn.execute("SELECT * FROM incident_groups WHERE id = ?", (int(group_id),)).fetchone()
    if grp is None:
        return {}

    members = conn.execute(
        "SELECT id, incident_id, department_id, priority, status, due_at, created_at "
        "FROM complaints WHERE incident_group_id = ? AND is_deleted = 0 ORDER BY id ASC",
        (int(group_id),),
    ).fetchall()
    member_ids = [int(m["id"]) for m in members]
    member_count = len(member_ids)
    if member_count == 0:
        conn.execute("DELETE FROM incident_groups WHERE id = ?", (int(group_id),))
        return {"deleted": True}

    placeholders = ",".join("?" for _ in member_ids)
    support_count = int(
        conn.execute(
            f"SELECT COUNT(DISTINCT user_id) FROM complaint_supports WHERE complaint_id IN ({placeholders})",
            tuple(member_ids),
        ).fetchone()[0]
    )
    primary_id = grp["primary_complaint_id"]
    if primary_id not in member_ids:
        primary_id = member_ids[0]

    conn.execute(
        """
        UPDATE incident_groups
        SET primary_complaint_id = ?, member_count = ?, support_count = ?, updated_at = ?
        WHERE id = ?
        """,
        (primary_id, member_count, support_count, now_str, int(group_id)),
    )

    total_reports = member_count + support_count
    t1 = get_env_int("CGS_SUPPORT_ESCALATE_THRESHOLD_1", 3)
    t2 = get_env_int("CGS_SUPPORT_ESCALATE_THRESHOLD_2", 10)
    min_target: str | None = None
    if total_reports >= t2:
        min_target = "High"
    elif total_reports >= t1:
        min_target = "Medium"

    escalated_ids: list[int] = []
    if min_target is not None:
        min_rank = _PRIO_RANK[min_target]
        for m in members:
            if m["status"] in ("Resolved", "Closed"):
                continue
            cur_prio = m["priority"]
            if _PRIO_RANK.get(cur_prio, 1) < min_rank:
                new_due = compute_due_at(conn, m["department_id"], min_target, m["created_at"])
                conn.execute(
                    "UPDATE complaints SET priority = ?, due_at = ?, updated_at = ? WHERE id = ?",
                    (min_target, new_due, now_str, m["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO complaint_updates
                        (complaint_id, employee_id, actor_label, field_name,
                         old_status, new_status, old_value, new_value, source, remark, created_at)
                    VALUES (?, NULL, 'system', 'priority', ?, ?, ?, ?, 'system', ?, ?)
                    """,
                    (
                        m["id"],
                        m["status"],
                        m["status"],
                        cur_prio,
                        min_target,
                        f"Cluster report count reached {total_reports} (members={member_count}, supports={support_count}); priority escalated to {min_target}",
                        now_str,
                    ),
                )
                escalated_ids.append(int(m["id"]))

    return {
        "group_id": int(group_id),
        "primary_complaint_id": int(primary_id),
        "member_count": member_count,
        "support_count": support_count,
        "report_count": total_reports,
        "escalated_complaint_ids": escalated_ids,
    }


def _ensure_group_for_complaint_in_conn(conn, complaint_id: int, now_str: str) -> int:
    c = conn.execute(
        "SELECT id, text, department_id, district_id, incident_group_id, status "
        "FROM complaints WHERE id = ? AND is_deleted = 0",
        (int(complaint_id),),
    ).fetchone()
    if c is None:
        raise ValueError(f"Complaint with id {complaint_id} not found.")
    if c["incident_group_id"] is not None:
        return int(c["incident_group_id"])

    cur = conn.execute(
        """
        INSERT INTO incident_groups
            (title, department_id, district_id, primary_complaint_id,
             member_count, support_count, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, 0, ?, ?, ?)
        """,
        (
            str(c["text"])[:100],
            c["department_id"],
            c["district_id"],
            int(complaint_id),
            "Resolved" if c["status"] in ("Resolved", "Closed") else "Open",
            now_str,
            now_str,
        ),
    )
    gid = int(cur.lastrowid)
    conn.execute(
        "UPDATE complaints SET incident_group_id = ? WHERE id = ?",
        (gid, int(complaint_id)),
    )
    return gid


def support_complaint(actor: dict | None, complaint_id: int) -> dict[str, Any]:
    """
    Allow a citizen ('user') to support an existing open complaint once (cannot support own complaint).
    Automatically joins/creates an incident_group and escalates priority if threshold is crossed.
    """
    require(actor, "support_complaint")
    now_str = _utc_now()
    uid = int(actor["id"])

    with closing(_db.get_connection()) as conn, conn:
        comp = conn.execute(
            "SELECT id, incident_id, user_id, status, incident_group_id, is_deleted "
            "FROM complaints WHERE id = ?",
            (int(complaint_id),),
        ).fetchone()
        if comp is None or comp["is_deleted"]:
            raise ValueError(f"Complaint with id {complaint_id} not found.")
        if int(comp["user_id"]) == uid:
            raise ValueError("You cannot support your own complaint.")
        if comp["status"] in ("Resolved", "Closed"):
            raise ValueError("Cannot support an already Resolved or Closed complaint.")

        dup = conn.execute(
            "SELECT id FROM complaint_supports WHERE complaint_id = ? AND user_id = ?",
            (int(complaint_id), uid),
        ).fetchone()
        if dup is not None:
            raise ValueError("You have already supported this complaint.")

        gid = _ensure_group_for_complaint_in_conn(conn, int(complaint_id), now_str)

        # Also block supporting another member of the same cluster
        grp_dup = conn.execute(
            """
            SELECT cs.id FROM complaint_supports cs
            JOIN complaints c ON c.id = cs.complaint_id
            WHERE c.incident_group_id = ? AND cs.user_id = ?
            """,
            (gid, uid),
        ).fetchone()
        if grp_dup is not None:
            raise ValueError("You have already supported this incident cluster.")

        conn.execute(
            "INSERT INTO complaint_supports (complaint_id, user_id, created_at) VALUES (?, ?, ?)",
            (int(complaint_id), uid, now_str),
        )
        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'support_added', ?, ?, '', '+1', 'citizen_support', ?, ?)
            """,
            (
                int(complaint_id),
                uid,
                actor.get("username", ""),
                comp["status"],
                comp["status"],
                f"Citizen {actor.get('username', '')} supported this complaint",
                now_str,
            ),
        )
        summary = _recompute_group_and_escalate_in_conn(conn, gid, now_str)

    return {
        "complaint_id": int(complaint_id),
        "incident_id": comp["incident_id"],
        **summary,
    }


def merge_complaints(
    actor: dict | None,
    primary_complaint_id: int,
    duplicate_complaint_id: int,
    reason: str,
) -> dict[str, Any]:
    """
    Merge duplicate_complaint_id into primary_complaint_id's incident cluster (Admin / Super Admin).
    Enforces jurisdiction on both complaints and requires mandatory audit-logged reason.
    """
    require(actor, "merge_complaints")
    clean_reason = sanitize_text(reason or "", max_length=500, field_name="Merge reason")
    if len(clean_reason) < 5:
        raise ValueError("Merge reason is required (at least 5 characters).")
    if int(primary_complaint_id) == int(duplicate_complaint_id):
        raise ValueError("Cannot merge a complaint into itself.")

    now_str = _utc_now()
    with closing(_db.get_connection()) as conn, conn:
        pri = conn.execute(
            "SELECT id, incident_id, department_id, district_id, status, incident_group_id, is_deleted "
            "FROM complaints WHERE id = ?",
            (int(primary_complaint_id),),
        ).fetchone()
        dup = conn.execute(
            "SELECT id, incident_id, department_id, district_id, status, incident_group_id, is_deleted "
            "FROM complaints WHERE id = ?",
            (int(duplicate_complaint_id),),
        ).fetchone()
        if pri is None or pri["is_deleted"]:
            raise ValueError(f"Primary complaint {primary_complaint_id} not found.")
        if dup is None or dup["is_deleted"]:
            raise ValueError(f"Duplicate complaint {duplicate_complaint_id} not found.")

        _check_admin_jurisdiction_in_conn(conn, actor, pri["district_id"], pri["department_id"])
        _check_admin_jurisdiction_in_conn(conn, actor, dup["district_id"], dup["department_id"])

        if int(pri["department_id"]) != int(dup["department_id"]):
            raise ValueError("Cannot merge complaints from different departments.")

        pri_gid = _ensure_group_for_complaint_in_conn(conn, int(primary_complaint_id), now_str)
        old_dup_gid = dup["incident_group_id"]

        if old_dup_gid is not None and int(old_dup_gid) != pri_gid:
            # Move any members from old duplicate group into primary group
            conn.execute(
                "UPDATE complaints SET incident_group_id = ?, updated_at = ? WHERE incident_group_id = ?",
                (pri_gid, now_str, int(old_dup_gid)),
            )
            conn.execute("DELETE FROM incident_groups WHERE id = ?", (int(old_dup_gid),))
        else:
            conn.execute(
                "UPDATE complaints SET incident_group_id = ?, updated_at = ? WHERE id = ?",
                (pri_gid, now_str, int(duplicate_complaint_id)),
            )

        for cid, st_val, note in (
            (
                int(primary_complaint_id),
                pri["status"],
                f"Merged duplicate #{dup['incident_id']} into cluster #G{pri_gid}. Reason: {clean_reason}",
            ),
            (
                int(duplicate_complaint_id),
                dup["status"],
                f"Merged as duplicate of #{pri['incident_id']} (cluster #G{pri_gid}). Reason: {clean_reason}",
            ),
        ):
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, ?, ?, 'incident_group', ?, ?, ?, ?, 'staff_update', ?, ?)
                """,
                (
                    cid,
                    actor["id"],
                    actor.get("username", ""),
                    st_val,
                    st_val,
                    str(old_dup_gid or "None"),
                    f"G{pri_gid}",
                    note,
                    now_str,
                ),
            )

        summary = _recompute_group_and_escalate_in_conn(conn, pri_gid, now_str)

    return summary


def unmerge_complaint(actor: dict | None, complaint_id: int, reason: str) -> dict[str, Any]:
    """Detach a complaint from its incident cluster (Admin in jurisdiction / Super Admin)."""
    require(actor, "merge_complaints")
    clean_reason = sanitize_text(reason or "", max_length=500, field_name="Unmerge reason")
    if len(clean_reason) < 5:
        raise ValueError("Unmerge reason is required (at least 5 characters).")

    now_str = _utc_now()
    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            "SELECT id, incident_id, department_id, district_id, status, incident_group_id, is_deleted "
            "FROM complaints WHERE id = ?",
            (int(complaint_id),),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise ValueError(f"Complaint {complaint_id} not found.")
        _check_admin_jurisdiction_in_conn(conn, actor, row["district_id"], row["department_id"])

        old_gid = row["incident_group_id"]
        if old_gid is None:
            raise ValueError("Complaint is not attached to any incident cluster.")

        conn.execute(
            "UPDATE complaints SET incident_group_id = NULL, updated_at = ? WHERE id = ?",
            (now_str, int(complaint_id)),
        )
        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'incident_group', ?, ?, ?, 'None', 'staff_update', ?, ?)
            """,
            (
                int(complaint_id),
                actor["id"],
                actor.get("username", ""),
                row["status"],
                row["status"],
                f"G{old_gid}",
                f"Unmerged from cluster #G{old_gid}. Reason: {clean_reason}",
                now_str,
            ),
        )
        summary = _recompute_group_and_escalate_in_conn(conn, int(old_gid), now_str)

    return {"unmerged_complaint_id": int(complaint_id), "former_group_id": int(old_gid), **summary}


def get_cluster_info(complaint_id: int) -> dict[str, Any]:
    """Return cluster metadata, member tickets, and support count for a complaint."""
    with closing(_db.get_connection()) as conn:
        row = conn.execute(
            "SELECT id, incident_id, incident_group_id FROM complaints WHERE id = ? AND is_deleted = 0",
            (int(complaint_id),),
        ).fetchone()
        if row is None:
            return {"group": None, "members": [], "support_count": 0, "report_count": 1}

        direct_supports = int(
            conn.execute(
                "SELECT COUNT(*) FROM complaint_supports WHERE complaint_id = ?",
                (int(complaint_id),),
            ).fetchone()[0]
        )
        gid = row["incident_group_id"]
        if gid is None:
            return {
                "group": None,
                "members": [{"id": int(row["id"]), "incident_id": row["incident_id"]}],
                "support_count": direct_supports,
                "report_count": 1 + direct_supports,
            }

        grp = conn.execute("SELECT * FROM incident_groups WHERE id = ?", (int(gid),)).fetchone()
        members = conn.execute(
            "SELECT id, incident_id, status, priority, user_id, created_at "
            "FROM complaints WHERE incident_group_id = ? AND is_deleted = 0 ORDER BY id ASC",
            (int(gid),),
        ).fetchall()
        sup_cnt = int(grp["support_count"] or 0) if grp else direct_supports
        mem_cnt = len(members)
        return {
            "group": dict(grp) if grp else None,
            "members": [dict(m) for m in members],
            "support_count": sup_cnt,
            "report_count": mem_cnt + sup_cnt,
        }


def resolve_group_on_primary_resolution_in_conn(
    conn,
    primary_complaint_id: int,
    remark: str,
    now_str: str,
) -> list[int]:
    """
    Rule: When the primary complaint of an incident cluster is marked Resolved,
    automatically mark remaining open cluster members as Resolved (actor='system')
    and create notifications for linked citizen reporters and supporters.
    """
    pri = conn.execute(
        "SELECT id, incident_id, incident_group_id FROM complaints WHERE id = ? AND is_deleted = 0",
        (int(primary_complaint_id),),
    ).fetchone()
    if pri is None or pri["incident_group_id"] is None:
        return []

    gid = int(pri["incident_group_id"])
    grp = conn.execute("SELECT * FROM incident_groups WHERE id = ?", (gid,)).fetchone()
    if grp is None or grp["primary_complaint_id"] != int(primary_complaint_id):
        return []

    conn.execute(
        "UPDATE incident_groups SET status = 'Resolved', updated_at = ? WHERE id = ?",
        (now_str, gid),
    )

    siblings = conn.execute(
        """
        SELECT id, incident_id, user_id, status
        FROM complaints
        WHERE incident_group_id = ? AND id != ? AND is_deleted = 0
        """,
        (gid, int(primary_complaint_id)),
    ).fetchall()

    resolved_sibling_ids: list[int] = []
    notified_users: set[int] = set()

    for sib in siblings:
        notified_users.add(int(sib["user_id"]))
        if sib["status"] not in ("Resolved", "Closed"):
            conn.execute(
                "UPDATE complaints SET status = 'Resolved', resolved_at = ?, updated_at = ? WHERE id = ?",
                (now_str, now_str, sib["id"]),
            )
            conn.execute(
                """
                INSERT INTO complaint_updates
                    (complaint_id, employee_id, actor_label, field_name,
                     old_status, new_status, old_value, new_value, source, remark, created_at)
                VALUES (?, NULL, 'system', 'status', ?, 'Resolved', ?, 'Resolved', 'system', ?, ?)
                """,
                (
                    sib["id"],
                    sib["status"],
                    sib["status"],
                    f"Auto-resolved because primary cluster incident #{pri['incident_id']} was resolved: {remark}",
                    now_str,
                ),
            )
            resolved_sibling_ids.append(int(sib["id"]))

            conn.execute(
                """
                INSERT INTO notifications
                    (user_id, target_role, complaint_id, incident_id, title, message, is_read, created_at)
                VALUES (?, 'user', ?, ?, ?, ?, 0, ?)
                """,
                (
                    sib["user_id"],
                    sib["id"],
                    sib["incident_id"],
                    f"Cluster Incident Resolved: #{sib['incident_id']}",
                    f"Your complaint #{sib['incident_id']} was resolved along with primary incident #{pri['incident_id']}.",
                    now_str,
                ),
            )

    # Notify supporters of this cluster as well
    all_member_ids = [int(primary_complaint_id), *[int(s["id"]) for s in siblings]]
    placeholders = ",".join("?" for _ in all_member_ids)
    sup_rows = conn.execute(
        f"SELECT DISTINCT user_id FROM complaint_supports WHERE complaint_id IN ({placeholders})",
        tuple(all_member_ids),
    ).fetchall()
    for sr in sup_rows:
        suid = int(sr["user_id"])
        if suid in notified_users:
            continue
        notified_users.add(suid)
        conn.execute(
            """
            INSERT INTO notifications
                (user_id, target_role, complaint_id, incident_id, title, message, is_read, created_at)
            VALUES (?, 'user', ?, ?, ?, ?, 0, ?)
            """,
            (
                suid,
                int(primary_complaint_id),
                pri["incident_id"],
                f"Supported Issue Resolved: #{pri['incident_id']}",
                f"An issue you supported (#{pri['incident_id']}) has been marked Resolved.",
                now_str,
            ),
        )

    return resolved_sibling_ids
