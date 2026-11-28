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
    create_admin_account,
    create_user_account,
    get_all_users,
    reassign_district_to_admin,
    reset_user_password,
    set_user_active,
    set_user_department,
    set_user_jurisdiction,
    update_admin_jurisdiction,
)
from core.departments import (
    create_department,
    get_active_departments,
    get_all_departments,
    rename_department,
    set_department_active,
)
from core.locations import (
    get_default_state,
    get_districts,
    get_location_stats,
    get_mandals,
    reload_locations_admin,
    set_district_self_claim,
)
from core.ml_service import activate_ml_model, list_ml_models, retrain_ml_models
from core.oversight import (
    get_admin_oversight_metrics,
    get_audit_logs_filtered,
    get_super_admin_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    run_unassigned_escalation_check,
)
from core.priority_rules import (
    get_duration_extraction_logs,
    get_priority_feedback_logs,
    get_priority_rule_audit_logs,
    load_priority_rules,
    save_priority_rules,
)
from core.sla import VALID_PRIORITIES, get_sla_policies, run_priority_aging_check, update_sla_policy
from ui.shared import (
    render_escalations_tab,
    render_forecast_and_predictions_tab,
    render_html,
    render_performance_tab,
    render_review_queue_tab,
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

    all_districts = get_districts(active_only=True)
    dist_ids = [d["id"] for d in all_districts]
    dist_labels = {d["id"]: d["name"] for d in all_districts}

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
            new_district_id = st.selectbox(
                "District Jurisdiction (for employee / single-district)",
                options=[None, *dist_ids],
                format_func=lambda did: "None (Unscoped)" if did is None else dist_labels.get(did, str(did)),
                key=f"sa_new_user_district_{user_form_gen}",
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
            new_admin_districts = st.multiselect(
                "Admin Districts (when role is admin; defaults to selected District above)",
                options=dist_ids,
                format_func=lambda did: dist_labels.get(did, str(did)),
                key=f"sa_new_admin_districts_{user_form_gen}",
            )

        new_admin_depts = st.multiselect(
            "Admin Department Scope (optional; leave empty for all departments in assigned districts)",
            options=dept_ids,
            format_func=lambda did: dept_labels.get(did, str(did)),
            key=f"sa_new_admin_depts_{user_form_gen}",
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
                if new_role == "admin":
                    admin_dids = list(new_admin_districts)
                    if not admin_dids and new_district_id is not None:
                        admin_dids = [new_district_id]
                    if admin_dids:
                        create_admin_account(
                            user,
                            username_stripped,
                            full_name_stripped,
                            new_password,
                            district_ids=admin_dids,
                            department_ids=list(new_admin_depts) if new_admin_depts else None,
                        )
                    else:
                        create_user_account(
                            user,
                            username_stripped,
                            full_name_stripped,
                            new_password,
                            new_role,
                        )
                else:
                    create_user_account(
                        user,
                        username_stripped,
                        full_name_stripped,
                        new_password,
                        new_role,
                        department_id=new_dept_id if new_role == "employee" else None,
                        district_id=new_district_id if new_role == "employee" else None,
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

    def _fmt_user_jur(u: dict) -> str:
        if u["role"] == "admin":
            jurs = u.get("admin_jurisdictions") or []
            dnames = sorted({j["district_name"] for j in jurs if j.get("district_name")})
            return ", ".join(dnames) if dnames else "Legacy Unscoped"
        if u.get("district_name"):
            return f"{u['district_name']}" + (f" / {u['mandal_name']}" if u.get("mandal_name") else "")
        return "-"

    rows = [
        {
            "ID":             u["id"],
            "Username":       u["username"],
            "Full Name":      u["full_name"],
            "Role":           u["role"],
            "Department":     u["department"] or "-",
            "Jurisdiction":   _fmt_user_jur(u),
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
    target_ver = f"{target_id}_{target['role']}_{target['department_id']}_{target.get('district_id')}_{target['is_active']}"

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
                extra = (
                    " Open district tasks returned to Awaiting Assignment."
                    if (not new_state.startswith("re")) and target["role"] == "admin"
                    else ""
                )
                st.session_state["sa_user_flash"] = (
                    f"User {target['username']} {new_state}.{extra}"
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

    if target["role"] == "admin" and dist_ids:
        st.markdown("#### Admin Jurisdiction Scope")
        if target["is_active"]:
            st.caption("Deactivating an admin automatically releases their open complaints back to Awaiting Assignment.")
        cur_jurs = target.get("admin_jurisdictions") or []
        cur_dist_ids = sorted({j["district_id"] for j in cur_jurs if j.get("district_id") in dist_ids})
        cur_admin_dept_ids = sorted({j["department_id"] for j in cur_jurs if j.get("department_id") in dept_ids})

        jcol1, jcol2 = st.columns(2)
        with jcol1:
            upd_admin_dists = st.multiselect(
                "Assigned Districts",
                options=dist_ids,
                default=cur_dist_ids,
                format_func=lambda did: dist_labels.get(did, str(did)),
                key=f"sa_edit_admin_dists_{target_ver}",
            )
        with jcol2:
            upd_admin_depts = st.multiselect(
                "Department Restriction (optional)",
                options=dept_ids,
                default=cur_admin_dept_ids,
                format_func=lambda did: dept_labels.get(did, str(did)),
                key=f"sa_edit_admin_depts_{target_ver}",
            )
        if st.button("Update Admin Jurisdiction", key=f"sa_upd_admin_jur_btn_{target_ver}"):
            try:
                update_admin_jurisdiction(
                    user,
                    target_id,
                    district_ids=list(upd_admin_dists),
                    department_ids=list(upd_admin_depts) if upd_admin_depts else None,
                )
                st.session_state["sa_user_flash"] = f"Updated jurisdiction for admin {target['username']}."
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    elif target["role"] == "employee" and dist_ids:
        st.markdown("#### Employee Jurisdiction Scope")
        emp_dist_opts: list[int | None] = [None, *dist_ids]
        cur_emp_did = target.get("district_id")
        emp_dist_idx = emp_dist_opts.index(cur_emp_did) if cur_emp_did in emp_dist_opts else 0

        ecol1, ecol2 = st.columns(2)
        with ecol1:
            upd_emp_did = st.selectbox(
                "Assigned District",
                options=emp_dist_opts,
                index=emp_dist_idx,
                format_func=lambda did: "Unscoped" if did is None else dist_labels.get(did, str(did)),
                key=f"sa_edit_emp_dist_{target_ver}",
            )
        upd_emp_mid = None
        emp_mandals = get_mandals(upd_emp_did) if upd_emp_did is not None else []
        with ecol2:
            if emp_mandals:
                m_opts: list[int | None] = [None, *[m["id"] for m in emp_mandals]]
                m_lbls = {None: "All Mandals in District", **{m["id"]: m["name"] for m in emp_mandals}}
                cur_mid = target.get("mandal_id")
                m_idx = m_opts.index(cur_mid) if cur_mid in m_opts else 0
                upd_emp_mid = st.selectbox(
                    "Assigned Mandal (optional)",
                    options=m_opts,
                    index=m_idx,
                    format_func=lambda mid: m_lbls.get(mid, str(mid)),
                    key=f"sa_edit_emp_mandal_{target_ver}",
                )
            else:
                st.text_input(
                    "Assigned Mandal (optional)",
                    value="N/A (no mandal data loaded)",
                    disabled=True,
                    key=f"sa_edit_emp_mandal_na_{target_ver}",
                )
        if st.button("Update Employee Jurisdiction", key=f"sa_upd_emp_jur_btn_{target_ver}"):
            try:
                set_user_jurisdiction(user, target_id, upd_emp_did, upd_emp_mid)
                st.session_state["sa_user_flash"] = f"Updated jurisdiction for employee {target['username']}."
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


def _render_ml_models_tab(user: dict) -> None:
    """Super Admin ML model registry: retrain without auto-activation & manually activate versions."""
    if "sa_ml_flash" in st.session_state:
        st.success(st.session_state.pop("sa_ml_flash"))

    st.markdown("### ML Model Versions")
    st.caption(
        "Retraining trains on data/synthetic_complaints.csv plus reviewed complaints and saves a "
        "new versioned artifact without auto-activating it. Activate a version only after checking metrics."
    )

    if st.button("Retrain Candidate Model Version", key="sa_ml_retrain_btn", type="primary"):
        with st.spinner("Training and evaluating candidate models..."):
            try:
                meta = retrain_ml_models(user)
                st.session_state["sa_ml_flash"] = (
                    f"Trained candidate version {meta['version']} "
                    f"(Dept {meta['department']['best_model']} F1={meta['department']['best_metrics']['test_macro_f1']}, "
                    f"Priority {meta['priority']['best_model']} F1={meta['priority']['best_metrics']['test_macro_f1']}). "
                    "Not activated automatically."
                )
                st.rerun()
            except (PermissionError, Exception) as exc:
                st.error(f"Retraining failed: {exc}")

    models = list_ml_models(user)
    if not models:
        st.info("No trained ML model artifacts found yet. Run scripts/train.py or click Retrain above.")
        return

    df_models = pd.DataFrame([
        {
            "Version": m["version"],
            "Active": "ACTIVE" if m["is_active"] else "Candidate",
            "Dept Model": m["dept_model_name"],
            "Dept Test F1": round(float(m["dept_test_f1"]), 4) if m.get("dept_test_f1") is not None else None,
            "Priority Model": m["priority_model_name"],
            "Priority Test F1": round(float(m["priority_test_f1"]), 4) if m.get("priority_test_f1") is not None else None,
            "Rows": m["training_rows"],
            "Created By": m["created_by_username"],
            "Created (UTC)": m["created_at"],
            "Activated (UTC)": m.get("activated_at") or "",
        }
        for m in models
    ])
    st.dataframe(df_models, width="stretch", hide_index=True)

    inactive = [m for m in models if not m["is_active"]]
    if inactive:
        st.markdown("#### Activate a Model Version")
        sel_ver = st.selectbox(
            "Candidate Version",
            options=[m["version"] for m in inactive],
            key="sa_ml_activate_select",
        )
        if st.button("Activate Selected Version", key="sa_ml_activate_btn"):
            try:
                activate_ml_model(user, sel_ver)
                st.session_state["sa_ml_flash"] = f"Activated ML model version {sel_ver}."
                st.rerun()
            except (PermissionError, ValueError, FileNotFoundError) as exc:
                st.error(str(exc))


def _render_priority_rules_tab(user: dict) -> None:
    """Super Admin Priority Rules YAML editor (audit-logged), Aging Check, and Continuous Improvement Logs."""
    if "sa_pr_flash" in st.session_state:
        st.success(st.session_state.pop("sa_pr_flash"))

    rules = load_priority_rules()
    st.markdown("### Duration-Aware Priority Rules (`config/priority_rules.yaml`)")
    st.caption("Configure default duration thresholds, faster escalation overrides, and open-ticket aging. Every edit is audit-logged.")

    with st.form(key="sa_priority_rules_form"):
        st.markdown("#### Default Thresholds")
        c1, c2, c3 = st.columns(3)
        with c1:
            def_med = st.number_input(
                "Default Medium >= (hours)",
                min_value=1.0,
                max_value=720.0,
                value=float(rules["default"]["medium_min_hours"]),
                step=1.0,
                key="sa_pr_def_med",
            )
        with c2:
            def_high = st.number_input(
                "Default High >= (hours)",
                min_value=2.0,
                max_value=2160.0,
                value=float(rules["default"]["high_min_hours"]),
                step=1.0,
                key="sa_pr_def_high",
            )
        with c3:
            rev_thresh = st.number_input(
                "Low-Confidence Review Threshold",
                min_value=0.10,
                max_value=0.95,
                value=float(rules.get("review_confidence_threshold", 0.55)),
                step=0.05,
                key="sa_pr_rev_thresh",
            )

        st.markdown("#### Essential Service Department Overrides")
        overrides = dict(rules.get("department_overrides", {}))
        active_depts = [d["name"] for d in get_active_departments()]
        override_depts = sorted(set(list(overrides.keys()) + ["Water", "Electricity"]))
        edited_overrides: dict[str, dict[str, float]] = {}
        for dname in override_depts:
            if dname not in active_depts and dname not in overrides:
                continue
            ov = overrides.get(dname, rules["default"])
            oc1, oc2, oc3 = st.columns([1.2, 1, 1])
            with oc1:
                enabled_ov = st.checkbox(
                    f"Override: {dname}",
                    value=dname in overrides,
                    key=f"sa_pr_ov_en_{dname}",
                )
            with oc2:
                ov_med = st.number_input(
                    f"{dname} Medium >= (hours)",
                    min_value=1.0,
                    max_value=720.0,
                    value=float(ov["medium_min_hours"]),
                    step=1.0,
                    key=f"sa_pr_ov_med_{dname}",
                )
            with oc3:
                ov_high = st.number_input(
                    f"{dname} High >= (hours)",
                    min_value=2.0,
                    max_value=2160.0,
                    value=float(ov["high_min_hours"]),
                    step=1.0,
                    key=f"sa_pr_ov_high_{dname}",
                )
            if enabled_ov:
                edited_overrides[dname] = {"medium_min_hours": ov_med, "high_min_hours": ov_high}

        st.markdown("#### Open Complaint Aging Rules")
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            aging_enabled = st.checkbox(
                "Enable Automatic Priority Aging",
                value=bool(rules["aging"].get("enabled", True)),
                key="sa_pr_aging_en",
            )
        with ac2:
            aging_low_med = st.number_input(
                "Low -> Medium after (days)",
                min_value=1.0,
                max_value=90.0,
                value=float(rules["aging"]["low_to_medium_days"]),
                step=1.0,
                key="sa_pr_aging_lm",
            )
        with ac3:
            aging_med_high = st.number_input(
                "Medium -> High after (days)",
                min_value=2.0,
                max_value=180.0,
                value=float(rules["aging"]["medium_to_high_days"]),
                step=1.0,
                key="sa_pr_aging_mh",
            )

        audit_note = st.text_input(
            "Audit Reason / Note",
            key="sa_pr_note",
            placeholder="Reason for changing priority thresholds...",
        )
        if st.form_submit_button("Save Priority Rules", type="primary"):
            try:
                save_priority_rules(
                    user,
                    {
                        "default": {"medium_min_hours": def_med, "high_min_hours": def_high},
                        "department_overrides": edited_overrides,
                        "aging": {
                            "enabled": aging_enabled,
                            "low_to_medium_days": aging_low_med,
                            "medium_to_high_days": aging_med_high,
                        },
                        "review_confidence_threshold": rev_thresh,
                    },
                    note=audit_note,
                )
                st.session_state["sa_pr_flash"] = "Priority rules saved and audit-logged."
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    if st.button("Run Open Complaint Aging Check Now", key="sa_pr_run_aging_btn"):
        res = run_priority_aging_check()
        st.session_state["sa_pr_flash"] = (
            f"Priority aging complete: {res['low_to_medium']} moved Low->Medium, "
            f"{res['medium_to_high']} moved Medium->High."
        )
        st.rerun()

    st.markdown("---")
    st.markdown("#### Priority Rules Audit Log")
    audits = get_priority_rule_audit_logs(user, limit=30)
    if audits:
        st.dataframe(
            pd.DataFrame([
                {
                    "Time (UTC)": a["created_at"],
                    "Actor": a["actor_username"],
                    "Note": a["note"],
                    "New Config": a["new_config_json"][:140],
                }
                for a in audits
            ]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No priority rule edits recorded yet.")

    st.markdown("#### Continuous Improvement: Failed / Low-Confidence Duration Extractions")
    dur_logs = get_duration_extraction_logs(user, limit=50)
    if dur_logs:
        st.dataframe(
            pd.DataFrame([
                {
                    "Incident ID": d["incident_id"],
                    "Status": d["status"],
                    "Found Hours": d["found_hours"],
                    "Matched Text": d["matched_text"],
                    "Confidence": d["confidence"],
                    "Reason": d["reason"],
                    "Complaint Text": d["text"][:140],
                    "Created (UTC)": d["created_at"],
                }
                for d in dur_logs
            ]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No failed or low-confidence duration extractions logged yet.")

    st.markdown("#### Admin Priority Corrections (Training Feedback)")
    fb_logs = get_priority_feedback_logs(user, limit=50)
    if fb_logs:
        st.dataframe(
            pd.DataFrame([
                {
                    "Incident ID": f["incident_id"],
                    "Corrected Priority": f["corrected_priority"],
                    "Duration (h)": f["duration_hours"],
                    "Matched Text": f["matched_text"],
                    "Corrected By": f["corrected_by_username"],
                    "Text": f["text"][:140],
                    "Created (UTC)": f["created_at"],
                }
                for f in fb_logs
            ]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No priority corrections logged yet.")


def _render_admin_oversight_tab(user: dict) -> None:
    """Super Admin Oversight: admin metrics, unassigned escalation alerts, district handoff, audit filter."""
    if "sa_oversight_flash" in st.session_state:
        st.success(st.session_state.pop("sa_oversight_flash"))

    st.markdown("### Admin Oversight Dashboard")

    try:
        admin_rows = get_admin_oversight_metrics(user)
    except PermissionError as exc:
        st.error(f"Permission denied: {exc}")
        return

    unread_esc = get_super_admin_notifications(user, unread_only=True, limit=100)
    m1, m2, m3 = st.columns(3)
    m1.metric("Managed Admins", len(admin_rows))
    m2.metric("Unread Escalation Alerts", len(unread_esc))
    m3.metric("Total Overdue in Admin Scopes", sum(r["overdue_in_district"] for r in admin_rows))

    if admin_rows:
        st.dataframe(
            pd.DataFrame([
                {
                    "Admin": r["username"],
                    "Full Name": r["full_name"],
                    "Active": "Yes" if r["is_active"] else "No",
                    "Districts": r["districts"],
                    "Awaiting Assignment": r["awaiting_assignment"],
                    "Avg Time to Assign (h)": r["avg_time_to_assign_hours"] if r["avg_time_to_assign_hours"] is not None else None,
                    "Overdue in Districts": r["overdue_in_district"],
                    "Reassignments": r["reassignment_count"],
                    "Status Overrides": r["override_count"],
                }
                for r in admin_rows
            ]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No admin accounts found.")

    st.markdown("---")
    st.markdown("### Reassign District Jurisdiction to Another Admin")

    all_districts = get_districts(active_only=True)
    active_admins = [u for u in get_all_users(user) if u["role"] == "admin" and u["is_active"]]
    if all_districts and active_admins:
        rcol1, rcol2 = st.columns(2)
        dist_map = {d["id"]: d["name"] for d in all_districts}
        adm_map = {a["id"]: f"{a['username']} ({a['full_name']})" for a in active_admins}
        with rcol1:
            sel_dist = st.selectbox(
                "District to Reassign / Hand Off",
                options=list(dist_map.keys()),
                format_func=lambda did: dist_map[did],
                key="sa_reassign_dist_sel",
            )
        with rcol2:
            sel_adm = st.selectbox(
                "Target Active Admin",
                options=list(adm_map.keys()),
                format_func=lambda aid: adm_map[aid],
                key="sa_reassign_adm_sel",
            )
        if st.button("Reassign District to Admin", key="sa_reassign_dist_btn", type="primary"):
            try:
                reassign_district_to_admin(user, sel_dist, sel_adm)
                st.session_state["sa_oversight_flash"] = (
                    f"Reassigned district '{dist_map[sel_dist]}' to admin '{adm_map[sel_adm]}'."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))
    else:
        st.caption("Need at least one active district and one active admin for district handoff.")

    st.markdown("---")
    st.markdown("### Filtered Audit Log (by Admin / District)")

    all_admins_all = [u for u in get_all_users(user) if u["role"] in ("admin", "super_admin")]
    adm_filter_opts: list[int | None] = [None, *[a["id"] for a in all_admins_all]]
    adm_filter_lbls: dict[int | None, str] = {
        None: "All Admins",
        **{a["id"]: f"{a['username']} ({a['role']})" for a in all_admins_all},
    }
    dist_filter_opts: list[int | None] = [None, *[d["id"] for d in all_districts]]
    dist_filter_lbls: dict[int | None, str] = {
        None: "All Districts",
        **{d["id"]: d["name"] for d in all_districts},
    }

    af1, af2 = st.columns(2)
    with af1:
        f_adm = st.selectbox(
            "Filter by Actor (Admin / Super Admin)",
            options=adm_filter_opts,
            format_func=lambda aid: adm_filter_lbls.get(aid, str(aid)),
            key="sa_audit_adm_filter",
        )
    with af2:
        f_dist = st.selectbox(
            "Filter by District",
            options=dist_filter_opts,
            format_func=lambda did: dist_filter_lbls.get(did, str(did)),
            key="sa_audit_dist_filter",
        )

    audit_rows = get_audit_logs_filtered(user, admin_user_id=f_adm, district_id=f_dist, limit=150)
    if audit_rows:
        st.dataframe(
            pd.DataFrame([
                {
                    "Time (UTC)": r["created_at"],
                    "Incident ID": r["incident_id"],
                    "District": r["district_name"] or "-",
                    "Actor": r["actor_username"],
                    "Role": r["actor_role"] or "-",
                    "Field": r["field_name"],
                    "Old Value": r["old_value"] or "-",
                    "New Value": r["new_value"] or "-",
                    "Remark": (r["remark"] or "")[:120],
                }
                for r in audit_rows
            ]),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No audit records match the selected filters.")


def _render_locations_tab(user: dict) -> None:
    """Super Admin Locations view: AP official India Post summary, stats, and reload control."""
    if "sa_loc_flash" in st.session_state:
        st.success(st.session_state.pop("sa_loc_flash"))

    st.markdown("### Location Directory & Jurisdiction Data")
    default_st = get_default_state()
    stats = get_location_stats()

    st.caption(
        f"Configured default region: {default_st['name'] if default_st else 'Andhra Pradesh'} "
        "(official data.gov.in India Post pincode directory)."
    )

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("States", stats["states"])
    k2.metric("Districts", stats["districts"])
    k3.metric("Mandals", stats["mandals"])
    k4.metric("Areas / Post Offices", stats["areas"])
    k5.metric("Pincode Mappings", stats["pincodes"])

    if st.button("Reload Official Location CSV", key="sa_reload_loc_btn", type="primary"):
        try:
            summary = reload_locations_admin(user)
            st.session_state["sa_loc_flash"] = (
                f"Location load completed: {summary['loaded_districts']} districts added, "
                f"{summary['loaded_areas']} areas added, {summary['loaded_pincodes']} pincodes added, "
                f"{summary['skipped_rows']} rows skipped."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Location reload failed: {exc}")

    logs = stats.get("recent_loads", [])
    if logs:
        st.markdown("#### Recent Location Import Logs")
        st.dataframe(pd.DataFrame(logs), width="stretch", hide_index=True)

    dists = get_districts(active_only=True)
    if dists:
        st.markdown("#### District Self-Claim Policy (Default: Disabled)")
        d_map = {d["id"]: d for d in dists}
        scol1, scol2 = st.columns([2, 1], vertical_alignment="bottom")
        with scol1:
            sel_sc_dist = st.selectbox(
                "Select District",
                options=list(d_map.keys()),
                format_func=lambda did: f"{d_map[did]['name']} (Self-claim: {'ON' if d_map[did].get('allow_self_claim') else 'OFF'})",
                key="sa_sc_dist_sel",
            )
        cur_sc = bool(d_map[sel_sc_dist].get("allow_self_claim"))
        with scol2:
            if st.button(
                "Disable Self-Claim" if cur_sc else "Enable Self-Claim",
                key="sa_sc_toggle_btn",
                width="stretch",
            ):
                set_district_self_claim(user, sel_sc_dist, not cur_sc)
                st.session_state["sa_loc_flash"] = (
                    f"Self-claim {'disabled' if cur_sc else 'enabled'} for {d_map[sel_sc_dist]['name']}."
                )
                st.rerun()

        st.markdown("#### Loaded Districts")
        st.dataframe(
            pd.DataFrame([
                {
                    "ID": d["id"],
                    "District": d["name"],
                    "Self-Claim Allowed": "Yes" if d.get("allow_self_claim") else "No",
                }
                for d in dists
            ]),
            width="stretch",
            hide_index=True,
        )


def render_super_admin_page(user: dict) -> None:
    from analytics.anomaly import detect_anomalies
    run_unassigned_escalation_check()
    detect_anomalies(notify=True)

    display_name = html.escape(str(user.get("full_name", user.get("username", ""))))
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Super Admin Control Panel</h2>
        <p class="cgs-muted" style="margin:0 0 24px;">Logged in as <b>{display_name}</b></p>
        """
    )

    unread_notifs = get_super_admin_notifications(user, unread_only=True, limit=15)
    if unread_notifs:
        with st.expander(f"Unassigned Escalation & Oversight Alerts ({len(unread_notifs)} unread)", expanded=True):
            if st.button("Mark All Alerts Read", key="sa_mark_all_notifs_read"):
                mark_all_notifications_read(user)
                st.rerun()
            for n in unread_notifs:
                ncol1, ncol2 = st.columns([5, 1], vertical_alignment="center")
                with ncol1:
                    st.warning(f"{n['title']}: {n['message']} ({n['created_at']} UTC)")
                with ncol2:
                    if st.button("Mark Read", key=f"sa_mark_notif_{n['id']}"):
                        mark_notification_read(user, n["id"])
                        st.rerun()

    (
        tab_complaints,
        tab_oversight,
        tab_forecast,
        tab_review,
        tab_escalations,
        tab_perf,
        tab_users,
        tab_departments,
        tab_locations,
        tab_sla,
        tab_prio_rules,
        tab_ml,
    ) = st.tabs([
        "Complaints",
        "Admin Oversight",
        "Forecast and Predictions",
        "Review Queue",
        "Escalations",
        "Performance",
        "User Management",
        "Departments",
        "Locations",
        "SLA Settings",
        "Priority Rules",
        "ML Models",
    ])

    with tab_complaints:
        render_staff_dashboard(user, key_prefix="sa")

    with tab_oversight:
        _render_admin_oversight_tab(user)

    with tab_forecast:
        render_forecast_and_predictions_tab(user, key_prefix="sa")

    with tab_review:
        render_review_queue_tab(user, key_prefix="sa")

    with tab_escalations:
        render_escalations_tab(user, key_prefix="sa")

    with tab_perf:
        render_performance_tab(user)

    with tab_users:
        _render_user_management(user)

    with tab_departments:
        _render_department_management(user)

    with tab_locations:
        _render_locations_tab(user)

    with tab_sla:
        _render_sla_settings(user)

    with tab_prio_rules:
        _render_priority_rules_tab(user)

    with tab_ml:
        _render_ml_models_tab(user)
