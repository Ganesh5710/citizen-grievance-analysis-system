"""
Secure image attachment handling for complaints:
  - Content validation via Pillow (rejects renamed non-images, SVG, HTML, etc.)
  - Server-side size (<= 5 MB) and count (<= 3 per complaint) enforcement
  - EXIF/GPS stripping via pixel re-encoding & downscaling to max 1600 px
  - UUID-based storage inside uploads/<complaint_id>/ (outside any served path)
  - Scope-enforced get_images() and admin/super_admin soft remove_image()
"""

from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import re
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

import core.database as _db
from core.permissions import require

MAX_IMAGES_PER_COMPLAINT = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMAGE_DIMENSION = 1600

_FORMAT_META: dict[str, tuple[str, str]] = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG":  (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def get_uploads_dir() -> Path:
    """Resolve uploads root directory (honors CGS_UPLOADS_DIR for isolated tests)."""
    override = os.environ.get("CGS_UPLOADS_DIR")
    return Path(override) if override else Path(__file__).parent.parent / "uploads"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _sanitize_original_filename(filename: str | None) -> str:
    base = Path(str(filename or "photo")).name
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._")
    return (cleaned or "photo")[:120]


def _extract_file_bytes_and_name(file_obj) -> tuple[bytes, str]:
    """Support Streamlit UploadedFile, file-like objects, (name, bytes) tuples, or dicts."""
    if isinstance(file_obj, dict):
        raw = file_obj.get("data") or file_obj.get("bytes")
        name = file_obj.get("name") or file_obj.get("filename") or "photo"
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw), str(name)
    if isinstance(file_obj, tuple) and len(file_obj) == 2:
        a, b = file_obj
        if isinstance(a, str) and isinstance(b, (bytes, bytearray)):
            return bytes(b), a
        if isinstance(a, (bytes, bytearray)) and isinstance(b, str):
            return bytes(a), b
    if isinstance(file_obj, (bytes, bytearray)):
        return bytes(file_obj), "photo"
    if hasattr(file_obj, "getvalue"):
        return bytes(file_obj.getvalue()), str(getattr(file_obj, "name", "photo"))
    if hasattr(file_obj, "read"):
        pos = file_obj.tell() if hasattr(file_obj, "tell") else None
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        raw = bytes(file_obj.read())
        if pos is not None and hasattr(file_obj, "seek"):
            file_obj.seek(pos)
        return raw, str(getattr(file_obj, "name", "photo"))
    raise ValueError("Unsupported file input.")


def _validate_and_reencode_image(raw_bytes: bytes, orig_name: str) -> tuple[bytes, str, str]:
    """
    Validate actual image content with Pillow, orient, downscale to max 1600 px,
    and re-encode pixel data without any EXIF/GPS/metadata chunks.
    Returns (clean_bytes, ext, mime_type).
    """
    if not raw_bytes:
        raise ValueError(f"Uploaded file '{orig_name}' is empty.")
    if len(raw_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(f"File '{orig_name}' exceeds the 5 MB size limit.")

    # Quick reject obvious SVG/HTML text payloads before Pillow even touches them
    head = raw_bytes[:512].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml", b"<!doctype html", b"<html", b"<script")):
        raise ValueError(f"File '{orig_name}' is not a supported raster image.")

    try:
        with Image.open(BytesIO(raw_bytes)) as probe:
            probe.verify()
    except (UnidentifiedImageError, Exception) as exc:
        raise ValueError(f"File '{orig_name}' is not a valid image: {exc}")

    try:
        with Image.open(BytesIO(raw_bytes)) as img:
            fmt = (img.format or "").upper()
            if fmt not in _FORMAT_META:
                raise ValueError(
                    f"Unsupported image format '{fmt or 'unknown'}'. Allowed: JPG, PNG, WebP."
                )

            ext, mime_type = _FORMAT_META[fmt]
            # Apply EXIF orientation before stripping EXIF tags
            img = ImageOps.exif_transpose(img)
            img.load()

            if max(img.size) > MAX_IMAGE_DIMENSION:
                img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)

            if fmt == "JPEG":
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    rgba = img.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.split()[3])
                    clean = bg
                elif img.mode != "RGB":
                    clean = img.convert("RGB")
                else:
                    clean = Image.frombytes("RGB", img.size, img.tobytes())
            else:
                target_mode = "RGBA" if ("A" in img.mode or "transparency" in img.info) else "RGB"
                converted = img if img.mode == target_mode else img.convert(target_mode)
                clean = Image.frombytes(converted.mode, converted.size, converted.tobytes())

            out = BytesIO()
            if fmt == "JPEG":
                clean.save(out, format="JPEG", quality=88, optimize=True)
            elif fmt == "PNG":
                clean.save(out, format="PNG", optimize=True)
            else:
                clean.save(out, format="WEBP", quality=88)

            return out.getvalue(), ext, mime_type
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not process image '{orig_name}': {exc}")


def _assert_can_view_complaint(conn, actor: dict | None, complaint_id: int) -> dict:
    """Enforce role + scope (user=own, employee=own department, admin/super_admin=all)."""
    row = conn.execute(
        "SELECT id, user_id, department_id, status, is_deleted FROM complaints WHERE id = ?",
        (complaint_id,),
    ).fetchone()

    if isinstance(actor, dict) and actor.get("role") == "user":
        require(actor, "view_own")
        if row is None or row["is_deleted"] or row["user_id"] != actor.get("id"):
            raise PermissionError("You do not have access to this complaint.")
        return dict(row)

    require(actor, "view_all")
    if row is None or row["is_deleted"]:
        raise ValueError(f"Complaint with id {complaint_id} not found.")
    if actor.get("role") == "employee":
        if actor.get("department_id") is None or row["department_id"] != actor.get("department_id"):
            raise PermissionError("Employees can only view attachments in their own department.")
    return dict(row)


def count_images(complaint_id: int) -> int:
    """Return count of active (non-removed) images for complaint_id."""
    with closing(_db.get_connection()) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM complaint_images WHERE complaint_id = ? AND is_removed = 0",
            (complaint_id,),
        ).fetchone()[0]


def get_image_counts_map(complaint_ids: list[int]) -> dict[int, int]:
    """Batch-query active image counts for a list of complaint IDs."""
    if not complaint_ids:
        return {}
    placeholders = ",".join("?" for _ in complaint_ids)
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(
            f"SELECT complaint_id, COUNT(*) AS cnt FROM complaint_images "
            f"WHERE is_removed = 0 AND complaint_id IN ({placeholders}) GROUP BY complaint_id",
            tuple(complaint_ids),
        ).fetchall()
    return {row["complaint_id"]: row["cnt"] for row in rows}


def prepare_images(files: list | tuple | None) -> list[tuple[bytes, str, str, str]]:
    """Validate and sanitize a batch of uploaded image files before DB insertion."""
    if not files:
        return []
    prepared: list[tuple[bytes, str, str, str]] = []
    for fobj in list(files):
        raw, raw_name = _extract_file_bytes_and_name(fobj)
        safe_orig = _sanitize_original_filename(raw_name)
        clean_bytes, ext, mime_type = _validate_and_reencode_image(raw, safe_orig)
        stored_name = f"{uuid.uuid4().hex}{ext}"
        prepared.append((clean_bytes, stored_name, safe_orig, mime_type))
    return prepared


def save_prepared_images_in_conn(
    conn,
    actor: dict,
    complaint_id: int,
    complaint_status: str,
    prepared: list[tuple[bytes, str, str, str]],
    now: str | None = None,
) -> list[dict]:
    """Write already-validated image bytes to disk and insert DB + audit records."""
    if not prepared:
        return []

    target_dir = get_uploads_dir() / str(complaint_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = now or _utc_now()
    created: list[dict] = []

    for clean_bytes, stored_name, safe_orig, mime_type in prepared:
        dest_path = target_dir / stored_name
        dest_path.write_bytes(clean_bytes)
        size_bytes = len(clean_bytes)

        cursor = conn.execute(
            """
            INSERT INTO complaint_images
                (complaint_id, uploaded_by, stored_filename, original_filename,
                 mime_type, file_size, created_at, is_removed)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (complaint_id, actor["id"], stored_name, safe_orig, mime_type, size_bytes, ts),
        )
        created.append({
            "id":                cursor.lastrowid,
            "complaint_id":      complaint_id,
            "uploaded_by":       actor["id"],
            "stored_filename":   stored_name,
            "original_filename": safe_orig,
            "mime_type":         mime_type,
            "file_size":         size_bytes,
            "created_at":        ts,
        })

        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'image_added', ?, ?, '', ?, 'citizen_upload', ?, ?)
            """,
            (
                complaint_id,
                actor["id"],
                actor.get("username", ""),
                complaint_status,
                complaint_status,
                safe_orig,
                f"Photo attached: {safe_orig}",
                ts,
            ),
        )

    return created


def add_images(actor: dict | None, complaint_id: int, files: list | tuple) -> list[dict]:
    """
    Validate and attach up to MAX_IMAGES_PER_COMPLAINT images to an open complaint.
    Only the owning citizen ('user') may call this, and only while status is not Resolved/Closed.
    """
    require(actor, "add_images")

    if not files:
        return []

    file_list = list(files)

    with closing(_db.get_connection()) as conn, conn:
        comp = conn.execute(
            "SELECT id, user_id, status, is_deleted FROM complaints WHERE id = ?",
            (complaint_id,),
        ).fetchone()
        if comp is None or comp["is_deleted"] or comp["user_id"] != actor.get("id"):
            raise PermissionError("You can only add photos to your own open complaints.")
        if comp["status"] in ("Resolved", "Closed"):
            raise ValueError("Cannot attach photos after a complaint is Resolved or Closed.")

        existing_count = conn.execute(
            "SELECT COUNT(*) FROM complaint_images WHERE complaint_id = ? AND is_removed = 0",
            (complaint_id,),
        ).fetchone()[0]
        if existing_count + len(file_list) > MAX_IMAGES_PER_COMPLAINT:
            raise ValueError(
                f"Maximum {MAX_IMAGES_PER_COMPLAINT} photos allowed per complaint "
                f"(currently {existing_count})."
            )

        prepared = prepare_images(file_list)
        created = save_prepared_images_in_conn(conn, actor, complaint_id, comp["status"], prepared)

    return created


def get_images(actor: dict | None, complaint_id: int) -> list[dict]:
    """
    Retrieve active images (with raw bytes) for complaint_id.
    Enforces: user=own only, employee=own department only, admin/super_admin=all.
    """
    with closing(_db.get_connection()) as conn:
        _assert_can_view_complaint(conn, actor, complaint_id)
        rows = conn.execute(
            """
            SELECT id, complaint_id, uploaded_by, stored_filename, original_filename,
                   mime_type, file_size, created_at
            FROM complaint_images
            WHERE complaint_id = ? AND is_removed = 0
            ORDER BY id ASC
            """,
            (complaint_id,),
        ).fetchall()

    uploads_dir = get_uploads_dir() / str(complaint_id)
    results: list[dict] = []
    for row in rows:
        file_path = uploads_dir / Path(row["stored_filename"]).name
        data = file_path.read_bytes() if file_path.is_file() else b""
        results.append({
            "id":                row["id"],
            "complaint_id":      row["complaint_id"],
            "uploaded_by":       row["uploaded_by"],
            "stored_filename":   row["stored_filename"],
            "original_filename": row["original_filename"],
            "mime_type":         row["mime_type"],
            "file_size":         row["file_size"],
            "created_at":        row["created_at"],
            "data":              data,
        })
    return results


def remove_image(actor: dict | None, image_id: int, reason: str = "") -> None:
    """
    Soft-delete an image attachment (admin / super_admin only) and write an audit row.
    """
    require(actor, "remove_image")

    clean_reason = reason.strip()[:300] if isinstance(reason, str) else ""
    now = _utc_now()

    with closing(_db.get_connection()) as conn, conn:
        row = conn.execute(
            """
            SELECT ci.id, ci.complaint_id, ci.original_filename, ci.is_removed, c.status, c.is_deleted
            FROM complaint_images ci
            JOIN complaints c ON c.id = ci.complaint_id
            WHERE ci.id = ?
            """,
            (image_id,),
        ).fetchone()
        if row is None or row["is_deleted"] or row["is_removed"]:
            raise ValueError(f"Image with id {image_id} not found.")

        conn.execute(
            "UPDATE complaint_images SET is_removed = 1, removed_by = ?, removed_at = ? WHERE id = ?",
            (actor["id"], now, image_id),
        )
        conn.execute(
            """
            INSERT INTO complaint_updates
                (complaint_id, employee_id, actor_label, field_name,
                 old_status, new_status, old_value, new_value, source, remark, created_at)
            VALUES (?, ?, ?, 'image_removed', ?, ?, ?, 'Removed', 'staff_update', ?, ?)
            """,
            (
                row["complaint_id"],
                actor["id"],
                actor.get("username", ""),
                row["status"],
                row["status"],
                row["original_filename"],
                clean_reason or f"Attachment removed: {row['original_filename']}",
                now,
            ),
        )
