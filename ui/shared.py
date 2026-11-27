"""
Shared UI theme, helpers, Plotly charts, and read-only dashboard components
used across the Citizen, Employee, Admin, and Super Admin pages.
"""

import html

import pandas as pd
import plotly.express as px
import streamlit as st

from core.complaints import (
    get_complaint_detail,
    get_complaint_history,
    get_complaints,
)

STATUS_OPTIONS = ["Pending", "Assigned", "In Progress", "Resolved"]
PRIORITY_COLORS = {"High": "#ef4444", "Medium": "#f59e0b", "Low": "#22c55e"}
STATUS_COLORS = {
    "Pending": "#94a3b8",
    "Assigned": "#3b82f6",
    "In Progress": "#f59e0b",
    "Resolved": "#22c55e",
}

SHARED_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Poppins', sans-serif; }
.stApp {
    background:
        radial-gradient(circle at top left, #1d4ed8 0%, #0f172a 40%),
        linear-gradient(to bottom right, #020617, #111827);
    color: white;
}
div[data-testid="stMetric"] {
    background: rgba(15,23,42,0.75);
    border: 1px solid rgba(255,255,255,0.06);
    padding: 20px;
    border-radius: 20px;
    backdrop-filter: blur(14px);
    box-shadow: 0 10px 30px rgba(0,0,0,0.25);
}
.stButton > button {
    width: 100%;
    height: 48px;
    border: none;
    border-radius: 14px;
    background: linear-gradient(135deg, #2563eb, #3b82f6);
    color: white;
    font-size: 15px;
    font-weight: 700;
}
.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 10px 25px rgba(59,130,246,0.35);
}
textarea, .stTextInput input {
    border-radius: 12px !important;
    background: rgba(15,23,42,0.92) !important;
    color: white !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
}
.card {
    background: rgba(15,23,42,0.72);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 20px;
    padding: 22px;
    margin-bottom: 16px;
}
.top-line {
    height: 4px;
    border-radius: 4px;
    background: linear-gradient(to right, #2563eb, #38bdf8, #8b5cf6);
    margin-bottom: 16px;
}
.top-bar {
    background: rgba(15,23,42,0.80);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 20px;
    padding: 18px 28px;
    margin-bottom: 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
#MainMenu, footer, header { visibility: hidden; }
</style>
"""


def build_complaints_df(complaints: list[dict]) -> pd.DataFrame:
    """Build a display DataFrame excluding internal user_id."""
    display_keys = [
        "incident_id", "text", "location", "department",
        "priority", "status", "created_at", "updated_at",
    ]
    rows = [{k: c.get(k, "") for k in display_keys} for c in complaints]
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=display_keys)
    df.columns = [col.replace("_", " ").title() for col in df.columns]
    return df


def render_complaint_card(detail: dict) -> None:
    """Render a styled complaint detail card with HTML-escaped user inputs."""
    status = detail.get("status", "Pending")
    priority = detail.get("priority", "Low")
    s_color = STATUS_COLORS.get(status, "#94a3b8")
    p_color = PRIORITY_COLORS.get(priority, "#22c55e")

    incident_esc = html.escape(str(detail.get("incident_id", "")))
    dept_esc = html.escape(str(detail.get("department", "—")))
    loc_esc = html.escape(str(detail.get("location") or "—"))
    created_esc = html.escape(str(detail.get("created_at", "—")))
    updated_esc = html.escape(str(detail.get("updated_at", "—")))
    text_esc = html.escape(str(detail.get("text", "")))

    st.markdown(
        f"""
        <div class="card">
            <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
                <span style="font-size:18px;font-weight:700;">#{incident_esc}</span>
                <span>
                    <span style="background:{s_color};padding:4px 12px;border-radius:8px;font-size:12px;font-weight:600;">{html.escape(status)}</span>
                    &nbsp;
                    <span style="background:{p_color};padding:4px 12px;border-radius:8px;font-size:12px;font-weight:600;">{html.escape(priority)} Priority</span>
                </span>
            </div>
            <p><b>Department:</b> {dept_esc}</p>
            <p><b>Location:</b> {loc_esc}</p>
            <p><b>Filed:</b> {created_esc}</p>
            <p><b>Last Updated:</b> {updated_esc}</p>
            <hr style="border-color:rgba(255,255,255,0.08);"/>
            <p style="color:#cbd5e1;">{text_esc}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_history(complaint_id: int, user: dict) -> None:
    """Render chronological status update audit cards for a complaint."""
    try:
        history = get_complaint_history(user, complaint_id)
    except PermissionError as exc:
        st.error(f"🚫 Permission denied: {exc}")
        return
    except Exception as exc:
        st.warning(f"Could not load history: {exc}")
        return

    if not history:
        st.info("No status updates recorded yet.")
        return

    for entry in history:
        old_s = str(entry.get("old_status") or "—")
        new_s = str(entry.get("new_status") or "—")
        old_color = STATUS_COLORS.get(old_s, "#94a3b8")
        new_color = STATUS_COLORS.get(new_s, "#22c55e")
        remark = html.escape(str(entry.get("remark") or "No remark"))
        who = html.escape(str(entry.get("employee_username", "Staff")))
        when = html.escape(str(entry.get("created_at", "")))
        st.markdown(
            f"""
            <div class="card">
                <span style="background:{old_color};padding:3px 10px;border-radius:8px;font-size:12px;font-weight:600;">{html.escape(old_s)}</span>
                &nbsp;→&nbsp;
                <span style="background:{new_color};padding:3px 10px;border-radius:8px;font-size:12px;font-weight:600;">{html.escape(new_s)}</span>
                <br/><small style="color:#94a3b8;">By <b>{who}</b> on {when}</small>
                <p style="margin-top:8px;font-size:14px;">{remark}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_analytics_charts(complaints: list[dict]) -> None:
    """Render Plotly Express department & priority breakdown charts."""
    if not complaints:
        return

    raw_df = pd.DataFrame(complaints)
    c1, c2 = st.columns(2)

    with c1:
        dept_counts = raw_df["department"].value_counts().reset_index()
        dept_counts.columns = ["Department", "Count"]
        fig_dept = px.bar(
            dept_counts,
            x="Department",
            y="Count",
            text="Count",
            title="Complaints by Department",
            color="Department",
        )
        fig_dept.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="white",
            showlegend=False,
            height=340,
        )
        st.plotly_chart(fig_dept, width="stretch")

    with c2:
        prio_counts = raw_df["priority"].value_counts().reset_index()
        prio_counts.columns = ["Priority", "Count"]
        fig_prio = px.pie(
            prio_counts,
            names="Priority",
            values="Count",
            hole=0.5,
            title="Priority Breakdown",
            color="Priority",
            color_discrete_map=PRIORITY_COLORS,
        )
        fig_prio.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="white",
            height=340,
        )
        st.plotly_chart(fig_prio, width="stretch")


def render_staff_dashboard(
    user: dict,
    key_prefix: str,
    show_readonly_banner: bool = False,
) -> dict | None:
    """
    Shared staff dashboard (KPIs, Plotly charts, filterable table, complaint detail + history).
    Returns the currently selected complaint dict (or None if none selected/available).
    """
    if show_readonly_banner:
        st.info("🔒 Read-only access. Staff in this role can view complaints and history, but cannot edit them.")

    try:
        complaints = get_complaints(user)
    except PermissionError as exc:
        st.error(f"🚫 Permission denied: {exc}")
        return None
    except Exception as exc:
        st.error(f"❌ Could not load complaints: {exc}")
        return None

    # KPIs
    st.markdown("### 📊 Dashboard Overview")
    total = len(complaints)
    pending = sum(1 for c in complaints if c.get("status") == "Pending")
    high_priority = sum(1 for c in complaints if c.get("priority") == "High")
    resolved = sum(1 for c in complaints if c.get("status") == "Resolved")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📋 Total Complaints", total)
    col2.metric("⏳ Pending", pending)
    col3.metric("🔴 High Priority", high_priority)
    col4.metric("✅ Resolved", resolved)

    _render_analytics_charts(complaints)

    st.markdown("---")
    st.markdown("### 🗂️ All Complaints")

    all_statuses = sorted({c.get("status", "") for c in complaints if c.get("status")})
    all_departments = sorted({c.get("department", "") for c in complaints if c.get("department")})

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_statuses = st.multiselect(
            "Filter by Status",
            options=all_statuses,
            default=all_statuses,
            key=f"{key_prefix}_status_filter",
        )
    with filter_col2:
        selected_departments = st.multiselect(
            "Filter by Department",
            options=all_departments,
            default=all_departments,
            key=f"{key_prefix}_dept_filter",
        )

    search_query = st.text_input(
        "🔎 Search by description, Incident ID, Location, or Department",
        key=f"{key_prefix}_search",
        placeholder="Type to filter…",
    )

    filtered = complaints
    if all_statuses and selected_statuses != all_statuses:
        filtered = [c for c in filtered if c.get("status") in selected_statuses]
    if all_departments and selected_departments != all_departments:
        filtered = [c for c in filtered if c.get("department") in selected_departments]
    if search_query.strip():
        q = search_query.strip().lower()
        filtered = [
            c for c in filtered
            if q in (c.get("text") or "").lower()
            or q in (c.get("incident_id") or "").lower()
            or q in (c.get("department") or "").lower()
            or q in (c.get("location") or "").lower()
        ]

    st.dataframe(build_complaints_df(filtered), width="stretch", hide_index=True)
    st.caption(f"Showing {len(filtered)} of {total} complaints")

    st.markdown("---")
    st.markdown("### 🔍 Complaint Detail & History")

    selectable = filtered if filtered else complaints
    incident_ids = [c.get("incident_id") for c in selectable if c.get("incident_id")]
    if not incident_ids:
        st.info("No complaints available yet.")
        return None

    selected_id = st.selectbox(
        "Select a Complaint (Incident ID):",
        options=incident_ids,
        key=f"{key_prefix}_detail_select",
    )

    matched = next((c for c in selectable if c.get("incident_id") == selected_id), None)
    if not matched:
        return None

    try:
        detail = get_complaint_detail(user, matched["id"])
    except PermissionError as exc:
        st.error(f"🚫 Permission denied: {exc}")
        return None
    except Exception as exc:
        st.error(f"❌ Could not load detail: {exc}")
        return None

    render_complaint_card(detail)
    st.markdown("#### 📜 Status History")
    render_history(detail["id"], user)

    return detail
