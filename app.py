"""
app.py — Entry point for the Citizen Grievance System.
Authenticates users and routes to the appropriate role-specific UI.
"""

import html

import streamlit as st

from core.auth import login, register
import core.database as _db
from ui.shared import SHARED_CSS

# ── Page config (must be first Streamlit call) ───────────────────────────────
st.set_page_config(
    page_title="Citizen Grievance Portal",
    page_icon="🏛️",
    layout="wide",
)

st.markdown(SHARED_CSS, unsafe_allow_html=True)

# ── Session state bootstrap ───────────────────────────────────────────────────
if "user" not in st.session_state:
    st.session_state.user = None

if "default_pw_active" not in st.session_state:
    st.session_state.default_pw_active = _db.has_default_demo_passwords()


def _show_default_password_warning() -> None:
    if st.session_state.get("default_pw_active"):
        st.warning(
            "⚠️ **Default demo passwords are still active.** "
            "Seeded demo accounts (`superadmin`, `admin`, `employee` — password `Admin@123`) "
            "should have their default passwords changed before production deployment.",
            icon="⚠️",
        )


def render_auth_page() -> None:
    _, col_m, _ = st.columns([1, 2, 1])
    with col_m:
        st.markdown(
            """
            <div class='card' style='text-align:center; padding:32px;'>
                <div style='font-size:48px;'>🏛️</div>
                <h1 style='color:white; margin:12px 0 4px;'>Citizen Grievance Portal</h1>
                <p style='color:#94a3b8;'>Role-Based Civic Complaint Management System</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        _show_default_password_warning()

        tab_login, tab_signup = st.tabs(["🔐 Login", "📝 Sign Up"])

        with tab_login:
            with st.form("login_form"):
                username = st.text_input("Username", key="login_username")
                password = st.text_input("Password", type="password", key="login_password")
                submitted = st.form_submit_button("Login")

            if submitted:
                if not username or not password:
                    st.error("Please enter both username and password.")
                else:
                    user = login(username.strip(), password)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    else:
                        # Generic message — never reveal whether username or password was wrong
                        st.error("Invalid username or password.")

        with tab_signup:
            st.info("Public sign-up creates a **Citizen** (`user`) account only.")
            with st.form("signup_form"):
                new_username = st.text_input("Choose a Username (min 3 chars)", key="signup_username")
                new_fullname = st.text_input("Full Name", key="signup_fullname")
                new_password = st.text_input("Password (min 8 chars)", type="password", key="signup_password")
                new_password2 = st.text_input("Confirm Password", type="password", key="signup_password2")
                reg_submitted = st.form_submit_button("Create Account")

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
                        st.success(
                            f"Account created for {html.escape(user['full_name'])}! "
                            "Please switch to the Login tab to sign in."
                        )
                    except ValueError as exc:
                        st.error(str(exc))


def render_topbar(user: dict) -> None:
    role_badges = {
        "user":        ("👤", "#3b82f6"),
        "employee":    ("🛡️", "#8b5cf6"),
        "admin":       ("🔑", "#f59e0b"),
        "super_admin": ("⭐", "#ef4444"),
    }
    icon, color = role_badges.get(user["role"], ("❓", "#6b7280"))
    safe_name = html.escape(str(user["full_name"]))
    safe_role = html.escape(user["role"].replace("_", " ").upper())

    col1, col2 = st.columns([6, 1])
    with col1:
        st.markdown(
            f"""<div class='top-bar'>
                <div>
                  <span style='font-size:22px; font-weight:700; color:white;'>🏛️ Citizen Grievance Portal</span>
                  <span style='margin-left:16px; color:#94a3b8;'>|</span>
                  <span style='margin-left:12px; color:#cbd5e1;'>{icon} {safe_name}</span>
                  <span style='margin-left:10px; background:{color}22; color:{color}; border:1px solid {color}44;
                   padding:3px 12px; border-radius:20px; font-size:12px; font-weight:600;'>
                   {safe_role}</span>
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
    with col2:
        if st.button("🚪 Logout", key="logout_btn"):
            st.session_state.clear()
            st.rerun()


def main() -> None:
    user = st.session_state.get("user")

    if user is None:
        render_auth_page()
        return

    render_topbar(user)
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
        from ui.staff_readonly import render_readonly_page
        render_readonly_page(user)

    elif role == "super_admin":
        from ui.super_admin import render_super_admin_page
        render_super_admin_page(user)

    else:
        st.error(f"Unknown role: {html.escape(str(role))}. Please contact support.")


if __name__ == "__main__":
    main()
