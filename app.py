"""
app.py — Entry point for the Citizen Grievance System.
Authenticates users, enforces forced temporary-password resets, and routes by role.
Follows Streamlit's native Light / Dark / System theme.
"""

import html

import streamlit as st

from core.auth import change_own_password, login, register
import core.database as _db
from ui.shared import inject_shared_css, render_html

# ── Page config (must be first Streamlit call) ───────────────────────────────
st.set_page_config(
    page_title="Citizen Grievance Portal",
    page_icon=None,
    layout="wide",
)

inject_shared_css()

# ── Session state bootstrap (auth/data only — no theme state) ────────────────
if "user" not in st.session_state:
    st.session_state.user = None

if "default_pw_active" not in st.session_state:
    st.session_state.default_pw_active = _db.has_default_demo_passwords()


def _show_default_password_warning() -> None:
    if st.session_state.get("default_pw_active"):
        st.warning(
            "Default demo passwords are still active for seeded accounts "
            "(`superadmin`, `admin`, `employee` — password `Admin@123`). "
            "Change them before production deployment."
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

        tab_login, tab_signup = st.tabs(["Login", "Sign Up"])

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
                        st.rerun()
                    else:
                        st.error("Invalid username or password, or account is deactivated.")

        with tab_signup:
            st.info("Public sign-up creates a Citizen account only.")
            if "signup_flash" in st.session_state:
                st.success(st.session_state.pop("signup_flash"))

            signup_gen = st.session_state.get("signup_gen", 0)
            with st.form(f"signup_form_{signup_gen}", clear_on_submit=False):
                new_username = st.text_input("Username (min 3 chars)", key=f"signup_username_{signup_gen}")
                new_fullname = st.text_input("Full Name", key=f"signup_fullname_{signup_gen}")
                new_password = st.text_input("Password (min 8 chars)", type="password", key=f"signup_password_{signup_gen}")
                new_password2 = st.text_input("Confirm Password", type="password", key=f"signup_password2_{signup_gen}")
                reg_submitted = st.form_submit_button("Create Account", type="primary")

            if reg_submitted:
                if new_password != new_password2:
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
                    st.session_state.default_pw_active = _db.has_default_demo_passwords()
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
    with col1:
        render_html(
            f"""<div class="cgs-top-bar">
                <div>
                  <span style="font-size:20px;font-weight:700;">Citizen Grievance Portal</span>
                  <span class="cgs-muted" style="margin:0 12px;">|</span>
                  <span>{safe_name}</span>
                  <span class="cgs-badge {badge_cls}" style="margin-left:10px;">{safe_role}</span>
                </div>
            </div>"""
        )
    with col2:
        if st.button("Logout", key="logout_btn", width="stretch"):
            _clear_app_session()
            st.rerun()


def main() -> None:
    user = st.session_state.get("user")

    if user is None:
        render_auth_page()
        return

    render_topbar(user)

    if user.get("must_change_password"):
        render_forced_password_change(user)
        return

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
