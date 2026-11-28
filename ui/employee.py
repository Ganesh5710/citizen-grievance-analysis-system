"""
ui/employee.py
Employee page: scoped to employee's own department with My Queue / Unassigned views,
claimable unassigned complaints, and status/remark updates only on assigned complaints.
"""

import html

import streamlit as st

from ui.shared import render_forecast_and_predictions_tab, render_html, render_staff_dashboard


def render_employee_page(user: dict) -> None:
    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    dept_name = html.escape(str(user.get("department") or "Unassigned"))
    scope_bits = [
        f"Logged in as <b>{display_name}</b>",
        f"Department: <b>{dept_name}</b>",
    ]
    if user.get("district_name"):
        scope_bits.append(f"District: <b>{html.escape(str(user['district_name']))}</b>")
    if user.get("mandal_name"):
        scope_bits.append(f"Mandal: <b>{html.escape(str(user['mandal_name']))}</b>")
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Employee Dashboard</h2>
        <p class="cgs-muted" style="margin:0 0 24px;">
            {" &nbsp;|&nbsp; ".join(scope_bits)}
        </p>
        """
    )

    tab_tasks, tab_forecast = st.tabs(["My Complaints & Queue", "Forecast and Predictions"])
    with tab_tasks:
        render_staff_dashboard(user, key_prefix="emp")
    with tab_forecast:
        render_forecast_and_predictions_tab(user, key_prefix="emp")
