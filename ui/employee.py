"""
ui/employee.py
Employee page: scoped to employee's own department with My Queue / Unassigned views,
claimable unassigned complaints, and status/remark updates only on assigned complaints.
"""

import html

import streamlit as st

from ui.shared import render_html, render_staff_dashboard


def render_employee_page(user: dict) -> None:
    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    dept_name = html.escape(str(user.get("department") or "Unassigned"))
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Employee Dashboard</h2>
        <p class="cgs-muted" style="margin:0 0 24px;">
            Logged in as <b>{display_name}</b> &nbsp;|&nbsp; Department: <b>{dept_name}</b>
        </p>
        """
    )

    render_staff_dashboard(user, key_prefix="emp")
