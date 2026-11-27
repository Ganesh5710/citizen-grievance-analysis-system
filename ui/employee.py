"""
ui/employee.py
Employee page for the Citizen Grievance System.
Employees can view all complaints AND are the ONLY role allowed to update status/remarks.
"""

import html

import streamlit as st

from core.complaints import update_complaint
from ui.shared import SHARED_CSS, STATUS_OPTIONS, render_staff_dashboard


def render_employee_page(user: dict) -> None:
    st.markdown(SHARED_CSS, unsafe_allow_html=True)

    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    st.markdown(
        f"""
        <div class="top-line"></div>
        <h2 style="margin:0 0 4px;">🏢 Employee Dashboard</h2>
        <p style="color:#94a3b8;margin:0 0 24px;">Logged in as <b>{display_name}</b></p>
        """,
        unsafe_allow_html=True,
    )

    selected_complaint = render_staff_dashboard(
        user,
        key_prefix="emp",
        show_readonly_banner=False,
    )

    if selected_complaint is None:
        return

    st.markdown("---")
    st.markdown(f"### ✏️ Update Complaint `#{selected_complaint['incident_id']}`")

    current_status = selected_complaint.get("status", "Pending")
    default_idx = STATUS_OPTIONS.index(current_status) if current_status in STATUS_OPTIONS else 0

    with st.form(key=f"emp_update_form_{selected_complaint['id']}"):
        new_status = st.selectbox(
            "New Status",
            options=STATUS_OPTIONS,
            index=default_idx,
            key=f"emp_new_status_{selected_complaint['id']}",
        )
        remark = st.text_area(
            "Remark (visible to citizen)",
            placeholder="Add notes or resolution details…",
            key=f"emp_remark_{selected_complaint['id']}",
        )
        save_btn = st.form_submit_button("💾 Save Update")

    if save_btn:
        try:
            update_complaint(user, selected_complaint["id"], new_status, remark.strip())
            st.success(
                f"✅ Complaint **{selected_complaint['incident_id']}** updated to **{new_status}**."
            )
            st.rerun()
        except PermissionError as exc:
            st.error(f"🚫 Permission denied: {exc}")
        except ValueError as exc:
            st.error(f"⚠️ Validation error: {exc}")
        except Exception as exc:
            st.error(f"❌ Unexpected error: {exc}")
