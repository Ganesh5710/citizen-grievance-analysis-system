"""
ui/super_admin.py
Super Admin control panel:
  - Complaints tab (full edit + assignment + remove image + soft delete)
  - Escalations tab (overdue queue + acknowledgement + reassign)
  - Performance tab (department & employee analytics)
  - User Management tab (create any role, assign employee department, deactivate/reactivate, role change, password reset)
  - Departments tab (add, rename, deactivate, reactivate)
  - SLA Settings tab (configure target hours per department & priority)
"""

import html

import pandas as pd
import streamlit as st

from core.auth import (
    change_user_role,
    create_user_account,
    get_all_users,
    reset_user_password,
    set_user_active,
    set_user_department,
)
from core.departments import (
    create_department,
    get_active_departments,
    get_all_departments,
    rename_department,
    set_department_active,
)
from core.sla import VALID_PRIORITIES, get_sla_policies, update_sla_policy
from ui.shared import (
    render_escalations_tab,
    render_html,
    render_performance_tab,
    render_staff_dashboard,
)

ALL_ROLES = ["user", "employee", "admin", "super_admin"]


def _render_user_management(user: dict) -> None:
    """Create accounts with any role, manage status/role/department/passwords, and list all users."""
    if "sa_user_flash" in st.session_state:
        st.success(st.session_state.pop("sa_user_flash"))
    if "sa_temp_pw_notice" in st.session_state:
        notice = st.session_state.pop("sa_temp_pw_notice")
        st.info(
            f"Temporary password generated for {notice['username']} "
            "(user will be forced to change it on next login):"
        )
        st.code(notice["password"], language="text")

    st.markdown("### Create User Account")

    active_depts = get_active_departments()
    dept_ids = [d["id"] for d in active_depts]
    dept_labels = {d["id"]: d["name"] for d in active_depts}

    user_form_gen = st.session_state.get("sa_user_form_gen", 0)
    with st.form(key=f"sa_create_user_form_{user_form_gen}", clear_on_submit=False):
        col1, col2 = st.columns(2)
        with col1:
            new_username = st.text_input(
                "Username (min 3 chars, letters/digits/underscores)",
                key=f"sa_new_username_{user_form_gen}",
                placeholder="e.g. john_doe",
            )
            new_password = st.text_input(
                "Password (min 8 chars)",
                type="password",
                key=f"sa_new_password_{user_form_gen}",
            )
            new_role = st.selectbox(
                "Role",
                options=ALL_ROLES,
                key=f"sa_new_role_{user_form_gen}",
            )
        with col2:
            new_full_name = st.text_input(
                "Full Name",
                key=f"sa_new_full_name_{user_form_gen}",
                placeholder="e.g. John Doe",
            )
            confirm_password = st.text_input(
                "Confirm Password",
                type="password",
                key=f"sa_confirm_password_{user_form_gen}",
            )
            new_dept_id = st.selectbox(
                "Department (required when role is employee)",
                options=[None, *dept_ids],
                format_func=lambda did: "None (non-employee)" if did is None else dept_labels.get(did, str(did)),
                key=f"sa_new_user_dept_{user_form_gen}",
            )

        create_btn = st.form_submit_button("Create Account", type="primary")

    if create_btn:
        username_stripped = new_username.strip()
        full_name_stripped = new_full_name.strip()

        if not username_stripped:
            st.error("Username is required.")
        elif not full_name_stripped:
            st.error("Full name is required.")
        elif not new_password:
            st.error("Password is required.")
        elif new_password != confirm_password:
            st.error("Passwords do not match.")
        else:
            try:
                create_user_account(
                    user,
                    username_stripped,
                    full_name_stripped,
                    new_password,
                    new_role,
                    department_id=new_dept_id if new_role == "employee" else None,
                )
                st.session_state["sa_user_flash"] = (
                    f"Account created for {full_name_stripped} ({username_stripped}) "
                    f"with role {new_role}."
                )
                st.session_state["sa_user_form_gen"] = user_form_gen + 1
                st.rerun()
            except PermissionError as exc:
                st.error(f"Permission denied: {exc}")
            except ValueError as exc:
                st.error(f"Validation error: {exc}")
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

    st.markdown("---")
    st.markdown("### All Registered Users")

    try:
        all_users = get_all_users(user)
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return
    except Exception as exc:
        st.error(f"Could not load users: {exc}")
        return

    if not all_users:
        st.info("No users found in the system.")
        return

    rows = [
        {
            "ID":             u["id"],
            "Username":       u["username"],
            "Full Name":      u["full_name"],
            "Role":           u["role"],
            "Department":     u["department"] or "-",
            "Status":         "Active" if u["is_active"] else "Inactive",
            "Reset Required": "Yes" if u["must_change_password"] else "No",
            "Created At":     u["created_at"],
        }
        for u in all_users
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption(f"{len(all_users)} user(s) registered")

    st.markdown("---")
    st.markdown("### Manage Selected User")

    all_depts = get_all_departments(user)
    all_dept_ids = [d["id"] for d in all_depts]
    all_dept_labels = {
        d["id"]: (d["name"] if d["is_active"] else f"{d['name']} (Inactive)")
        for d in all_depts
    }

    user_ids = [u["id"] for u in all_users]
    user_labels = {
        u["id"]: f"{u['username']} ({u['full_name']}) - {u['role']} [{'Active' if u['is_active'] else 'Inactive'}]"
        for u in all_users
    }

    select_key = "sa_manage_user_select"
    if select_key in st.session_state and st.session_state[select_key] not in user_ids:
        st.session_state[select_key] = user_ids[0]

    target_id = st.selectbox(
        "Select User",
        options=user_ids,
        format_func=lambda uid: user_labels[uid],
        key=select_key,
    )
    target = next(u for u in all_users if u["id"] == target_id)
    target_ver = f"{target_id}_{target['role']}_{target['department_id']}_{target['is_active']}"

    col_role, col_dept, col_status, col_reset = st.columns([2, 2, 1, 1], vertical_alignment="bottom")

    with col_role:
        role_idx = ALL_ROLES.index(target["role"]) if target["role"] in ALL_ROLES else 0
        updated_role = st.selectbox(
            "Role",
            options=ALL_ROLES,
            index=role_idx,
            key=f"sa_role_select_{target_ver}",
        )
        dept_for_role = target["department_id"]
        if updated_role == "employee" and dept_for_role is None and all_dept_ids:
            dept_for_role = all_dept_ids[0]
        if st.button("Update Role", key=f"sa_update_role_btn_{target_ver}", width="stretch"):
            try:
                change_user_role(user, target_id, updated_role, department_id=dept_for_role)
                st.session_state["sa_user_flash"] = (
                    f"Updated role for {target['username']} to {updated_role}."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    with col_dept:
        dept_opts: list[int | None] = [None, *all_dept_ids]
        cur_did = target.get("department_id")
        dept_idx = dept_opts.index(cur_did) if cur_did in dept_opts else 0
        updated_did = st.selectbox(
            "Department (employees)",
            options=dept_opts,
            index=dept_idx,
            format_func=lambda did: "None" if did is None else all_dept_labels.get(did, str(did)),
            key=f"sa_user_dept_select_{target_ver}",
        )
        if st.button("Update Department", key=f"sa_update_dept_btn_{target_ver}", width="stretch"):
            try:
                set_user_department(user, target_id, updated_did)
                st.session_state["sa_user_flash"] = (
                    f"Updated department for {target['username']}."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    with col_status:
        action_label = "Deactivate User" if target["is_active"] else "Reactivate User"
        if st.button(action_label, key=f"sa_toggle_active_btn_{target_ver}", width="stretch"):
            try:
                set_user_active(user, target_id, not target["is_active"])
                new_state = "reactivated" if not target["is_active"] else "deactivated"
                st.session_state["sa_user_flash"] = (
                    f"User {target['username']} {new_state}."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    with col_reset:
        if st.button("Reset Password", key=f"sa_reset_pw_btn_{target_ver}", width="stretch"):
            try:
                temp_pw = reset_user_password(user, target_id)
                st.session_state["sa_temp_pw_notice"] = {
                    "username": target["username"],
                    "password": temp_pw,
                }
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))


def _render_department_management(user: dict) -> None:
    """Super Admin CRUD for departments (never hard-deletes departments)."""
    if "sa_dept_flash" in st.session_state:
        st.success(st.session_state.pop("sa_dept_flash"))

    st.markdown("### Add Department")

    dept_form_gen = st.session_state.get("sa_dept_form_gen", 0)
    with st.form(key=f"sa_create_dept_form_{dept_form_gen}", clear_on_submit=False):
        dept_name = st.text_input(
            "Department Name",
            key=f"sa_new_dept_name_{dept_form_gen}",
            placeholder="e.g. Parks and Recreation",
        )
        dept_desc = st.text_input(
            "Short Description",
            key=f"sa_new_dept_desc_{dept_form_gen}",
            placeholder="e.g. Public parks, trees, playgrounds, and lighting",
        )
        add_btn = st.form_submit_button("Add Department", type="primary")

    if add_btn:
        try:
            created = create_department(user, dept_name.strip(), dept_desc.strip())
            st.session_state["sa_dept_flash"] = f"Department '{created['name']}' added."
            st.session_state["sa_dept_form_gen"] = dept_form_gen + 1
            st.rerun()
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))

    st.markdown("---")
    st.markdown("### Existing Departments")

    try:
        depts = get_all_departments(user)
    except (PermissionError, ValueError) as exc:
        st.error(str(exc))
        return

    dept_rows = [
        {
            "ID":          d["id"],
            "Name":        d["name"],
            "Description": d["description"],
            "Status":      "Active" if d["is_active"] else "Inactive",
            "Created At":  d["created_at"],
        }
        for d in depts
    ]
    st.dataframe(pd.DataFrame(dept_rows), width="stretch", hide_index=True)

    if not depts:
        return

    st.markdown("---")
    st.markdown("### Edit Selected Department")

    dept_ids = [d["id"] for d in depts]
    dept_labels = {
        d["id"]: f"{d['name']} ({'Active' if d['is_active'] else 'Inactive'})"
        for d in depts
    }
    select_key = "sa_manage_dept_select"
    if select_key in st.session_state and st.session_state[select_key] not in dept_ids:
        st.session_state[select_key] = dept_ids[0]

    selected_did = st.selectbox(
        "Select Department",
        options=dept_ids,
        format_func=lambda did: dept_labels[did],
        key=select_key,
    )
    selected_dept = next(d for d in depts if d["id"] == selected_did)
    ver = f"{selected_did}_{selected_dept['name']}_{selected_dept['is_active']}"

    with st.form(key=f"sa_rename_dept_form_{ver}"):
        edit_name = st.text_input(
            "Department Name",
            value=selected_dept["name"],
            key=f"sa_edit_dept_name_{ver}",
        )
        edit_desc = st.text_input(
            "Short Description",
            value=selected_dept["description"],
            key=f"sa_edit_dept_desc_{ver}",
        )
        rename_btn = st.form_submit_button("Save Department Changes", type="primary")

    if rename_btn:
        try:
            updated = rename_department(user, selected_did, edit_name.strip(), edit_desc.strip())
            st.session_state["sa_dept_flash"] = f"Department '{updated['name']}' updated."
            st.rerun()
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))

    toggle_label = "Deactivate Department" if selected_dept["is_active"] else "Reactivate Department"
    if st.button(toggle_label, key=f"sa_toggle_dept_{ver}"):
        try:
            set_department_active(user, selected_did, not selected_dept["is_active"])
            state = "reactivated" if not selected_dept["is_active"] else "deactivated"
            st.session_state["sa_dept_flash"] = f"Department '{selected_dept['name']}' {state}."
            st.rerun()
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))


def _render_sla_settings(user: dict) -> None:
    """Super Admin SLA target hours configuration per department and priority."""
    if "sa_sla_flash" in st.session_state:
        st.success(st.session_state.pop("sa_sla_flash"))

    st.markdown("### SLA Policies (Target Resolution Hours)")
    try:
        policies = get_sla_policies(user)
        depts = get_all_departments(user)
    except (PermissionError, ValueError) as exc:
        st.error(str(exc))
        return

    policy_df = pd.DataFrame([
        {
            "Department":   p["department"] if p["department_is_active"] else f"{p['department']} (Inactive)",
            "Priority":     p["priority"],
            "Target Hours": p["target_hours"],
            "Updated At":   p["updated_at"],
        }
        for p in policies
    ])
    st.dataframe(policy_df, width="stretch", hide_index=True)

    if not depts:
        return

    st.markdown("---")
    st.markdown("### Update SLA Target")
    dept_ids = [d["id"] for d in depts]
    dept_labels = {
        d["id"]: (d["name"] if d["is_active"] else f"{d['name']} (Inactive)")
        for d in depts
    }

    with st.form(key="sa_sla_update_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            sel_dept = st.selectbox(
                "Department",
                options=dept_ids,
                format_func=lambda did: dept_labels[did],
                key="sa_sla_dept",
            )
        with col2:
            sel_prio = st.selectbox(
                "Priority",
                options=list(VALID_PRIORITIES),
                key="sa_sla_prio",
            )
        cur_match = next(
            (p for p in policies if p["department_id"] == sel_dept and p["priority"] == sel_prio),
            None,
        )
        default_hrs = cur_match["target_hours"] if cur_match else 48
        with col3:
            target_hours = st.number_input(
                "Target Hours",
                min_value=1,
                max_value=8760,
                value=int(default_hrs),
                step=1,
                key="sa_sla_hours",
            )
        if st.form_submit_button("Save SLA Policy", type="primary"):
            try:
                updated = update_sla_policy(user, sel_dept, sel_prio, int(target_hours))
                st.session_state["sa_sla_flash"] = (
                    f"SLA updated: {updated['department']} / {updated['priority']} -> {updated['target_hours']} hours."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))


def render_super_admin_page(user: dict) -> None:
    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Super Admin Control Panel</h2>
        <p class="cgs-muted" style="margin:0 0 24px;">Logged in as <b>{display_name}</b></p>
        """
    )

    tab_complaints, tab_escalations, tab_perf, tab_users, tab_departments, tab_sla = st.tabs([
        "Complaints",
        "Escalations",
        "Performance",
        "User Management",
        "Departments",
        "SLA Settings",
    ])

    with tab_complaints:
        render_staff_dashboard(user, key_prefix="sa")

    with tab_escalations:
        render_escalations_tab(user, key_prefix="sa")

    with tab_perf:
        render_performance_tab(user)

    with tab_users:
        _render_user_management(user)

    with tab_departments:
        _render_department_management(user)

    with tab_sla:
        _render_sla_settings(user)
