"""
ui/admin.py
Admin portal:
  - Complaints tab (full edit: status, remark, department, priority, assign/reassign, remove image)
  - Escalations tab (overdue queue sorted by overdue duration, acknowledgement, reassign)
  - Performance tab (per-department and per-employee metrics)
"""

import html

import streamlit as st

from core.auth import get_user_jurisdictions
from ui.shared import (
    render_escalations_tab,
    render_forecast_and_predictions_tab,
    render_html,
    render_performance_tab,
    render_review_queue_tab,
    render_staff_dashboard,
)


def render_admin_page(user: dict) -> None:
    from analytics.anomaly import detect_anomalies
    detect_anomalies(notify=True)

    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    jurs = get_user_jurisdictions(user["id"]) if user.get("id") else []
    dist_names = sorted({j["district_name"] for j in jurs if j.get("district_name")})
    jur_str = html.escape(", ".join(dist_names)) if dist_names else "Legacy Unscoped / All"
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Admin Dashboard</h2>
        <p class="cgs-muted" style="margin:0 0 24px;">
            Logged in as <b>{display_name}</b> &nbsp;|&nbsp; Jurisdiction: <b>{jur_str}</b>
        </p>
        """
    )

    tab_complaints, tab_review, tab_escalations, tab_perf, tab_forecast = st.tabs([
        "Complaints",
        "Review Queue",
        "Escalations",
        "Performance",
        "Forecast and Predictions",
    ])

    with tab_complaints:
        render_staff_dashboard(user, key_prefix="adm")

    with tab_review:
        render_review_queue_tab(user, key_prefix="adm")

    with tab_escalations:
        render_escalations_tab(user, key_prefix="adm")

    with tab_perf:
        render_performance_tab(user)

    with tab_forecast:
        render_forecast_and_predictions_tab(user, key_prefix="adm")
