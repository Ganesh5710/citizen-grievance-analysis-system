"""
ui/citizen.py
Citizen (role='user') page for the Citizen Grievance System.
"""

import html

import streamlit as st

from core.complaints import (
    get_complaint_detail,
    get_complaints,
    submit_complaint,
)
from ui.shared import (
    SHARED_CSS,
    build_complaints_df,
    render_complaint_card,
    render_history,
)


def render_citizen_page(user: dict) -> None:
    st.markdown(SHARED_CSS, unsafe_allow_html=True)

    display_name = html.escape(str(user.get("full_name", user.get("username", "Citizen"))))
    st.markdown(
        f"""
        <div class="top-line"></div>
        <h2 style="margin:0 0 4px;">👋 Welcome, <span style="color:#38bdf8;">{display_name}</span></h2>
        <p style="color:#94a3b8;margin:0 0 24px;">Citizen Portal — Submit and track your grievances</p>
        """,
        unsafe_allow_html=True,
    )

    tab_submit, tab_my = st.tabs(["📝 Submit Complaint", "📋 My Complaints"])

    # -----------------------------------------------------------------------
    # TAB 1 — Submit Complaint
    # -----------------------------------------------------------------------
    with tab_submit:
        st.markdown("### File a New Grievance")
        with st.form(key="citizen_submit_form", clear_on_submit=True):
            complaint_text = st.text_area(
                "Describe your complaint *",
                placeholder="Please describe your issue in detail (minimum 10 characters)…",
                height=160,
                key="citizen_complaint_text",
            )
            location = st.text_input(
                "Location / Landmark (optional)",
                placeholder="e.g. Main Street near City Hall",
                key="citizen_location",
            )
            submitted = st.form_submit_button("🚀 Submit Complaint")

        if submitted:
            text_stripped = complaint_text.strip()
            if len(text_stripped) < 10:
                st.error("⚠️ Please describe your complaint in at least 10 characters.")
            else:
                try:
                    incident_id = submit_complaint(user, text_stripped, location.strip())
                    st.success(
                        f"✅ Ticket raised! Your Incident ID: **{incident_id}**. "
                        "Save this ID to track your status in the 'My Complaints' tab."
                    )
                except PermissionError as exc:
                    st.error(f"🚫 Permission denied: {exc}")
                except ValueError as exc:
                    st.error(f"⚠️ Validation error: {exc}")
                except Exception as exc:
                    st.error(f"❌ Unexpected error: {exc}")

    # -----------------------------------------------------------------------
    # TAB 2 — My Complaints
    # -----------------------------------------------------------------------
    with tab_my:
        st.markdown("### My Submitted Complaints")
        try:
            complaints = get_complaints(user)
        except PermissionError as exc:
            st.error(f"🚫 Permission denied: {exc}")
            return
        except Exception as exc:
            st.error(f"❌ Could not load complaints: {exc}")
            return

        if not complaints:
            st.info("You haven't submitted any complaints yet. Use the 'Submit Complaint' tab to get started.")
            return

        total = len(complaints)
        pending = sum(1 for c in complaints if c.get("status") == "Pending")
        resolved = sum(1 for c in complaints if c.get("status") == "Resolved")

        col1, col2, col3 = st.columns(3)
        col1.metric("📋 Total Submitted", total)
        col2.metric("⏳ Pending", pending)
        col3.metric("✅ Resolved", resolved)

        st.markdown("---")
        st.dataframe(build_complaints_df(complaints), width="stretch", hide_index=True)

        st.markdown("---")
        st.markdown("### 🔍 Complaint Detail & History")

        incident_ids = [c.get("incident_id") for c in complaints if c.get("incident_id")]
        selected_id = st.selectbox(
            "Select an Incident ID to view status & employee remarks:",
            options=incident_ids,
            key="citizen_detail_select",
        )

        if selected_id:
            matched = next((c for c in complaints if c.get("incident_id") == selected_id), None)
            if matched:
                try:
                    detail = get_complaint_detail(user, matched["id"])
                except PermissionError as exc:
                    st.error(f"🚫 Permission denied: {exc}")
                    return
                except Exception as exc:
                    st.error(f"❌ Could not load complaint detail: {exc}")
                    return

                render_complaint_card(detail)
                st.markdown("#### 📜 Status History & Remarks")
                render_history(detail["id"], user)
