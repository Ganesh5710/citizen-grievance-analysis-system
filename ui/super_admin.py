"""
ui/super_admin.py
Super Admin page for the Citizen Grievance System.
Role: 'super_admin' — read-only complaints view + user management (create staff accounts, list users).
Does NOT grant complaint-edit rights.
"""

import html

import pandas as pd
import streamlit as st

from core.auth import create_staff_account, get_all_users
from ui.shared import SHARED_CSS, render_staff_dashboard

STAFF_ROLES = ["employee", "admin", "super_admin"]


def _render_user_management(user: dict) -> None:
    """Create staff accounts and list all registered users."""
    st.markdown("### ➕ Create Staff Account")

    with st.form(key="sa_create_staff_form", clear_on_submit=True):
        new_username = st.text_input(
            "Username (min 3 chars, letters/digits/underscores)",
            key="sa_new_username",
            placeholder="e.g. john_doe",
        )
        new_full_name = st.text_input(
            "Full Name",
            key="sa_new_full_name",
            placeholder="e.g. John Doe",
        )
        new_password = st.text_input(
            "Password (min 8 chars)",
            type="password",
            key="sa_new_password",
        )
        confirm_password = st.text_input(
            "Confirm Password",
            type="password",
            key="sa_confirm_password",
        )
        new_role = st.selectbox(
            "Role",
            options=STAFF_ROLES,
            key="sa_new_role",
        )
        create_btn = st.form_submit_button("🆕 Create Account")

    if create_btn:
        username_stripped = new_username.strip()
        full_name_stripped = new_full_name.strip()

        if not username_stripped:
            st.error("⚠️ Username is required.")
        elif not full_name_stripped:
            st.error("⚠️ Full name is required.")
        elif not new_password:
            st.error("⚠️ Password is required.")
        elif new_password != confirm_password:
            st.error("⚠️ Passwords do not match. Please re-enter.")
        else:
            try:
                create_staff_account(
                    user,
                    username_stripped,
                    full_name_stripped,
                    new_password,
                    new_role,
                )
                st.success(
                    f"✅ Staff account created for **{html.escape(full_name_stripped)}** "
                    f"(`{html.escape(username_stripped)}`) with role **{new_role}**."
                )
            except PermissionError as exc:
                st.error(f"🚫 Permission denied: {exc}")
            except ValueError as exc:
                st.error(f"⚠️ Validation error: {exc}")
            except Exception as exc:
                st.error(f"❌ Unexpected error: {exc}")

    st.markdown("---")
    st.markdown("### 👥 All Registered Users")

    try:
        all_users = get_all_users(user)
    except PermissionError as exc:
        st.error(f"🚫 Permission denied: {exc}")
        return
    except Exception as exc:
        st.error(f"❌ Could not load users: {exc}")
        return

    if not all_users:
        st.info("No users found in the system.")
        return

    display_keys = ["id", "username", "full_name", "role", "created_at"]
    rows = [{k: u.get(k, "") for k in display_keys} for u in all_users]
    df = pd.DataFrame(rows)
    df.columns = [col.replace("_", " ").title() for col in df.columns]
    st.dataframe(df, width="stretch", hide_index=True)
    st.caption(f"{len(all_users)} user(s) registered in the system")


def render_super_admin_page(user: dict) -> None:
    st.markdown(SHARED_CSS, unsafe_allow_html=True)

    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    st.markdown(
        f"""
        <div class="top-line"></div>
        <h2 style="margin:0 0 4px;">⚙️ Super Admin Control Panel</h2>
        <p style="color:#94a3b8;margin:0 0 24px;">Logged in as <b>{display_name}</b> (Super Admin)</p>
        """,
        unsafe_allow_html=True,
    )

    tab_overview, tab_users = st.tabs(["📋 Complaints Overview", "👤 User Management"])

    with tab_overview:
        render_staff_dashboard(
            user,
            key_prefix="sa",
            show_readonly_banner=True,
        )

    with tab_users:
        _render_user_management(user)
