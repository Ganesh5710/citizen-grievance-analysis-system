"""
ui/citizen.py
Citizen (role='user') portal:
  Tab 1: Two-step department selection -> complaint form with optional photo attachments.
  Tab 2: My Complaints table, detail, photo additions (while open), and Resolved feedback/reopen.
"""

from datetime import datetime, timezone
import html

import streamlit as st

from core.complaints import (
    get_complaint_detail,
    get_complaint_feedback,
    get_complaints,
    submit_complaint,
    submit_feedback,
)
from core.departments import get_active_departments
from core.images import MAX_IMAGES_PER_COMPLAINT, add_images
from core.sla import AUTO_CLOSE_DAYS, parse_utc
from ui.shared import (
    build_complaints_df,
    render_attachments,
    render_complaint_card,
    render_history,
    render_html,
)


def _render_department_selection(active_depts: list[dict]) -> None:
    """Step 1: Responsive card grid of active departments."""
    st.markdown("### Step 1: Select a Department")
    st.caption("Choose the department relevant to your grievance.")

    if not active_depts:
        st.warning("No active departments are currently available. Please try again later.")
        return

    cols_per_row = 3
    for start in range(0, len(active_depts), cols_per_row):
        row_depts = active_depts[start : start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, dept in zip(cols, row_depts):
            with col:
                name_esc = html.escape(dept["name"])
                desc_esc = html.escape(dept["description"])
                render_html(
                    f"""
                    <div class="cgs-card cgs-dept-card">
                        <div>
                            <h4 style="margin:0 0 8px 0;">{name_esc}</h4>
                            <p class="cgs-muted" style="font-size:14px;margin:0;">{desc_esc}</p>
                        </div>
                    </div>
                    """
                )
                if st.button(
                    "Raise Complaint",
                    key=f"citizen_choose_dept_{dept['id']}",
                    type="primary",
                    width="stretch",
                ):
                    st.session_state["citizen_selected_dept_id"] = dept["id"]
                    st.rerun()


def _render_complaint_form(user: dict, dept: dict) -> None:
    """Step 2: Complaint form for the chosen department, including optional photos."""
    col_hdr, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_hdr:
        st.markdown(f"### Step 2: File Complaint - {dept['name']}")
        st.caption(dept["description"])
    with col_btn:
        if st.button("Change department", key="citizen_change_dept_btn", width="stretch"):
            st.session_state.pop("citizen_selected_dept_id", None)
            st.rerun()

    form_gen = st.session_state.get("citizen_form_gen", 0)

    # clear_on_submit=False preserves typed text when validation fails
    with st.form(key=f"citizen_submit_form_{form_gen}", clear_on_submit=False):
        complaint_text = st.text_area(
            "Complaint Description (required, min 10 characters)",
            placeholder="Describe the issue clearly...",
            height=150,
            key=f"citizen_complaint_desc_{form_gen}",
        )
        location = st.text_input(
            "Location / Landmark (required)",
            placeholder="e.g. Main Street near City Hall",
            key=f"citizen_complaint_loc_{form_gen}",
        )
        uploaded_files = st.file_uploader(
            "Attach photos (optional, max 3 images, JPG/PNG/WebP, max 5 MB each)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key=f"citizen_upload_{form_gen}",
        )
        st.caption("Privacy notice: EXIF and GPS location data are automatically removed from uploaded photos.")
        submitted = st.form_submit_button("Submit Complaint", type="primary")

    if uploaded_files:
        st.caption("Photo preview:")
        preview_cols = st.columns(min(len(uploaded_files), 3))
        for pcol, fobj in zip(preview_cols, uploaded_files[:MAX_IMAGES_PER_COMPLAINT]):
            with pcol:
                try:
                    st.image(fobj.getvalue(), caption=fobj.name, width="stretch")
                except Exception:
                    st.caption(f"Preview unavailable: {fobj.name}")

    if submitted:
        text_stripped = complaint_text.strip()
        loc_stripped = location.strip()

        if len(text_stripped) < 10:
            st.error("Please describe your complaint in at least 10 characters.")
            return
        if len(loc_stripped) < 2:
            st.error("Location / Landmark is required.")
            return
        if uploaded_files and len(uploaded_files) > MAX_IMAGES_PER_COMPLAINT:
            st.error(f"You can attach at most {MAX_IMAGES_PER_COMPLAINT} photos per complaint.")
            return

        try:
            incident_id = submit_complaint(
                user,
                dept["id"],
                text_stripped,
                loc_stripped,
                files=uploaded_files or None,
            )
            saved = next(
                (c for c in get_complaints(user) if c["incident_id"] == incident_id),
                None,
            )
            priority_text = saved["priority"] if saved else "Assigned"
            st.session_state["citizen_last_ticket"] = {
                "incident_id": incident_id,
                "department":  dept["name"],
                "priority":    priority_text,
            }
            st.session_state.pop("citizen_selected_dept_id", None)
            st.session_state["citizen_form_gen"] = form_gen + 1
            st.rerun()
        except PermissionError as exc:
            st.error(f"Permission denied: {exc}")
        except ValueError as exc:
            st.error(f"Validation error: {exc}")
        except Exception as exc:
            st.error(f"Unexpected error: {exc}")


def _render_citizen_feedback_section(user: dict, detail: dict) -> None:
    """Display feedback history and, for Resolved complaints within 7 days, Confirm/Reopen form."""
    feedback_list = get_complaint_feedback(user, detail["id"])
    if feedback_list:
        st.markdown("#### Resolution Feedback History")
        for fb in feedback_list:
            outcome = "Confirmed Resolved" if fb["accepted"] else "Marked Not Resolved (Reopened)"
            comment_esc = html.escape(fb["comment"] or "No comment").replace("\n", "<br/>")
            render_html(
                f"""
                <div class="cgs-card">
                    <b>{html.escape(outcome)}</b> &nbsp;|&nbsp; Rating: {fb['rating']}/5
                    <br/><small class="cgs-muted">Submitted on {html.escape(fb['created_at'])} UTC</small>
                    <p style="margin-top:6px;margin-bottom:0;">{comment_esc}</p>
                </div>
                """
            )

    if detail.get("status") != "Resolved":
        return

    current_cycle = int(detail.get("reopen_count", 0))
    already_responded = any(fb["resolution_cycle"] == current_cycle for fb in feedback_list)
    if already_responded:
        return

    resolved_dt = parse_utc(detail.get("resolved_at"))
    if resolved_dt is not None:
        age_secs = (datetime.now(timezone.utc) - resolved_dt).total_seconds()
        if age_secs > AUTO_CLOSE_DAYS * 86400:
            st.info("The 7-day feedback window for this resolution has expired.")
            return

    st.markdown("#### Resolution Confirmation")
    st.info(
        "This complaint has been marked Resolved. Confirm resolution to close it, "
        "or report it as not resolved within 7 days to reopen it."
    )

    ver = f"{detail['id']}_{current_cycle}"
    rating = st.slider(
        "Satisfaction Rating (1 = Poor, 5 = Excellent)",
        min_value=1,
        max_value=5,
        value=5,
        key=f"citizen_fb_rating_{ver}",
    )
    fb_comment = st.text_area(
        "Comment (optional when confirming; required when reopening)",
        placeholder="Share your feedback or explain why the issue persists...",
        key=f"citizen_fb_comment_{ver}",
    )

    col_ok, col_reopen = st.columns(2)
    with col_ok:
        if st.button("Confirm resolved", key=f"citizen_fb_confirm_{ver}", type="primary", width="stretch"):
            try:
                submit_feedback(user, detail["id"], accepted=True, rating=int(rating), comment=fb_comment.strip())
                st.session_state["citizen_my_flash"] = (
                    f"Complaint {detail['incident_id']} confirmed resolved and closed."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    with col_reopen:
        if st.button("Not resolved", key=f"citizen_fb_reopen_{ver}", width="stretch"):
            try:
                submit_feedback(user, detail["id"], accepted=False, rating=int(rating), comment=fb_comment.strip())
                st.session_state["citizen_my_flash"] = (
                    f"Complaint {detail['incident_id']} reopened and returned for follow-up."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))


def render_citizen_page(user: dict) -> None:
    display_name = html.escape(str(user.get("full_name", user.get("username", "Citizen"))))
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Welcome, <span class="cgs-accent">{display_name}</span></h2>
        <p class="cgs-muted" style="margin:0 0 24px;">Citizen Portal - Submit and track your grievances</p>
        """
    )

    tab_submit, tab_my = st.tabs(["Submit Complaint", "My Complaints"])

    with tab_submit:
        if "citizen_last_ticket" in st.session_state:
            ticket = st.session_state.pop("citizen_last_ticket")
            st.success(
                f"Complaint submitted. Ticket ID: {ticket['incident_id']} | "
                f"Department: {ticket['department']} | "
                f"Detected Priority: {ticket['priority']}."
            )

        active_depts = get_active_departments()
        selected_dept_id = st.session_state.get("citizen_selected_dept_id")
        selected_dept = next((d for d in active_depts if d["id"] == selected_dept_id), None)

        if selected_dept is None:
            if selected_dept_id is not None:
                st.session_state.pop("citizen_selected_dept_id", None)
            _render_department_selection(active_depts)
        else:
            _render_complaint_form(user, selected_dept)

    with tab_my:
        if "citizen_my_flash" in st.session_state:
            st.success(st.session_state.pop("citizen_my_flash"))

        st.markdown("### My Submitted Complaints")
        try:
            complaints = get_complaints(user)
        except PermissionError as exc:
            st.error(f"Permission denied: {exc}")
            return
        except Exception as exc:
            st.error(f"Could not load complaints: {exc}")
            return

        if not complaints:
            st.info("You have not submitted any complaints yet. Use the Submit Complaint tab to get started.")
            return

        total = len(complaints)
        pending = sum(1 for c in complaints if c.get("status") == "Pending")
        resolved = sum(1 for c in complaints if c.get("status") in ("Resolved", "Closed"))

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Submitted", total)
        col2.metric("Pending", pending)
        col3.metric("Resolved / Closed", resolved)

        st.markdown("---")
        st.dataframe(build_complaints_df(complaints), width="stretch", hide_index=True)

        st.markdown("---")
        st.markdown("### Complaint Detail and History")

        incident_ids = [c.get("incident_id") for c in complaints if c.get("incident_id")]
        select_key = "citizen_detail_select"
        if select_key in st.session_state and st.session_state[select_key] not in incident_ids:
            st.session_state[select_key] = incident_ids[0]

        selected_id = st.selectbox(
            "Select an Incident ID to view status, photos, and remarks",
            options=incident_ids,
            key=select_key,
        )

        if selected_id:
            matched = next((c for c in complaints if c.get("incident_id") == selected_id), None)
            if matched:
                try:
                    detail = get_complaint_detail(user, matched["id"])
                except PermissionError as exc:
                    st.error(f"Permission denied: {exc}")
                    return
                except Exception as exc:
                    st.error(f"Could not load complaint detail: {exc}")
                    return

                render_complaint_card(detail)
                render_attachments(user, detail["id"], "citizen_my")

                remaining = MAX_IMAGES_PER_COMPLAINT - int(detail.get("photo_count", 0))
                if detail.get("status") not in ("Resolved", "Closed") and remaining > 0:
                    more_gen = st.session_state.get("citizen_more_upload_gen", 0)
                    with st.expander(f"Add more photos ({remaining} remaining)"):
                        more_files = st.file_uploader(
                            "Select additional photos (JPG/PNG/WebP, max 5 MB each)",
                            type=["jpg", "jpeg", "png", "webp"],
                            accept_multiple_files=True,
                            key=f"citizen_more_photos_{detail['id']}_{more_gen}",
                        )
                        st.caption("Privacy notice: EXIF and GPS location data are removed automatically.")
                        if st.button("Upload Photos", key=f"citizen_more_btn_{detail['id']}_{more_gen}"):
                            if not more_files:
                                st.error("Select at least one photo to upload.")
                            else:
                                try:
                                    add_images(user, detail["id"], more_files)
                                    st.session_state["citizen_more_upload_gen"] = more_gen + 1
                                    st.session_state["citizen_my_flash"] = "Photos attached successfully."
                                    st.rerun()
                                except (PermissionError, ValueError) as exc:
                                    st.error(str(exc))

                _render_citizen_feedback_section(user, detail)
                st.markdown("#### Status History and Remarks")
                render_history(detail["id"], user)
