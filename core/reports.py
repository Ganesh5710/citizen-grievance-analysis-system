"""
Jurisdiction-scoped governance reports (CSV, Excel XLSX, multi-page PDF, Audit CSV, Weekly Digest).
"""

from __future__ import annotations

from contextlib import closing
import csv
from datetime import datetime, timedelta, timezone
import io
from typing import Any
from xml.sax.saxutils import escape as xml_escape
import zipfile

import matplotlib

matplotlib.use("Agg")
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from core.complaints import get_complaints
import core.database as _db
from core.permissions import require


def _csv_safe(val: Any) -> str:
    """Neutralize CSV/spreadsheet formula injection on string cells."""
    s = "" if val is None else str(val)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


def _col_letter(idx_1based: int) -> str:
    res = ""
    n = idx_1based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        res = chr(65 + rem) + res
    return res


def _enforce_requested_scope(
    actor: dict,
    district_id: int | None,
    department_id: int | None,
) -> None:
    if actor.get("role") != "admin":
        return
    assigned_districts = [int(d) for d in (actor.get("assigned_districts") or [])]
    if not assigned_districts and actor.get("district_id") is not None:
        assigned_districts = [int(actor["district_id"])]
    if district_id is not None and int(district_id) not in assigned_districts:
        raise PermissionError("Admins can only export reports for their assigned district(s).")
    admin_dept = actor.get("department_id")
    if admin_dept is not None and department_id is not None and int(department_id) != int(admin_dept):
        raise PermissionError("You are not authorized to export another department's report.")


def fetch_filtered_complaints_for_report(
    actor: dict | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    district_id: int | None = None,
    department_id: int | None = None,
    status: str | None = None,
) -> list[dict]:
    """Return jurisdiction-filtered complaint records (zero citizen PII)."""
    require(actor, "export_reports")
    _enforce_requested_scope(actor, district_id, department_id)

    rows = get_complaints(actor)
    out: list[dict] = []
    for r in rows:
        if status and status != "All" and r.get("status") != status:
            continue
        if district_id is not None and r.get("district_id") != int(district_id):
            continue
        if department_id is not None and r.get("department_id") != int(department_id):
            continue
        created = str(r.get("created_at") or "")[:10]
        if start_date and created and created < str(start_date)[:10]:
            continue
        if end_date and created and created > str(end_date)[:10]:
            continue
        out.append(r)
    return out


_REPORT_HEADERS = [
    "Incident ID",
    "Department",
    "District",
    "Mandal",
    "Area",
    "Pincode",
    "Priority",
    "Status",
    "Escalation Level",
    "Assigned Officer",
    "Report Count",
    "Created At (UTC)",
    "Due At (UTC)",
    "Resolved At (UTC)",
    "Summary",
]


def _row_values(r: dict) -> list[str]:
    return [
        _csv_safe(r.get("incident_id", "")),
        _csv_safe(r.get("department", "")),
        _csv_safe(r.get("district_name", "") or ""),
        _csv_safe(r.get("mandal_name", "") or ""),
        _csv_safe(r.get("area_name", "") or ""),
        _csv_safe(r.get("pincode", "") or ""),
        _csv_safe(r.get("priority", "")),
        _csv_safe(r.get("status", "")),
        str(int(r.get("escalation_level") or 0)),
        _csv_safe(r.get("assigned_username", "") or "Unassigned"),
        str(int(r.get("report_count") or 1)),
        _csv_safe(r.get("created_at", "") or ""),
        _csv_safe(r.get("due_at", "") or ""),
        _csv_safe(r.get("resolved_at", "") or ""),
        _csv_safe((r.get("text") or "")[:180]),
    ]


def export_complaints_csv(
    actor: dict | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    district_id: int | None = None,
    department_id: int | None = None,
    status: str | None = None,
) -> bytes:
    rows = fetch_filtered_complaints_for_report(
        actor,
        start_date=start_date,
        end_date=end_date,
        district_id=district_id,
        department_id=department_id,
        status=status,
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_REPORT_HEADERS)
    for r in rows:
        writer.writerow(_row_values(r))
    return buf.getvalue().encode("utf-8")


def _build_sheet_xml(rows: list[list[str]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for r_idx, row in enumerate(rows, start=1):
        lines.append(f'<row r="{r_idx}">')
        for c_idx, val in enumerate(row, start=1):
            ref = f"{_col_letter(c_idx)}{r_idx}"
            esc = xml_escape(str(val))
            lines.append(f'<c r="{ref}" t="inlineStr"><is><t>{esc}</t></is></c>')
        lines.append("</row>")
    lines.append("</sheetData></worksheet>")
    return "".join(lines)


def export_complaints_xlsx(
    actor: dict | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    district_id: int | None = None,
    department_id: int | None = None,
    status: str | None = None,
) -> bytes:
    """Export filtered complaints + summary sheet into an OpenXML .xlsx workbook."""
    rows = fetch_filtered_complaints_for_report(
        actor,
        start_date=start_date,
        end_date=end_date,
        district_id=district_id,
        department_id=department_id,
        status=status,
    )
    total = len(rows)
    resolved = sum(1 for r in rows if r.get("status") in ("Resolved", "Closed"))
    overdue = sum(1 for r in rows if r.get("is_overdue"))
    summary_rows = [
        ["Citizen Grievance Governance Report", "Generated UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")],
        ["Metric", "Value", ""],
        ["Total Matching Complaints", str(total), ""],
        ["Resolved / Closed", str(resolved), ""],
        ["Open / Active", str(total - resolved), ""],
        ["Overdue (SLA Breached)", str(overdue), ""],
    ]
    detail_rows = [_REPORT_HEADERS] + [_row_values(r) for r in rows]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet2.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<sheets>"
            '<sheet name="Summary" sheetId="1" r:id="rId1"/>'
            '<sheet name="Complaints" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", _build_sheet_xml(summary_rows))
        zf.writestr("xl/worksheets/sheet2.xml", _build_sheet_xml(detail_rows))
    return buf.getvalue()


def export_complaints_pdf(
    actor: dict | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    district_id: int | None = None,
    department_id: int | None = None,
    status: str | None = None,
) -> bytes:
    """Render a multi-page landscape tabular PDF summary report using matplotlib PdfPages."""
    rows = fetch_filtered_complaints_for_report(
        actor,
        start_date=start_date,
        end_date=end_date,
        district_id=district_id,
        department_id=department_id,
        status=status,
    )
    total = len(rows)
    resolved = sum(1 for r in rows if r.get("status") in ("Resolved", "Closed"))
    overdue = sum(1 for r in rows if r.get("is_overdue"))
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    buf = io.BytesIO()
    cols = ["Ticket ID", "Department", "District", "Priority", "Status", "Officer", "Created (UTC)", "Summary"]
    chunk_size = 18
    chunks = [rows[i : i + chunk_size] for i in range(0, max(1, len(rows)), chunk_size)]

    with PdfPages(buf) as pdf:
        for page_idx, chunk in enumerate(chunks, start=1):
            fig, ax = plt.subplots(figsize=(11, 8.5))
            ax.axis("off")
            title = f"Citizen Grievance System — Governance Report (Page {page_idx}/{len(chunks)})"
            subtitle = (
                f"Generated: {now_str} | Total: {total} | Resolved/Closed: {resolved} | Overdue SLA: {overdue}"
            )
            fig.text(0.05, 0.94, title, fontsize=14, fontweight="bold")
            fig.text(0.05, 0.91, subtitle, fontsize=10, color="#475569")

            if chunk:
                table_data = [
                    [
                        r.get("incident_id", ""),
                        (r.get("department") or "")[:16],
                        (r.get("district_name") or "")[:16],
                        r.get("priority", ""),
                        r.get("status", ""),
                        (r.get("assigned_username") or "Unassigned")[:14],
                        str(r.get("created_at") or "")[:16],
                        (r.get("text") or "").replace("\n", " ")[:42],
                    ]
                    for r in chunk
                ]
                tbl = ax.table(
                    cellText=table_data,
                    colLabels=cols,
                    loc="center",
                    cellLoc="left",
                    colWidths=[0.11, 0.11, 0.11, 0.08, 0.13, 0.11, 0.13, 0.22],
                )
                tbl.auto_set_font_size(False)
                tbl.set_fontsize(8)
                tbl.scale(1.0, 1.4)
            else:
                fig.text(0.05, 0.80, "No complaints match the selected filters.", fontsize=11)

            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

    return buf.getvalue()


def export_audit_logs_csv(
    actor: dict | None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    district_id: int | None = None,
    department_id: int | None = None,
) -> bytes:
    """Export filtered audit log rows scoped to the Admin's jurisdiction or full system for Super Admin."""
    require(actor, "export_reports")
    _enforce_requested_scope(actor, district_id, department_id)

    where = ["c.is_deleted = 0"]
    params: list[Any] = []
    if actor.get("role") == "admin":
        assigned = [int(d) for d in (actor.get("assigned_districts") or [])]
        if not assigned and actor.get("district_id") is not None:
            assigned = [int(actor["district_id"])]
        if assigned:
            placeholders = ",".join("?" for _ in assigned)
            where.append(f"c.district_id IN ({placeholders})")
            params.extend(assigned)
        else:
            where.append("1 = 0")
        if actor.get("department_id") is not None:
            where.append("c.department_id = ?")
            params.append(int(actor["department_id"]))

    if district_id is not None:
        where.append("c.district_id = ?")
        params.append(int(district_id))
    if department_id is not None:
        where.append("c.department_id = ?")
        params.append(int(department_id))
    if start_date:
        where.append("substr(cu.created_at, 1, 10) >= ?")
        params.append(str(start_date)[:10])
    if end_date:
        where.append("substr(cu.created_at, 1, 10) <= ?")
        params.append(str(end_date)[:10])

    sql = f"""
        SELECT cu.id, c.incident_id, d.name AS department, dist.name AS district,
               cu.actor_label, cu.field_name, cu.old_value, cu.new_value,
               cu.source, cu.remark, cu.created_at
        FROM complaint_updates cu
        JOIN complaints c ON c.id = cu.complaint_id
        JOIN departments d ON d.id = c.department_id
        LEFT JOIN districts dist ON dist.id = c.district_id
        WHERE {' AND '.join(where)}
        ORDER BY cu.id DESC
    """
    with closing(_db.get_connection()) as conn:
        rows = conn.execute(sql, params).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Audit ID", "Incident ID", "Department", "District", "Actor",
        "Field", "Old Value", "New Value", "Source", "Remark", "Created At (UTC)",
    ])
    for r in rows:
        writer.writerow([
            r["id"],
            _csv_safe(r["incident_id"]),
            _csv_safe(r["department"]),
            _csv_safe(r["district"] or ""),
            _csv_safe(r["actor_label"]),
            _csv_safe(r["field_name"]),
            _csv_safe(r["old_value"]),
            _csv_safe(r["new_value"]),
            _csv_safe(r["source"]),
            _csv_safe(r["remark"]),
            _csv_safe(r["created_at"]),
        ])
    return buf.getvalue().encode("utf-8")


def generate_weekly_admin_summary(
    admin_user: dict,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute a zero-PII 7-day governance digest for a verified Admin's jurisdiction."""
    now_dt = now or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    since_str = (now_dt - timedelta(days=int(days))).strftime("%Y-%m-%d")

    all_rows = fetch_filtered_complaints_for_report(admin_user)
    recent_rows = [r for r in all_rows if str(r.get("created_at") or "")[:10] >= since_str]
    open_rows = [r for r in all_rows if r.get("status") not in ("Resolved", "Closed", "Withdrawn")]
    overdue_rows = [r for r in open_rows if r.get("is_overdue")]
    resolved_recent = [r for r in recent_rows if r.get("status") in ("Resolved", "Closed")]

    body = (
        f"Weekly Civic Governance Digest (last {days} days, ending {now_dt.strftime('%Y-%m-%d')} UTC)\n"
        f"- New Complaints in Jurisdiction: {len(recent_rows)}\n"
        f"- Resolved/Closed (from recent cohort): {len(resolved_recent)}\n"
        f"- Currently Open Queue: {len(open_rows)}\n"
        f"- Currently Overdue (SLA Breached): {len(overdue_rows)}\n"
        "Sign in to the Admin dashboard to export full PDF/Excel reports."
    )
    return {
        "admin_id": admin_user["id"],
        "new_count": len(recent_rows),
        "resolved_count": len(resolved_recent),
        "open_count": len(open_rows),
        "overdue_count": len(overdue_rows),
        "subject": f"Weekly Grievance Jurisdiction Digest ({now_dt.strftime('%Y-%m-%d')})",
        "body": body,
    }
