"""
app.py — Entry point for the Citizen Grievance System.
Authenticates users, enforces forced temporary-password resets, and routes by role.
Follows Streamlit's native Light / Dark / System theme.
"""

from datetime import datetime, timezone
import html
import importlib
import sys

import pandas as pd
import streamlit as st

# If Streamlit Cloud hot-reloads app.py inside an existing Python process after git pull,
# evict stale cached submodules before importing new symbols.
_PRE_DB = sys.modules.get("core.database")
_PRE_AUTH = sys.modules.get("core.auth")
if (_PRE_DB is not None and getattr(_PRE_DB, "_CGS_SCHEMA_REV", None) != "2026-11-28-r2") or (
    _PRE_AUTH is not None and not hasattr(_PRE_AUTH, "confirm_password_reset")
):
    for _mod_name in list(sys.modules):
        if _mod_name in ("core", "analytics", "geo", "ml", "nlp", "notifications", "ui") or _mod_name.startswith(
            ("core.", "analytics.", "geo.", "ml.", "nlp.", "notifications.", "ui.")
        ):
            sys.modules.pop(_mod_name, None)
    importlib.invalidate_caches()

from core.analytics_geo import get_public_transparency_by_district
from core.auth import (
    change_own_password,
    confirm_password_reset,
    is_session_expired,
    login,
    register,
    request_password_reset,
)
import core.database as _db
from core.health import get_health_status
from core.locations import DEFAULT_LGD_CSV, DEFAULT_LOCATION_CSV, get_location_stats, load_all_default_locations, load_lgd_villages_csv
from core.moderation import generate_captcha_challenge, is_captcha_enabled, verify_captcha_answer
from core.privacy import get_privacy_notice_markdown
from core.public_status import lookup_public_status
from ui.shared import inject_shared_css, render_html, render_notification_center

# ── Page config (must be first Streamlit call) ───────────────────────────────
st.set_page_config(
    page_title="Citizen Grievance Portal",
    page_icon=None,
    layout="wide",
)

inject_shared_css()


def _ensure_locations_bootstrapped() -> None:
    """Auto-load official AP India Post pincodes & LGD villages once if empty/upgraded."""
    if st.session_state.get("_locations_checked"):
        return
    try:
        stats = get_location_stats()
        if stats["states"] == 0 and DEFAULT_LOCATION_CSV.exists():
            load_all_default_locations()
        elif stats.get("towns_villages", 0) == 0 and DEFAULT_LGD_CSV.exists():
            load_lgd_villages_csv(DEFAULT_LGD_CSV)
    except Exception:
        pass
    st.session_state["_locations_checked"] = True


_ensure_locations_bootstrapped()

# ── Session state bootstrap (auth/data only — no theme state) ────────────────
if "user" not in st.session_state:
    st.session_state.user = None

if "default_pw_active" not in st.session_state:
    st.session_state.default_pw_active = _db.has_pending_seed_password_reset()


def _show_default_password_warning() -> None:
    if st.session_state.get("default_pw_active"):
        st.warning(
            "Seeded staff accounts require a password change on first login. "
            "Use the one-time passwords printed at startup or configured in .env."
        )


def _clear_app_session() -> None:
    """Clear authentication and view session keys without affecting Streamlit theme preferences."""
    st.session_state.clear()


def render_auth_page() -> None:
    _, col_m, _ = st.columns([1, 2, 1])
    with col_m:
        render_html(
            """
            <div class="cgs-card cgs-hero-card">
                <div class="cgs-top-line"></div>
                <h1 style="margin:6px 0 6px 0;">Citizen Grievance Portal</h1>
                <p class="cgs-muted" style="margin:0;">Role-Based Civic Complaint Management System</p>
            </div>
            """
        )

        _show_default_password_warning()

        tab_login, tab_signup, tab_track, tab_public, tab_privacy = st.tabs([
            "Login",
            "Sign Up",
            "Track Ticket (Public Status)",
            "Public Transparency",
            "Privacy Notice",
        ])

        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username", key="login_username")
                password = st.text_input("Password", type="password", key="login_password")
                submitted = st.form_submit_button("Login", type="primary")

            if submitted:
                if not username or not password:
                    st.error("Please enter both username and password.")
                else:
                    user = login(username.strip(), password)
                    if user:
                        st.session_state.user = user
                        st.session_state["last_activity_utc"] = datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        st.rerun()
                    else:
                        st.error(
                            "Invalid credentials, account is deactivated, or login is temporarily locked."
                        )

            with st.expander("Forgot Password? (One-Time Email Reset Code)"):
                with st.form("req_reset_form"):
                    r_id = st.text_input("Username or Registered Email", key="reset_req_id")
                    if st.form_submit_button("Send 15-Min Reset Code"):
                        try:
                            msg = request_password_reset(r_id.strip())
                            st.info(msg)
                        except ValueError as exc:
                            st.error(str(exc))
                with st.form("confirm_reset_form"):
                    c_id = st.text_input("Username or Email", key="reset_conf_id")
                    c_code = st.text_input("6-Digit One-Time Code", key="reset_conf_code")
                    c_pw = st.text_input("New Password (min 8 chars)", type="password", key="reset_conf_pw")
                    c_pw2 = st.text_input("Confirm New Password", type="password", key="reset_conf_pw2")
                    if st.form_submit_button("Reset Password"):
                        if c_pw != c_pw2:
                            st.error("New passwords do not match.")
                        else:
                            try:
                                confirm_password_reset(c_id.strip(), c_code.strip(), c_pw)
                                st.success("Password reset successfully. You can log in above.")
                            except ValueError as exc:
                                st.error(str(exc))

        with tab_signup:
            st.info("Public sign-up creates a Citizen account only.")
            if "signup_flash" in st.session_state:
                st.success(st.session_state.pop("signup_flash"))

            signup_gen = st.session_state.get("signup_gen", 0)
            cap_on = is_captcha_enabled()
            if cap_on and f"signup_cap_{signup_gen}" not in st.session_state:
                st.session_state[f"signup_cap_{signup_gen}"] = generate_captcha_challenge()

            with st.form(f"signup_form_{signup_gen}", clear_on_submit=False):
                new_username = st.text_input("Username (min 3 chars)", key=f"signup_username_{signup_gen}")
                new_fullname = st.text_input("Full Name", key=f"signup_fullname_{signup_gen}")
                new_password = st.text_input("Password (min 8 chars)", type="password", key=f"signup_password_{signup_gen}")
                new_password2 = st.text_input("Confirm Password", type="password", key=f"signup_password2_{signup_gen}")
                cap_input = None
                if cap_on:
                    q_str, _ = st.session_state[f"signup_cap_{signup_gen}"]
                    cap_input = st.text_input(q_str, key=f"signup_cap_ans_{signup_gen}")
                reg_submitted = st.form_submit_button("Create Account", type="primary")

            if reg_submitted:
                if cap_on and not verify_captcha_answer(st.session_state[f"signup_cap_{signup_gen}"][1], cap_input):
                    st.error("Security arithmetic answer is incorrect. Please try again.")
                    st.session_state[f"signup_cap_{signup_gen}"] = generate_captcha_challenge()
                elif new_password != new_password2:
                    st.error("Passwords do not match.")
                else:
                    try:
                        user = register(
                            new_username.strip(),
                            new_fullname.strip(),
                            new_password,
                        )
                        st.session_state["signup_flash"] = (
                            f"Account created for {user['full_name']}. "
                            "Switch to the Login tab to sign in."
                        )
                        st.session_state["signup_gen"] = signup_gen + 1
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

        with tab_track:
            st.markdown("#### Public Ticket Status Lookup (No Login Required)")
            st.caption(
                "Enter your Ticket ID and the last 4 digits of your registered phone number "
                "(or last 4 characters of your registered email) to inspect live status without exposing personal details."
            )
            qp_ticket = str(st.query_params.get("ticket", "") or "").strip()
            with st.form("public_ticket_lookup_form"):
                pt_id = st.text_input("Ticket ID", value=qp_ticket, placeholder="e.g. CIV-2026-0001", key="pub_track_id")
                pt_last4 = st.text_input(
                    "Last 4 digits of phone OR last 4 chars of email",
                    max_chars=4,
                    key="pub_track_last4",
                )
                pt_go = st.form_submit_button("Check Ticket Status", type="primary")

            if pt_go:
                try:
                    info = lookup_public_status(pt_id.strip(), pt_last4.strip())
                    st.success(f"Ticket #{info['incident_id']} — Status: {info['status']} ({info['department']})")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Department", info["department"])
                    c2.metric("Priority", info["priority"])
                    c3.metric("District", info["district"] or "-")
                    st.write(
                        f"**Created:** {info['created_at']} UTC | **Updated:** {info['updated_at']} UTC"
                        + (f" | **SLA Due:** {info['due_at']} UTC" if info.get("due_at") else "")
                    )
                    if info.get("timeline"):
                        st.markdown("##### Public Status Timeline")
                        for ev in info["timeline"]:
                            st.write(f"- `{ev['timestamp']} UTC` — {ev['event']}")
                except ValueError as exc:
                    st.error(str(exc))

        with tab_public:
            st.markdown("#### Public District Transparency Dashboard")
            st.caption(
                "Aggregated civic grievance statistics by district. "
                "District groups with fewer than 5 complaints are suppressed to prevent identification; no personal details are shown."
            )
            pub = get_public_transparency_by_district()
            p1, p2, p3 = st.columns(3)
            p1.metric("Total Reported", pub["total_complaints"])
            p2.metric("Districts Shown", len(pub["visible_districts"]))
            p3.metric("Suppressed Groups (< 5)", pub["suppressed_groups_count"])

            if pub["visible_districts"]:
                st.dataframe(
                    pd.DataFrame([
                        {
                            "District": d["district"],
                            "Total": d["total"],
                            "Awaiting Assignment": d["awaiting_assignment"],
                            "In Progress": d["in_progress"],
                            "Resolved / Closed": d["resolved"],
                            "Resolution Rate (%)": d["resolution_rate_pct"],
                            "Avg Resolution (h)": d["avg_resolution_hours"],
                        }
                        for d in pub["visible_districts"]
                    ]),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("No district currently meets the minimum aggregation threshold of 5 complaints.")

        with tab_privacy:
            st.markdown(get_privacy_notice_markdown())


def render_forced_password_change(user: dict) -> None:
    """Block dashboard access until a user with a temporary password updates it."""
    _, col_m, _ = st.columns([1, 2, 1])
    with col_m:
        render_html(
            """
            <div class="cgs-card">
                <div class="cgs-top-line"></div>
                <h2 style="margin:0 0 6px 0;">Password Change Required</h2>
                <p class="cgs-muted" style="margin:0;">
                    Your password was reset by an administrator. Choose a new password to continue.
                </p>
            </div>
            """
        )
        with st.form("force_pw_change_form"):
            cur_pw = st.text_input("Temporary / Current Password", type="password", key="force_cur_pw")
            new_pw = st.text_input("New Password (min 8 chars)", type="password", key="force_new_pw")
            new_pw2 = st.text_input("Confirm New Password", type="password", key="force_new_pw2")
            save_pw = st.form_submit_button("Update Password", type="primary")

        if save_pw:
            if new_pw != new_pw2:
                st.error("New passwords do not match.")
            else:
                try:
                    updated_user = change_own_password(user, cur_pw, new_pw)
                    st.session_state.user = updated_user
                    st.session_state.default_pw_active = _db.has_pending_seed_password_reset()
                    st.success("Password updated successfully.")
                    st.rerun()
                except (PermissionError, ValueError) as exc:
                    st.error(str(exc))


def render_topbar(user: dict) -> None:
    role_badges = {
        "user":        "cgs-badge-primary",
        "employee":    "cgs-badge-purple",
        "admin":       "cgs-badge-warning",
        "super_admin": "cgs-badge-danger",
    }
    badge_cls = role_badges.get(user["role"], "cgs-badge-neutral")
    safe_name = html.escape(str(user["full_name"]))
    safe_role = html.escape(user["role"].replace("_", " ").upper())

    col1, col2 = st.columns([6, 1], vertical_alignment="center")
    extra_scope = ""
    if user.get("district_name"):
        extra_scope += f" <span class='cgs-muted'>|</span> <span>{html.escape(str(user['district_name']))}</span>"
    with col1:
        render_html(
            f"""<div class="cgs-top-bar">
                <div>
                  <span style="font-size:20px;font-weight:700;">Citizen Grievance Portal</span>
                  <span class="cgs-muted" style="margin:0 12px;">|</span>
                  <span>{safe_name}</span>
                  <span class="cgs-badge {badge_cls}" style="margin-left:10px;">{safe_role}</span>{extra_scope}
                </div>
            </div>"""
        )
    with col2:
        if st.button("Logout", key="logout_btn", width="stretch"):
            _clear_app_session()
            st.rerun()


def main() -> None:
    if str(st.query_params.get("health", "")).strip() == "1":
        st.json(get_health_status())
        return

    user = st.session_state.get("user")

    if user is not None:
        last_act = st.session_state.get("last_activity_utc")
        if is_session_expired(last_act):
            _clear_app_session()
            st.session_state["session_timeout_notice"] = (
                "Your session timed out after 30 minutes of inactivity. Please log in again."
            )
            st.rerun()
        st.session_state["last_activity_utc"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    if "session_timeout_notice" in st.session_state:
        st.warning(st.session_state.pop("session_timeout_notice"))

    if user is None:
        render_auth_page()
        return

    render_topbar(user)

    if user.get("must_change_password"):
        render_forced_password_change(user)
        return

    render_notification_center(user)

    role = user["role"]

    if role in ("employee", "admin", "super_admin"):
        _show_default_password_warning()

    if role == "user":
        from ui.citizen import render_citizen_page
        render_citizen_page(user)

    elif role == "employee":
        from ui.employee import render_employee_page
        render_employee_page(user)

    elif role == "admin":
        from ui.admin import render_admin_page
        render_admin_page(user)

    elif role == "super_admin":
        from ui.super_admin import render_super_admin_page
        render_super_admin_page(user)

    else:
        st.error(f"Unknown role: {html.escape(str(role))}. Please contact support.")


if __name__ == "__main__":
    main()
