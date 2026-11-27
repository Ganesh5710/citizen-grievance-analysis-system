"""
ui/staff_readonly.py
Read-only admin dashboard for the Citizen Grievance System.
Role: 'admin' — can view all complaints + history, but CANNOT edit anything.
"""

import html

import streamlit as st

from ui.shared import SHARED_CSS, render_staff_dashboard


def render_readonly_page(user: dict) -> None:
    st.markdown(SHARED_CSS, unsafe_allow_html=True)

    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    st.markdown(
        f"""
        <div class="top-line"></div>
        <h2 style="margin:0 0 4px;">🛡️ Admin Dashboard</h2>
        <p style="color:#94a3b8;margin:0 0 24px;">Logged in as <b>{display_name}</b> (Admin)</p>
        """,
        unsafe_allow_html=True,
    )

    render_staff_dashboard(
        user,
        key_prefix="admin_ro",
        show_readonly_banner=True,
    )
