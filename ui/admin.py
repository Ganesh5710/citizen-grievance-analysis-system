"""
ui/admin.py
Admin portal:
  - Complaints tab (full edit: status, remark, department, priority, assign/reassign, remove image)
  - Escalations tab (overdue queue sorted by overdue duration, acknowledgement, reassign)
  - Performance tab (per-department and per-employee metrics)
"""

import html

import streamlit as st

from ui.shared import (
    render_escalations_tab,
    render_html,
    render_performance_tab,
    render_staff_dashboard,
)


def render_admin_page(user: dict) -> None:
    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Admin Dashboard</h2>
        <p class="cgs-muted" style="margin:0 0 24px;">Logged in as <b>{display_name}</b></p>
        """
    )

    tab_complaints, tab_escalations, tab_perf = st.tabs([
        "Complaints",
        "Escalations",
        "Performance",
    ])

    with tab_complaints:
        render_staff_dashboard(user, key_prefix="adm")

    with tab_escalations:
        render_escalations_tab(user, key_prefix="adm")

    with tab_perf:
        render_performance_tab(user)
