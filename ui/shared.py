"""
Shared UI styles, helpers, Plotly charts, Attachments, Escalations, Performance,
and permission-aware complaint dashboard.
Follows Streamlit's native Light / Dark / System theme via CSS theme variables.
"""

from datetime import datetime, timezone
import html

import pandas as pd
import plotly.express as px
import streamlit as st

from core.complaints import (
    VALID_PRIORITIES,
    VALID_STATUSES,
    assign_complaint,
    claim_complaint,
    delete_complaint,
    get_complaint_detail,
    get_complaint_history,
    get_complaints,
    get_department_employees,
    update_complaint,
)
from core.departments import get_active_departments, get_all_departments
from core.images import get_images, remove_image
from core.ml_service import get_review_queue, resolve_review_item
from core.performance import get_employee_own_performance, get_performance_metrics
from core.permissions import can
from core.sla import acknowledge_escalation, run_escalation_check
from ml.classifier import explain_with_lime, has_emergency_keyword

STATUS_OPTIONS = list(VALID_STATUSES)
PRIORITY_OPTIONS = list(VALID_PRIORITIES)

_STATUS_BADGE_CLASSES = {
    "Pending":     "cgs-badge-neutral",
    "Assigned":    "cgs-badge-primary",
    "In Progress": "cgs-badge-warning",
    "Resolved":    "cgs-badge-success",
    "Reopened":    "cgs-badge-danger",
    "Closed":      "cgs-badge-neutral",
}

_PRIORITY_BADGE_CLASSES = {
    "High":   "cgs-badge-danger",
    "Medium": "cgs-badge-warning",
    "Low":    "cgs-badge-success",
}

SHARED_CSS = """
<style>
[data-testid="stMetric"] {
    background: var(
        --secondary-background-color,
        color-mix(in srgb, currentColor 5%, var(--background-color, transparent))
    );
    border: 1px solid color-mix(in srgb, var(--text-color, currentColor) 15%, transparent);
    box-shadow: 0 2px 10px color-mix(in srgb, var(--text-color, currentColor) 5%, transparent);
    padding: 18px 20px;
    border-radius: 14px;
    backdrop-filter: blur(10px);
}

[data-testid="stBaseButton-primary"] button,
[data-testid="stBaseButton-secondary"] button,
[data-testid="stFormSubmitButton"] button {
    border-radius: 10px;
    font-weight: 600;
}

.cgs-card {
    background: var(
        --secondary-background-color,
        color-mix(in srgb, currentColor 5%, var(--background-color, transparent))
    );
    border: 1px solid color-mix(in srgb, var(--text-color, currentColor) 16%, transparent);
    box-shadow: 0 2px 10px color-mix(in srgb, var(--text-color, currentColor) 6%, transparent);
    border-radius: 14px;
    padding: 18px 20px;
    margin-bottom: 14px;
    backdrop-filter: blur(10px);
    color: var(--text-color, currentColor);
}

.cgs-dept-card {
    min-height: 148px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
}

.cgs-hero-card {
    text-align: center;
    padding: 26px 24px;
}

.cgs-top-line {
    height: 4px;
    border-radius: 4px;
    background: linear-gradient(
        90deg,
        var(--primary-color, currentColor),
        color-mix(in srgb, var(--primary-color, currentColor) 35%, transparent)
    );
    margin-bottom: 16px;
}

.cgs-top-bar {
    background: var(
        --secondary-background-color,
        color-mix(in srgb, currentColor 5%, var(--background-color, transparent))
    );
    border: 1px solid color-mix(in srgb, var(--text-color, currentColor) 16%, transparent);
    border-radius: 14px;
    padding: 14px 20px;
    margin-bottom: 18px;
    backdrop-filter: blur(10px);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    color: var(--text-color, currentColor);
}

.cgs-muted {
    color: color-mix(in srgb, var(--text-color, currentColor) 72%, transparent);
}

.cgs-accent {
    color: var(--primary-color, currentColor);
}

.cgs-divider {
    border: none;
    border-top: 1px solid color-mix(in srgb, var(--text-color, currentColor) 16%, transparent);
    margin: 12px 0;
}

.cgs-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-color, currentColor);
}

.cgs-badge-neutral {
    background: color-mix(in srgb, var(--text-color, currentColor) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--text-color, currentColor) 26%, transparent);
}

.cgs-badge-primary {
    background: color-mix(in srgb, var(--primary-color, currentColor) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--primary-color, currentColor) 45%, transparent);
}

.cgs-badge-warning {
    background: rgba(245, 158, 11, 0.18);
    border: 1px solid rgba(245, 158, 11, 0.55);
}

.cgs-badge-success {
    background: rgba(34, 197, 94, 0.18);
    border: 1px solid rgba(34, 197, 94, 0.55);
}

.cgs-badge-danger {
    background: rgba(239, 68, 68, 0.18);
    border: 1px solid rgba(239, 68, 68, 0.55);
}

.cgs-badge-purple {
    background: rgba(139, 92, 246, 0.18);
    border: 1px solid rgba(139, 92, 246, 0.55);
}
</style>
"""


def render_html(markup: str) -> None:
    """Render HTML cleanly via st.markdown without CommonMark indented-code pitfalls."""
    lines = [line.strip() for line in markup.splitlines() if line.strip()]
    st.markdown("\n".join(lines), unsafe_allow_html=True)


def inject_shared_css() -> None:
    """Inject theme-aware CSS once per run."""
    st.markdown(SHARED_CSS, unsafe_allow_html=True)


def build_complaints_df(complaints: list[dict]) -> pd.DataFrame:
    """Build a display DataFrame excluding internal user_id."""
    rows = [
        {
            "Incident ID": c.get("incident_id", ""),
            "Description": c.get("text", ""),
            "Location":    c.get("location", ""),
            "Department":  c.get("department", "") if c.get("department_is_active", True) else f"{c.get('department', '')} (Inactive)",
            "Priority":    c.get("priority", ""),
            "Status":      c.get("status", ""),
            "Assigned To": c.get("assigned_username") or "Unassigned",
            "Due At":      c.get("due_at") or "",
            "SLA":         f"Overdue ({c.get('overdue_hours', 0)}h)" if c.get("is_overdue") else "On Track",
            "Escalation":  f"Level {c.get('escalation_level', 0)}" if c.get("escalation_level", 0) > 0 else "None",
            "Photos":      int(c.get("photo_count", 0)),
            "Created At":  c.get("created_at", ""),
            "Updated At":  c.get("updated_at", ""),
        }
        for c in complaints
    ]
    if not rows:
        return pd.DataFrame(columns=[
            "Incident ID", "Description", "Location", "Department", "Priority",
            "Status", "Assigned To", "Due At", "SLA", "Escalation", "Photos",
            "Created At", "Updated At",
        ])
    return pd.DataFrame(rows)


def render_complaint_card(detail: dict) -> None:
    """Render a theme-aware complaint detail card with HTML-escaped user inputs."""
    status = str(detail.get("status", "Pending"))
    priority = str(detail.get("priority", "Low"))
    s_cls = _STATUS_BADGE_CLASSES.get(status, "cgs-badge-neutral")
    p_cls = _PRIORITY_BADGE_CLASSES.get(priority, "cgs-badge-neutral")

    incident_esc = html.escape(str(detail.get("incident_id", "")))
    dept_name = str(detail.get("department", ""))
    if not detail.get("department_is_active", True):
        dept_name = f"{dept_name} (Inactive)"
    dept_esc = html.escape(dept_name or "Unknown")
    loc_esc = html.escape(str(detail.get("location") or "Not specified"))
    assigned_esc = html.escape(str(detail.get("assigned_username") or "Unassigned"))
    due_esc = html.escape(str(detail.get("due_at") or "Not set"))
    created_esc = html.escape(str(detail.get("created_at", "")))
    updated_esc = html.escape(str(detail.get("updated_at", "")))
    text_esc = html.escape(str(detail.get("text", "")))
    reopen_count = int(detail.get("reopen_count", 0))
    esc_level = int(detail.get("escalation_level", 0))

    extra_badges = ""
    if detail.get("is_overdue"):
        extra_badges += f' <span class="cgs-badge cgs-badge-danger">Overdue ({detail.get("overdue_hours", 0)}h)</span>'
    if esc_level > 0:
        extra_badges += f' <span class="cgs-badge cgs-badge-warning">Escalation Level {esc_level}</span>'
    if detail.get("is_recurring"):
        extra_badges += ' <span class="cgs-badge cgs-badge-purple">Recurring</span>'
    if reopen_count > 0:
        extra_badges += f' <span class="cgs-badge cgs-badge-purple">Reopened x{reopen_count}</span>'

    dur_hrs = detail.get("duration_hours")
    dur_txt = detail.get("duration_matched_text") or ""
    dur_conf = detail.get("duration_confidence")
    prio_reason = detail.get("priority_reason") or ""
    dur_line = ""
    if dur_hrs is not None:
        dur_disp = f"{round(float(dur_hrs) / 24.0, 1)} days ({float(dur_hrs):.1f}h)" if float(dur_hrs) >= 24 else f"{float(dur_hrs):.1f}h"
        conf_str = f", conf {float(dur_conf):.2f}" if dur_conf is not None else ""
        dur_line = f"<p><b>Extracted Duration:</b> {html.escape(dur_disp)} &mdash; <i>\"{html.escape(dur_txt)}\"</i>{html.escape(conf_str)}</p>"
    reason_line = f'<p class="cgs-muted" style="font-size:13px;"><b>Priority Reason:</b> {html.escape(prio_reason)}</p>' if prio_reason else ""

    text_html = text_esc.replace("\n", "<br/>")
    render_html(
        f"""
        <div class="cgs-card">
            <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
                <span style="font-size:18px;font-weight:700;">#{incident_esc}</span>
                <span>
                    <span class="cgs-badge {s_cls}">{html.escape(status)}</span>
                    <span class="cgs-badge {p_cls}">{html.escape(priority)} Priority</span>
                    {extra_badges}
                </span>
            </div>
            <p><b>Department:</b> {dept_esc} &nbsp;|&nbsp; <b>Assigned To:</b> {assigned_esc}</p>
            <p><b>Location:</b> {loc_esc}</p>
            <p><b>Due At (UTC):</b> {due_esc} &nbsp;|&nbsp; <b>Photos:</b> {int(detail.get("photo_count", 0))}</p>
            {dur_line}
            {reason_line}
            <p class="cgs-muted" style="font-size:13px;"><b>Filed:</b> {created_esc} &nbsp;|&nbsp; <b>Updated:</b> {updated_esc}</p>
            <hr class="cgs-divider"/>
            <p style="margin-bottom:0;">{text_html}</p>
        </div>
        """
    )


def render_attachments(user: dict, complaint_id: int, key_prefix: str) -> None:
    """Render secure in-memory photo attachments grid + optional Admin/Super Admin removal."""
    try:
        images = get_images(user, complaint_id)
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return
    except Exception as exc:
        st.warning(f"Could not load attachments: {exc}")
        return

    st.markdown("#### Attachments")
    if not images:
        st.caption("No photos attached to this complaint.")
        return

    can_remove = can(user, "remove_image")
    cols_per_row = 3
    for start in range(0, len(images), cols_per_row):
        cols = st.columns(cols_per_row)
        for col, img in zip(cols, images[start : start + cols_per_row]):
            with col:
                if img["data"]:
                    st.image(img["data"], width="stretch")
                    with st.expander("Full size"):
                        st.image(img["data"], width="stretch")
                size_kb = round(img["file_size"] / 1024.0, 1)
                st.caption(f"{img['original_filename']} ({size_kb} KB)")

                if can_remove:
                    with st.expander("Remove image"):
                        rem_reason = st.text_input(
                            "Reason (optional)",
                            key=f"{key_prefix}_img_reason_{img['id']}",
                        )
                        rem_confirm = st.checkbox(
                            "Confirm removal",
                            key=f"{key_prefix}_img_confirm_{img['id']}",
                        )
                        if st.button(
                            "Remove Image",
                            key=f"{key_prefix}_img_del_{img['id']}",
                        ):
                            if not rem_confirm:
                                st.error("Tick the confirmation box before removing.")
                            else:
                                try:
                                    remove_image(user, img["id"], rem_reason.strip())
                                    st.session_state[f"{key_prefix}_flash_msg"] = (
                                        f"Removed image '{img['original_filename']}'."
                                    )
                                    st.rerun()
                                except (PermissionError, ValueError) as exc:
                                    st.error(str(exc))


def render_history(complaint_id: int, user: dict) -> None:
    """Render chronological audit log cards for a complaint."""
    try:
        history = get_complaint_history(user, complaint_id)
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return
    except Exception as exc:
        st.warning(f"Could not load history: {exc}")
        return

    if not history:
        st.info("No audit updates recorded yet.")
        return

    source_labels = {
        "citizen_selected": "Citizen Selected",
        "citizen_upload":   "Citizen Upload",
        "citizen_feedback": "Citizen Feedback",
        "system":           "System",
        "staff_update":     "Staff Update",
    }

    for entry in history:
        field_name = str(entry.get("field_name") or "status").replace("_", " ").title()
        old_val = str(entry.get("old_value") or entry.get("old_status") or "Initial")
        new_val = str(entry.get("new_value") or entry.get("new_status") or "")
        source = str(entry.get("source") or "staff_update")
        remark = html.escape(str(entry.get("remark") or "No remark"))
        who = html.escape(str(entry.get("actor_username") or "system"))
        when = html.escape(str(entry.get("created_at", "")))

        old_cls = _STATUS_BADGE_CLASSES.get(old_val, "cgs-badge-neutral")
        new_cls = _STATUS_BADGE_CLASSES.get(new_val, "cgs-badge-primary")
        if field_name == "Priority":
            old_cls = _PRIORITY_BADGE_CLASSES.get(old_val, "cgs-badge-neutral")
            new_cls = _PRIORITY_BADGE_CLASSES.get(new_val, "cgs-badge-primary")
        elif field_name in ("Deleted", "Image Removed"):
            new_cls = "cgs-badge-danger"
        elif field_name == "Escalation Level":
            new_cls = "cgs-badge-warning"

        source_label = source_labels.get(source, "Staff Update")

        render_html(
            f"""
            <div class="cgs-card">
                <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;margin-bottom:6px;">
                    <span>
                        <b>{html.escape(field_name)}:</b>&nbsp;
                        <span class="cgs-badge {old_cls}">{html.escape(old_val)}</span>
                        &nbsp;→&nbsp;
                        <span class="cgs-badge {new_cls}">{html.escape(new_val)}</span>
                    </span>
                    <span class="cgs-badge cgs-badge-neutral">{html.escape(source_label)}</span>
                </div>
                <small class="cgs-muted">Actor: <b>{who}</b> on {when} UTC</small>
                <p style="margin-top:8px;margin-bottom:0;font-size:14px;">{remark.replace(chr(10), '<br/>')}</p>
            </div>
            """
        )


def _render_analytics_charts(complaints: list[dict]) -> None:
    """Render Plotly Express charts using Streamlit's native theme."""
    if not complaints:
        return

    raw_df = pd.DataFrame(complaints)
    c1, c2, c3 = st.columns(3)

    with c1:
        dept_counts = raw_df["department"].value_counts().reset_index()
        dept_counts.columns = ["Department", "Count"]
        fig_dept = px.bar(
            dept_counts,
            x="Department",
            y="Count",
            text="Count",
            title="By Department",
            color="Department",
        )
        fig_dept.update_layout(showlegend=False, height=320)
        st.plotly_chart(fig_dept, width="stretch", theme="streamlit")

    with c2:
        status_counts = raw_df["status"].value_counts().reset_index()
        status_counts.columns = ["Status", "Count"]
        fig_status = px.bar(
            status_counts,
            x="Status",
            y="Count",
            text="Count",
            title="By Status",
            color="Status",
        )
        fig_status.update_layout(showlegend=False, height=320)
        st.plotly_chart(fig_status, width="stretch", theme="streamlit")

    with c3:
        prio_counts = raw_df["priority"].value_counts().reset_index()
        prio_counts.columns = ["Priority", "Count"]
        fig_prio = px.pie(
            prio_counts,
            names="Priority",
            values="Count",
            hole=0.5,
            title="By Priority",
        )
        fig_prio.update_layout(height=320)
        st.plotly_chart(fig_prio, width="stretch", theme="streamlit")


def _render_edit_and_delete_controls(user: dict, detail: dict, key_prefix: str) -> None:
    """Render claim, assign/reassign, status/field edit, and delete controls gated by can()."""
    flash_key = f"{key_prefix}_flash_msg"
    is_closed_or_resolved = detail.get("status") in ("Resolved", "Closed")

    # Claim button (employee on unassigned complaint in their department)
    if can(user, "claim_complaint") and detail.get("assigned_to") is None and not is_closed_or_resolved:
        if st.button(
            f"Claim Complaint #{detail['incident_id']}",
            key=f"{key_prefix}_claim_btn_{detail['id']}",
            type="primary",
        ):
            try:
                claim_complaint(user, detail["id"])
                st.session_state[flash_key] = f"Claimed complaint {detail['incident_id']}."
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    # Assignment / Reassignment form (Admin & Super Admin)
    if can(user, "assign_complaint"):
        st.markdown("---")
        st.markdown(f"### Assign Complaint #{detail['incident_id']}")
        dept_emps = get_department_employees(user, detail["department_id"])
        emp_options: list[int | None] = [None, *[e["id"] for e in dept_emps]]
        emp_labels: dict[int | None, str] = {None: "Unassigned"}
        for e in dept_emps:
            emp_labels[e["id"]] = f"{e['username']} ({e['full_name']}) - {e['open_count']} open"

        cur_emp_id = detail.get("assigned_to")
        if cur_emp_id not in emp_options and cur_emp_id is not None:
            emp_options.append(cur_emp_id)
            emp_labels[cur_emp_id] = f"{detail.get('assigned_username', 'Employee')} (Inactive / Other)"

        ver_assign = f"{detail['id']}_{detail['assigned_to']}_{detail['department_id']}_{detail['updated_at']}"
        with st.form(key=f"{key_prefix}_assign_form_{ver_assign}"):
            col_a1, col_a2 = st.columns([2, 3])
            with col_a1:
                chosen_emp_id = st.selectbox(
                    "Assignee (active department employees)",
                    options=emp_options,
                    index=emp_options.index(cur_emp_id) if cur_emp_id in emp_options else 0,
                    format_func=lambda eid: emp_labels[eid],
                    key=f"{key_prefix}_assignee_{ver_assign}",
                )
            with col_a2:
                assign_remark = st.text_input(
                    "Assignment Note (optional)",
                    key=f"{key_prefix}_assign_note_{ver_assign}",
                    placeholder="Reason for assignment or reassignment...",
                )
            if st.form_submit_button("Update Assignment"):
                try:
                    assign_complaint(user, detail["id"], chosen_emp_id, assign_remark.strip())
                    st.session_state[flash_key] = (
                        f"Assignment updated for {detail['incident_id']}."
                    )
                    st.rerun()
                except (PermissionError, ValueError) as exc:
                    st.error(str(exc))

    # Status / Remark / Field Edit form
    if can(user, "update_status_remark"):
        st.markdown("---")
        st.markdown(f"### Update Complaint #{detail['incident_id']}")

        if user.get("role") == "employee" and detail.get("assigned_to") != user.get("id"):
            st.info(
                "This complaint is not assigned to you. Claim it from the Unassigned queue "
                "or ask an administrator to assign it to you before updating status."
            )
        else:
            can_edit_fields = can(user, "edit_complaint_fields")
            version_suffix = f"{detail['id']}_{detail['updated_at']}"

            current_status = detail.get("status", "Pending")
            status_idx = STATUS_OPTIONS.index(current_status) if current_status in STATUS_OPTIONS else 0

            if can_edit_fields:
                active_depts = get_active_departments()
                dept_options: list[dict] = list(active_depts)
                if not any(d["id"] == detail["department_id"] for d in dept_options):
                    dept_options.append({
                        "id":   detail["department_id"],
                        "name": f"{detail['department']} (Inactive)",
                    })
                dept_labels = {d["id"]: d["name"] for d in dept_options}
                dept_ids = [d["id"] for d in dept_options]
                dept_idx = dept_ids.index(detail["department_id"]) if detail["department_id"] in dept_ids else 0

                current_prio = detail.get("priority", "Medium")
                prio_idx = PRIORITY_OPTIONS.index(current_prio) if current_prio in PRIORITY_OPTIONS else 1

            with st.form(key=f"{key_prefix}_edit_form_{version_suffix}"):
                new_status = st.selectbox(
                    "Status",
                    options=STATUS_OPTIONS,
                    index=status_idx,
                    key=f"{key_prefix}_status_{version_suffix}",
                )

                selected_dept_id = None
                selected_priority = None
                if can_edit_fields:
                    col_dept, col_prio = st.columns(2)
                    with col_dept:
                        selected_dept_id = st.selectbox(
                            "Department (auto-reassigns employee on change)",
                            options=dept_ids,
                            index=dept_idx,
                            format_func=lambda did: dept_labels[did],
                            key=f"{key_prefix}_dept_{version_suffix}",
                        )
                    with col_prio:
                        selected_priority = st.selectbox(
                            "Priority (recalculates SLA due date)",
                            options=PRIORITY_OPTIONS,
                            index=prio_idx,
                            key=f"{key_prefix}_prio_{version_suffix}",
                        )

                remark = st.text_area(
                    "Remark (closing remark of at least 10 chars required when resolving)",
                    placeholder="Add notes or resolution details...",
                    key=f"{key_prefix}_remark_{version_suffix}",
                )
                save_btn = st.form_submit_button("Save Update", type="primary")

            if save_btn:
                try:
                    update_complaint(
                        user,
                        detail["id"],
                        new_status=new_status,
                        remark=remark.strip(),
                        department_id=selected_dept_id if can_edit_fields else None,
                        priority=selected_priority if can_edit_fields else None,
                    )
                    st.session_state[flash_key] = f"Complaint {detail['incident_id']} updated."
                    st.rerun()
                except PermissionError as exc:
                    st.error(f"Permission denied: {exc}")
                except ValueError as exc:
                    st.error(f"Validation error: {exc}")
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")

    # Soft delete (Super Admin only)
    if can(user, "delete_complaint"):
        st.markdown("---")
        with st.expander(f"Delete Complaint #{detail['incident_id']}"):
            st.warning(
                "Deleting removes this complaint from active dashboards while preserving its audit log."
            )
            version_suffix = f"{detail['id']}_{detail['updated_at']}"
            reason = st.text_input(
                "Reason for deletion (optional)",
                key=f"{key_prefix}_del_reason_{version_suffix}",
                placeholder="e.g. Duplicate submission",
            )
            confirmed = st.checkbox(
                f"Confirm deletion of {detail['incident_id']}",
                key=f"{key_prefix}_del_confirm_{version_suffix}",
            )
            if st.button("Delete Complaint", key=f"{key_prefix}_del_btn_{version_suffix}"):
                if not confirmed:
                    st.error("Please tick the confirmation checkbox before deleting.")
                else:
                    try:
                        delete_complaint(user, detail["id"], reason.strip())
                        st.session_state[flash_key] = f"Complaint {detail['incident_id']} deleted."
                        st.rerun()
                    except (PermissionError, ValueError) as exc:
                        st.error(str(exc))


def render_staff_dashboard(user: dict, key_prefix: str) -> None:
    """
    Shared complaint dashboard for Employee, Admin, and Super Admin.
    Runs automated escalation check on load, shows KPIs including Overdue,
    employee queue tabs / compact self-performance, Plotly charts, and filters.
    """
    run_escalation_check()

    flash_key = f"{key_prefix}_flash_msg"
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))

    try:
        complaints = get_complaints(user)
        all_departments = get_all_departments(user)
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return
    except Exception as exc:
        st.error(f"Could not load complaints: {exc}")
        return

    is_employee = user.get("role") == "employee"

    if is_employee:
        dept_label = user.get("department") or "Unassigned"
        st.caption(f"Department scope: {dept_label}")
        own_perf = get_employee_own_performance(user)
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Assigned Handled", own_perf["handled"])
        p2.metric("My Open", own_perf["open"])
        p3.metric("My Resolved", own_perf["resolved"])
        p4.metric(
            "My SLA Met",
            f"{own_perf['sla_met_pct']}%" if own_perf["sla_met_pct"] is not None else "N/A",
        )
        p5.metric(
            "Avg Rating",
            f"{own_perf['avg_rating']}/5" if own_perf["avg_rating"] is not None else "N/A",
        )

    # KPIs
    st.markdown("### Dashboard Overview")
    total = len(complaints)
    pending = sum(1 for c in complaints if c.get("status") == "Pending")
    high_priority = sum(1 for c in complaints if c.get("priority") == "High")
    overdue_cnt = sum(1 for c in complaints if c.get("is_overdue"))
    resolved = sum(1 for c in complaints if c.get("status") in ("Resolved", "Closed"))

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Complaints", total)
    col2.metric("Pending", pending)
    col3.metric("High Priority", high_priority)
    col4.metric("Overdue", overdue_cnt)
    col5.metric("Resolved / Closed", resolved)

    _render_analytics_charts(complaints)

    st.markdown("---")
    st.markdown("### Complaints List")

    queue_mode = "All"
    if is_employee:
        queue_mode = st.radio(
            "Queue View",
            options=["My Queue", "Unassigned", "All Department"],
            horizontal=True,
            key=f"{key_prefix}_queue_mode",
        )

    if is_employee:
        filter_col1, filter_col2 = st.columns([3, 1], vertical_alignment="bottom")
        selected_dept_ids = [user.get("department_id")] if user.get("department_id") else []
    else:
        filter_col1, filter_col_dept, filter_col2 = st.columns([2, 2, 1], vertical_alignment="bottom")
        dept_filter_options = [d["id"] for d in all_departments]
        dept_filter_labels = {
            d["id"]: (d["name"] if d["is_active"] else f"{d['name']} (Inactive)")
            for d in all_departments
        }
        with filter_col_dept:
            selected_dept_ids = st.multiselect(
                "Filter by Department",
                options=dept_filter_options,
                default=dept_filter_options,
                format_func=lambda did: dept_filter_labels.get(did, str(did)),
                key=f"{key_prefix}_dept_filter",
            )

    with filter_col1:
        selected_statuses = st.multiselect(
            "Filter by Status",
            options=STATUS_OPTIONS,
            default=STATUS_OPTIONS,
            key=f"{key_prefix}_status_filter",
        )
    with filter_col2:
        overdue_only = st.checkbox("Overdue only", key=f"{key_prefix}_overdue_only")

    search_query = st.text_input(
        "Search by description, Incident ID, Location, Department, or Assignee",
        key=f"{key_prefix}_search",
        placeholder="Type to filter...",
    )

    filtered = complaints
    if queue_mode == "My Queue":
        filtered = [c for c in filtered if c.get("assigned_to") == user.get("id")]
    elif queue_mode == "Unassigned":
        filtered = [c for c in filtered if c.get("assigned_to") is None]

    filtered = [c for c in filtered if c.get("status") in selected_statuses]
    if not is_employee:
        filtered = [c for c in filtered if c.get("department_id") in selected_dept_ids]
    if overdue_only:
        filtered = [c for c in filtered if c.get("is_overdue")]

    if search_query.strip():
        q = search_query.strip().lower()
        filtered = [
            c for c in filtered
            if q in (c.get("text") or "").lower()
            or q in (c.get("incident_id") or "").lower()
            or q in (c.get("department") or "").lower()
            or q in (c.get("location") or "").lower()
            or q in (c.get("assigned_username") or "unassigned").lower()
        ]

    page_size = 25
    total_filtered = len(filtered)
    total_pages = max(1, (total_filtered + page_size - 1) // page_size)
    page_key = f"{key_prefix}_page_num"
    cur_page = min(int(st.session_state.get(page_key, 1)), total_pages)
    if total_pages > 1:
        pcol1, pcol2 = st.columns([1, 3], vertical_alignment="center")
        with pcol1:
            cur_page = st.number_input(
                f"Page (1 - {total_pages})",
                min_value=1,
                max_value=total_pages,
                value=cur_page,
                step=1,
                key=page_key,
            )
        with pcol2:
            st.caption(f"Showing page {cur_page} of {total_pages} ({total_filtered} matching, {total} total)")
    start_idx = (int(cur_page) - 1) * page_size
    page_rows = filtered[start_idx : start_idx + page_size]

    st.dataframe(build_complaints_df(page_rows), width="stretch", hide_index=True)
    if total_pages <= 1:
        st.caption(f"Showing {total_filtered} of {total} complaints")

    st.markdown("---")
    st.markdown("### Complaint Detail and History")

    selectable = filtered if filtered else complaints
    incident_ids = [c.get("incident_id") for c in selectable if c.get("incident_id")]
    if not incident_ids:
        st.info("No complaints available in this view.")
        return

    select_key = f"{key_prefix}_detail_select"
    if select_key in st.session_state and st.session_state[select_key] not in incident_ids:
        st.session_state[select_key] = incident_ids[0]

    selected_id = st.selectbox(
        "Select a Complaint (Incident ID)",
        options=incident_ids,
        key=select_key,
    )

    matched = next((c for c in selectable if c.get("incident_id") == selected_id), None)
    if not matched:
        return

    try:
        detail = get_complaint_detail(user, matched["id"])
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return
    except Exception as exc:
        st.error(f"Could not load detail: {exc}")
        return

    render_complaint_card(detail)
    _render_ml_explainability_panel(detail, key_prefix)
    render_attachments(user, detail["id"], key_prefix)
    st.markdown("#### Audit History")
    render_history(detail["id"], user)

    _render_edit_and_delete_controls(user, detail, key_prefix)


def render_escalations_tab(user: dict, key_prefix: str) -> None:
    """Escalations dashboard for Admin and Super Admin sorted by overdue duration."""
    run_escalation_check()

    flash_key = f"{key_prefix}_esc_flash"
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))

    st.markdown("### Escalated and Overdue Complaints")
    try:
        all_complaints = get_complaints(user)
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return

    escalated = [
        c for c in all_complaints
        if c.get("status") not in ("Resolved", "Closed")
        and (c.get("escalation_level", 0) >= 1 or c.get("is_overdue"))
    ]
    escalated.sort(key=lambda c: float(c.get("overdue_hours", 0.0)), reverse=True)

    e1, e2, e3 = st.columns(3)
    e1.metric("Total Escalated / Overdue", len(escalated))
    e2.metric("Level 1 (<= 24h overdue)", sum(1 for c in escalated if c.get("escalation_level") == 1))
    e3.metric("Level 2 (> 24h overdue)", sum(1 for c in escalated if c.get("escalation_level") == 2))

    if not escalated:
        st.info("No complaints are currently overdue or escalated.")
        return

    st.dataframe(build_complaints_df(escalated), width="stretch", hide_index=True)

    incident_ids = [c["incident_id"] for c in escalated]
    sel_key = f"{key_prefix}_esc_select"
    if sel_key in st.session_state and st.session_state[sel_key] not in incident_ids:
        st.session_state[sel_key] = incident_ids[0]

    selected_inc = st.selectbox(
        "Select Escalated Complaint",
        options=incident_ids,
        key=sel_key,
    )
    detail = next(c for c in escalated if c["incident_id"] == selected_inc)
    render_complaint_card(detail)

    ver = f"{detail['id']}_{detail['updated_at']}_{detail['assigned_to']}"
    col_ack, col_reassign = st.columns(2)

    with col_ack:
        with st.form(key=f"{key_prefix}_esc_ack_form_{ver}"):
            ack_note = st.text_area(
                "Acknowledgement Note",
                placeholder="Enter action plan or acknowledgement note...",
                key=f"{key_prefix}_esc_ack_note_{ver}",
            )
            if st.form_submit_button("Log Acknowledgement Note", type="primary"):
                try:
                    acknowledge_escalation(user, detail["id"], ack_note.strip())
                    st.session_state[flash_key] = f"Acknowledgement logged for {detail['incident_id']}."
                    st.rerun()
                except (PermissionError, ValueError) as exc:
                    st.error(str(exc))

    with col_reassign:
        dept_emps = get_department_employees(user, detail["department_id"])
        emp_opts: list[int | None] = [None, *[e["id"] for e in dept_emps]]
        emp_lbls: dict[int | None, str] = {None: "Unassigned"}
        for e in dept_emps:
            emp_lbls[e["id"]] = f"{e['username']} ({e['full_name']}) - {e['open_count']} open"

        cur_emp = detail.get("assigned_to")
        if cur_emp not in emp_opts and cur_emp is not None:
            emp_opts.append(cur_emp)
            emp_lbls[cur_emp] = detail.get("assigned_username") or "Employee"

        with st.form(key=f"{key_prefix}_esc_assign_form_{ver}"):
            new_emp = st.selectbox(
                "Reassign Employee",
                options=emp_opts,
                index=emp_opts.index(cur_emp) if cur_emp in emp_opts else 0,
                format_func=lambda eid: emp_lbls[eid],
                key=f"{key_prefix}_esc_emp_{ver}",
            )
            assign_note = st.text_input(
                "Reassignment Note (optional)",
                key=f"{key_prefix}_esc_assign_note_{ver}",
            )
            if st.form_submit_button("Reassign Complaint"):
                try:
                    assign_complaint(user, detail["id"], new_emp, assign_note.strip())
                    st.session_state[flash_key] = f"Reassigned {detail['incident_id']}."
                    st.rerun()
                except (PermissionError, ValueError) as exc:
                    st.error(str(exc))


def render_performance_tab(user: dict) -> None:
    """Admin and Super Admin Performance Analytics tab (per department & per employee)."""
    st.markdown("### Performance Metrics")
    try:
        data = get_performance_metrics(user)
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return

    by_dept = data["by_department"]
    by_emp = data["by_employee"]

    st.markdown("#### Department Performance")
    dept_df = pd.DataFrame([
        {
            "Department":           d["department"] if d["department_is_active"] else f"{d['department']} (Inactive)",
            "Handled":              d["handled"],
            "Resolved / Closed":    d["resolved"],
            "Open":                 d["open"],
            "Avg Resolution (hrs)": d["avg_resolution_hours"],
            "SLA Met %":            d["sla_met_pct"],
            "Reopen Rate %":        d["reopen_rate_pct"],
            "Avg Rating":           d["avg_rating"],
        }
        for d in by_dept
    ])
    st.dataframe(dept_df, width="stretch", hide_index=True)

    active_dept_rows = [d for d in by_dept if d["handled"] > 0]
    if active_dept_rows:
        chart_df = pd.DataFrame(active_dept_rows)
        c1, c2 = st.columns(2)
        with c1:
            fig_vol = px.bar(
                chart_df,
                x="department",
                y=["resolved", "open"],
                barmode="group",
                title="Resolved vs Open by Department",
                labels={"value": "Complaints", "department": "Department", "variable": "State"},
            )
            st.plotly_chart(fig_vol, width="stretch", theme="streamlit")
        with c2:
            sla_df = chart_df.dropna(subset=["sla_met_pct"])
            if not sla_df.empty:
                fig_sla = px.bar(
                    sla_df,
                    x="department",
                    y="sla_met_pct",
                    text="sla_met_pct",
                    title="SLA Met (%) by Department",
                    labels={"sla_met_pct": "SLA Met (%)", "department": "Department"},
                )
                st.plotly_chart(fig_sla, width="stretch", theme="streamlit")

    st.markdown("---")
    st.markdown("#### Employee Performance")
    if not by_emp:
        st.info("No employee accounts found.")
        return

    emp_df = pd.DataFrame([
        {
            "Employee":             e["username"],
            "Full Name":            e["full_name"],
            "Department":           e["department"],
            "Active":               "Yes" if e["is_active"] else "No",
            "Handled":              e["handled"],
            "Resolved / Closed":    e["resolved"],
            "Open":                 e["open"],
            "Avg Resolution (hrs)": e["avg_resolution_hours"],
            "SLA Met %":            e["sla_met_pct"],
            "Reopen Rate %":        e["reopen_rate_pct"],
            "Avg Rating":           e["avg_rating"],
        }
        for e in by_emp
    ])
    st.dataframe(emp_df, width="stretch", hide_index=True)

    handled_emps = [e for e in by_emp if e["handled"] > 0]
    if handled_emps:
        emp_chart_df = pd.DataFrame(handled_emps)
        fig_emp = px.bar(
            emp_chart_df,
            x="username",
            y=["resolved", "open"],
            barmode="group",
            title="Complaints Handled by Employee",
            labels={"value": "Complaints", "username": "Employee", "variable": "State"},
        )
        st.plotly_chart(fig_emp, width="stretch", theme="streamlit")


def _render_ml_explainability_panel(detail: dict, key_prefix: str) -> None:
    """Display ML department suggestion, confidence, top contributing terms, and LIME attribution."""
    sugg = detail.get("ml_suggested_department")
    conf = detail.get("ml_confidence")
    terms = detail.get("ml_top_terms") or []
    emergency = has_emergency_keyword(detail.get("text", ""))

    if not sugg and not emergency:
        return

    with st.expander("AI Classification and Explainability"):
        c1, c2, c3 = st.columns(3)
        c1.metric("ML Suggested Department", sugg or "N/A")
        c2.metric("Confidence", f"{round(float(conf) * 100, 1)}%" if conf is not None else "N/A")
        c3.metric("Emergency Keyword Rule", "Triggered (High)" if emergency else "Not triggered")

        if detail.get("priority_reason"):
            st.info(f"Priority Engine Decision: {detail['priority_reason']}")
        if detail.get("duration_hours") is not None:
            st.caption(
                f"Extracted Duration: {detail['duration_hours']}h "
                f"('{detail.get('duration_matched_text', '')}', confidence={detail.get('duration_confidence')}) | "
                f"Recurring: {'Yes' if detail.get('is_recurring') else 'No'}"
            )

        if sugg and sugg.lower() != str(detail.get("department", "")).lower():
            st.warning(
                f"Citizen chose '{detail.get('department')}', while ML suggested '{sugg}'."
            )

        if terms:
            st.caption("Top weighted model features for predicted class:")
            term_df = pd.DataFrame(
                [{"Term / N-gram": t.get("term", ""), "Contribution Weight": t.get("weight", 0.0)} for t in terms]
            )
            st.dataframe(term_df, width="stretch", hide_index=True)

        lime_key = f"{key_prefix}_lime_cache_{detail['id']}"
        if st.button("Compute LIME Word Attribution", key=f"{key_prefix}_lime_btn_{detail['id']}"):
            st.session_state[lime_key] = explain_with_lime(detail.get("text", ""), num_features=8)

        if lime_key in st.session_state:
            lime_terms = st.session_state[lime_key]
            if lime_terms:
                lime_df = pd.DataFrame(
                    [{"Word / Phrase": item["term"], "LIME Weight": item["weight"]} for item in lime_terms]
                )
                fig_lime = px.bar(
                    lime_df,
                    x="LIME Weight",
                    y="Word / Phrase",
                    orientation="h",
                    title="LIME Local Explanation",
                )
                fig_lime.update_layout(height=280)
                st.plotly_chart(fig_lime, width="stretch", theme="streamlit")
            else:
                st.caption("LIME attribution unavailable in fallback mode.")


def render_review_queue_tab(user: dict, key_prefix: str) -> None:
    """Paginated Review Queue for Admin and Super Admin."""
    if not can(user, "view_review_queue"):
        st.error("Permission denied.")
        return

    flash_key = f"{key_prefix}_rq_flash"
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))

    st.markdown("### AI Review Queue")
    st.caption(
        "Complaints flagged for low ML confidence, citizen-vs-ML department disagreement, "
        "photo disagreement, or suspected spam."
    )

    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        status_filter = st.selectbox(
            "Queue Status",
            options=["Pending", "Resolved", "All"],
            index=0,
            key=f"{key_prefix}_rq_status_filter",
        )
    with col_f2:
        page_num = int(
            st.number_input(
                "Page",
                min_value=1,
                value=1,
                step=1,
                key=f"{key_prefix}_rq_page",
            )
        )

    page_size = 20
    offset = (page_num - 1) * page_size
    items = get_review_queue(
        user,
        status_filter=None if status_filter == "All" else status_filter,
        limit=page_size,
        offset=offset,
    )

    if not items:
        st.info("No review items match the selected filter.")
        return

    df_rq = pd.DataFrame([
        {
            "Review ID": it["id"],
            "Incident ID": it["incident_id"],
            "Reason": it["reason"],
            "Citizen Dept": it["citizen_department"],
            "ML Suggested": it.get("ml_suggested_department") or "",
            "Confidence": round(float(it["ml_confidence"]), 3) if it.get("ml_confidence") is not None else None,
            "Current Dept": it["current_department_name"],
            "Status": it["status"],
            "Action": it.get("resolution_action") or "",
            "Created (UTC)": it["created_at"],
        }
        for it in items
    ])
    st.dataframe(df_rq, width="stretch", hide_index=True)

    pending_items = [it for it in items if it["status"] == "Pending"]
    if not pending_items or not can(user, "resolve_review_item"):
        return

    st.markdown("---")
    st.markdown("#### Resolve Flagged Item")
    selected_rq_id = st.selectbox(
        "Select Pending Review Item",
        options=[it["id"] for it in pending_items],
        format_func=lambda rid: next(
            f"#{x['incident_id']} - {x['reason']} (Citizen: {x['citizen_department']} vs ML: {x.get('ml_suggested_department') or 'N/A'})"
            for x in pending_items if x["id"] == rid
        ),
        key=f"{key_prefix}_rq_select",
    )
    selected_item = next(x for x in pending_items if x["id"] == selected_rq_id)
    st.info(f"Complaint text: {selected_item['complaint_text']}")

    active_depts = get_active_departments()
    dept_ids = [d["id"] for d in active_depts]
    dept_labels = {d["id"]: d["name"] for d in active_depts}

    note_val = st.text_input(
        "Resolution Note (optional)",
        key=f"{key_prefix}_rq_note_{selected_item['id']}",
        placeholder="Reason for keeping citizen choice or rerouting...",
    )
    prio_override = st.selectbox(
        "Optional Priority Correction (saved for continuous ML training)",
        options=["Keep Current", "High", "Medium", "Low"],
        key=f"{key_prefix}_rq_prio_{selected_item['id']}",
    )
    corr_prio = None if prio_override == "Keep Current" else prio_override

    c_b1, c_b2, c_b3 = st.columns([1, 1, 2])
    with c_b1:
        if st.button(
            f"Keep '{selected_item['citizen_department']}'",
            key=f"{key_prefix}_rq_keep_{selected_item['id']}",
            width="stretch",
        ):
            try:
                resolve_review_item(
                    user, selected_item["id"], "Keep Citizen", note=note_val, corrected_priority=corr_prio
                )
                st.session_state[flash_key] = f"Kept citizen department for #{selected_item['incident_id']}."
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    with c_b2:
        ml_sugg = selected_item.get("ml_suggested_department") or ""
        if st.button(
            f"Accept ML ({ml_sugg or 'N/A'})",
            key=f"{key_prefix}_rq_accept_{selected_item['id']}",
            type="primary",
            disabled=not bool(ml_sugg),
            width="stretch",
        ):
            try:
                resolve_review_item(
                    user, selected_item["id"], "Accept ML", note=note_val, corrected_priority=corr_prio
                )
                st.session_state[flash_key] = f"Accepted ML department '{ml_sugg}' for #{selected_item['incident_id']}."
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    with c_b3:
        if dept_ids:
            custom_dept_id = st.selectbox(
                "Or Select Other Department",
                options=dept_ids,
                format_func=lambda did: dept_labels[did],
                key=f"{key_prefix}_rq_custom_dept_{selected_item['id']}",
            )
            if st.button(
                "Apply Custom Department",
                key=f"{key_prefix}_rq_custom_btn_{selected_item['id']}",
            ):
                try:
                    resolve_review_item(
                        user,
                        selected_item["id"],
                        "Custom Department",
                        target_department_id=custom_dept_id,
                        note=note_val,
                        corrected_priority=corr_prio,
                    )
                    st.session_state[flash_key] = (
                        f"Reassigned #{selected_item['incident_id']} to '{dept_labels[custom_dept_id]}'."
                    )
                    st.rerun()
                except (PermissionError, ValueError) as exc:
                    st.error(str(exc))

