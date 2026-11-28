"""
ui/citizen.py
Citizen (role='user') portal:
  Tab 1: Two-step department selection -> complaint form with optional photo attachments.
  Tab 2: My Complaints table, detail, photo additions (while open), and Resolved feedback/reopen.
"""

from datetime import datetime, timezone
import html

import streamlit as st

from analytics.resolution_predictor import predict_resolution_hours
from core.clusters import find_similar_open_complaints, support_complaint
from core.complaints import (
    get_complaint_detail,
    get_complaint_feedback,
    get_complaints,
    submit_complaint,
    submit_feedback,
    withdraw_complaint,
)
from core.privacy import anonymize_own_account, export_own_data, get_privacy_notice_markdown
from core.departments import get_active_departments
from core.images import MAX_IMAGES_PER_COMPLAINT, add_images
from core.locations import (
    get_areas,
    get_default_state,
    get_districts,
    get_mandals,
    get_states,
    lookup_pincode,
)
from core.moderation import generate_captcha_challenge, is_captcha_enabled, verify_captcha_answer
from core.sla import AUTO_CLOSE_DAYS, parse_utc
from ml.classifier import predict_complaint
from ui.shared import (
    build_complaints_df,
    render_attachments,
    render_complaint_card,
    render_history,
    render_html,
)


def _render_department_selection(active_depts: list[dict]) -> None:
    """Step 1: Responsive card grid of active departments."""
    st.markdown("### Step 1: Select a Department")
    st.caption("Choose the department relevant to your grievance.")

    if not active_depts:
        st.warning("No active departments are currently available. Please try again later.")
        return

    cols_per_row = 3
    for start in range(0, len(active_depts), cols_per_row):
        row_depts = active_depts[start : start + cols_per_row]
        cols = st.columns(cols_per_row)
        for col, dept in zip(cols, row_depts):
            with col:
                name_esc = html.escape(dept["name"])
                desc_esc = html.escape(dept["description"])
                render_html(
                    f"""
                    <div class="cgs-card cgs-dept-card">
                        <div>
                            <h4 style="margin:0 0 8px 0;">{name_esc}</h4>
                            <p class="cgs-muted" style="font-size:14px;margin:0;">{desc_esc}</p>
                        </div>
                    </div>
                    """
                )
                if st.button(
                    "Raise Complaint",
                    key=f"citizen_choose_dept_{dept['id']}",
                    type="primary",
                    width="stretch",
                ):
                    st.session_state["citizen_selected_dept_id"] = dept["id"]
                    st.rerun()


def _render_complaint_form(user: dict, dept: dict) -> None:
    """Step 2: Complaint form with Pincode lookup or Cascading AP location + map pin + photos."""
    col_hdr, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_hdr:
        st.markdown(f"### Step 2: File Complaint - {dept['name']}")
        st.caption(dept["description"])
    with col_btn:
        if st.button("Change department", key="citizen_change_dept_btn", width="stretch"):
            st.session_state.pop("citizen_selected_dept_id", None)
            st.rerun()

    form_gen = st.session_state.get("citizen_form_gen", 0)

    # Interactive location resolution (Pincode Lookup vs Cascading Dropdowns)
    st.markdown("#### Grievance Location")
    states = get_states(active_only=True)
    default_state = get_default_state()

    sel_state_id: int | None = default_state["id"] if default_state else None
    sel_district_id: int | None = None
    sel_mandal_id: int | None = None
    sel_area_id: int | None = None
    sel_pincode: str = ""
    centroid_lat: float | None = None
    centroid_lon: float | None = None

    if states:
        loc_mode = st.radio(
            "Choose location entry method",
            options=["Lookup by 6-digit Pincode", "Cascading Select (State / District / Area)"],
            horizontal=True,
            key=f"citizen_loc_mode_{form_gen}",
        )
        if loc_mode.startswith("Lookup"):
            pin_val = st.text_input(
                "Enter 6-digit Pincode (required)",
                max_chars=6,
                placeholder="e.g. 521126",
                key=f"citizen_pincode_in_{form_gen}",
            ).strip()
            if pin_val:
                pin_info = lookup_pincode(pin_val)
                if pin_info is None:
                    st.error("Pincode must be 6 digits and present in the India Post directory.")
                else:
                    sel_pincode = pin_info["pincode"]
                    sel_state_id = pin_info["state_id"]
                    sel_district_id = pin_info["district_id"]
                    areas_for_pin = pin_info["areas"]
                    c_l1, c_l2, c_l3 = st.columns(3)
                    c_l1.text_input(
                        "State", value=pin_info["state_name"], disabled=True, key=f"c_ro_st_{form_gen}"
                    )
                    c_l2.text_input(
                        "District", value=pin_info["district_name"], disabled=True, key=f"c_ro_dist_{form_gen}"
                    )
                    with c_l3:
                        area_opts = [a["area_id"] for a in areas_for_pin]
                        area_lbls = {a["area_id"]: a["area_name"] for a in areas_for_pin}
                        sel_area_id = st.selectbox(
                            "Confirm Area / Post Office",
                            options=area_opts,
                            format_func=lambda aid: area_lbls.get(aid, str(aid)),
                            key=f"citizen_pin_area_{form_gen}_{sel_pincode}",
                        )
                    chosen_a = next((a for a in areas_for_pin if a["area_id"] == sel_area_id), None)
                    if chosen_a:
                        sel_mandal_id = chosen_a.get("mandal_id")
                        centroid_lat = chosen_a.get("latitude")
                        centroid_lon = chosen_a.get("longitude")
        else:
            state_ids = [s["id"] for s in states]
            state_lbls = {s["id"]: s["name"] for s in states}
            def_st_idx = state_ids.index(sel_state_id) if sel_state_id in state_ids else 0
            cc1, cc2 = st.columns(2)
            with cc1:
                sel_state_id = st.selectbox(
                    "State",
                    options=state_ids,
                    index=def_st_idx,
                    format_func=lambda sid: state_lbls.get(sid, str(sid)),
                    key=f"citizen_casc_state_{form_gen}",
                )
            districts = get_districts(sel_state_id, active_only=True)
            with cc2:
                if districts:
                    dist_ids = [d["id"] for d in districts]
                    dist_lbls = {d["id"]: d["name"] for d in districts}
                    sel_district_id = st.selectbox(
                        "District",
                        options=dist_ids,
                        format_func=lambda did: dist_lbls.get(did, str(did)),
                        key=f"citizen_casc_dist_{form_gen}_{sel_state_id}",
                    )
                else:
                    st.warning("No districts loaded for this state.")

            if sel_district_id is not None:
                mandals = get_mandals(sel_district_id, active_only=True)
                cc3, cc4 = st.columns(2)
                if mandals:
                    with cc3:
                        m_ids = [m["id"] for m in mandals]
                        m_lbls = {m["id"]: m["name"] for m in mandals}
                        sel_mandal_id = st.selectbox(
                            "Mandal",
                            options=m_ids,
                            format_func=lambda mid: m_lbls.get(mid, str(mid)),
                            key=f"citizen_casc_mandal_{form_gen}_{sel_district_id}",
                        )
                    areas = get_areas(sel_district_id, mandal_id=sel_mandal_id, active_only=True)
                else:
                    areas = get_areas(sel_district_id, active_only=True)
                    with cc3:
                        st.caption("Mandal level not present in India Post directory (district -> area mapping used).")

                with cc4:
                    if areas:
                        a_ids = [a["id"] for a in areas]
                        a_lbls = {a["id"]: f"{a['name']} ({a.get('pincode') or ''})" for a in areas}
                        sel_area_id = st.selectbox(
                            "Area / Village / Post Office",
                            options=a_ids,
                            format_func=lambda aid: a_lbls.get(aid, str(aid)),
                            key=f"citizen_casc_area_{form_gen}_{sel_district_id}_{sel_mandal_id}",
                        )
                        chosen_area = next((a for a in areas if a["id"] == sel_area_id), None)
                        if chosen_area:
                            sel_pincode = chosen_area.get("pincode") or ""
                            centroid_lat = chosen_area.get("latitude")
                            centroid_lon = chosen_area.get("longitude")
                            st.caption(f"Auto-filled Pincode: **{sel_pincode or 'N/A'}**")
    else:
        st.caption("Location directory not yet loaded; enter manual landmark below.")

    # Optional interactive map pin picker
    pin_lat_key = f"citizen_map_lat_{form_gen}"
    pin_lon_key = f"citizen_map_lon_{form_gen}"
    with st.expander("Drop Map Pin (Optional)"):
        base_lat = centroid_lat if centroid_lat is not None else 16.5062
        base_lon = centroid_lon if centroid_lon is not None else 80.6480
        try:
            import folium
            from streamlit_folium import st_folium

            fmap = folium.Map(location=[base_lat, base_lon], zoom_start=12)
            cur_lat = st.session_state.get(pin_lat_key)
            cur_lon = st.session_state.get(pin_lon_key)
            if cur_lat is not None and cur_lon is not None:
                folium.Marker([cur_lat, cur_lon], tooltip="Pinned Location").add_to(fmap)
            map_out = st_folium(fmap, height=280, width=None, key=f"citizen_pin_map_{form_gen}_{base_lat}_{base_lon}")
            if isinstance(map_out, dict) and map_out.get("last_clicked"):
                st.session_state[pin_lat_key] = round(float(map_out["last_clicked"]["lat"]), 6)
                st.session_state[pin_lon_key] = round(float(map_out["last_clicked"]["lng"]), 6)
        except Exception:
            st.caption("Map widget unavailable offline; manual coordinates optional.")
        mc1, mc2 = st.columns(2)
        manual_lat = mc1.number_input(
            "Latitude (optional)",
            value=float(st.session_state.get(pin_lat_key) or (centroid_lat or 0.0)),
            format="%.6f",
            key=f"citizen_lat_in_{form_gen}",
        )
        manual_lon = mc2.number_input(
            "Longitude (optional)",
            value=float(st.session_state.get(pin_lon_key) or (centroid_lon or 0.0)),
            format="%.6f",
            key=f"citizen_lon_in_{form_gen}",
        )
        chosen_lat = manual_lat if manual_lat != 0.0 else centroid_lat
        chosen_lon = manual_lon if manual_lon != 0.0 else centroid_lon

    # clear_on_submit=False preserves typed text when validation fails
    cap_on = is_captcha_enabled()
    if cap_on and f"cit_cap_{form_gen}" not in st.session_state:
        st.session_state[f"cit_cap_{form_gen}"] = generate_captcha_challenge()

    with st.form(key=f"citizen_submit_form_{form_gen}", clear_on_submit=False):
        complaint_text = st.text_area(
            "Complaint Description (required, min 10 characters)",
            placeholder="Describe the issue clearly...",
            height=150,
            key=f"citizen_complaint_desc_{form_gen}",
        )
        location = st.text_input(
            "Landmark / Street Detail (optional if area selected)",
            placeholder="e.g. Near Bus Stop / Door No. 4-12",
            key=f"citizen_complaint_loc_{form_gen}",
        )
        uploaded_files = st.file_uploader(
            "Attach photos (optional, max 3 images, JPG/PNG/WebP, max 5 MB each)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key=f"citizen_upload_{form_gen}",
        )
        cap_ans = None
        if cap_on:
            cap_q, _ = st.session_state[f"cit_cap_{form_gen}"]
            cap_ans = st.text_input(cap_q, key=f"citizen_cap_ans_{form_gen}")
        st.caption("Privacy notice: EXIF and GPS location data are automatically removed from uploaded photos.")
        col_sub, col_chk = st.columns([2, 1])
        with col_sub:
            submitted = st.form_submit_button("Submit Complaint", type="primary")
        with col_chk:
            preview_clicked = st.form_submit_button("Preview AI Hint")

    if preview_clicked:
        if len(complaint_text.strip()) < 10:
            st.warning("Enter at least 10 characters in the description to preview the AI suggestion.")
        else:
            pred = predict_complaint(complaint_text.strip(), department_hint=dept["name"])
            sugg_dept = pred.get("department", dept["name"])
            conf_pct = round(float(pred.get("confidence", 0.0)) * 100)
            sugg_prio = pred.get("priority", "Medium")
            if sugg_dept.lower() != dept["name"].lower():
                st.info(
                    f"Non-blocking AI hint: This description looks similar to '{sugg_dept}' "
                    f"({conf_pct}% confidence, estimated priority: {sugg_prio}). "
                    "You can keep your selected department or click 'Change department' above."
                )
            else:
                st.success(
                    f"AI matches '{dept['name']}' ({conf_pct}% confidence, estimated priority: {sugg_prio})."
                )

    if uploaded_files:
        st.caption("Photo preview:")
        preview_cols = st.columns(min(len(uploaded_files), 3))
        for pcol, fobj in zip(preview_cols, uploaded_files[:MAX_IMAGES_PER_COMPLAINT]):
            with pcol:
                try:
                    st.image(fobj.getvalue(), caption=fobj.name, width="stretch")
                except Exception:
                    st.caption(f"Preview unavailable: {fobj.name}")

    if submitted:
        if cap_on and not verify_captcha_answer(st.session_state[f"cit_cap_{form_gen}"][1], cap_ans):
            st.error("Security arithmetic check failed. Please enter the correct answer.")
            st.session_state[f"cit_cap_{form_gen}"] = generate_captcha_challenge()
            return
        text_stripped = complaint_text.strip()
        loc_stripped = location.strip()

        if len(text_stripped) < 10:
            st.error("Please describe your complaint in at least 10 characters.")
            return
        if states and not sel_pincode and sel_district_id is None:
            st.error("Please enter a valid 6-digit pincode or select District and Area.")
            return
        if not states and len(loc_stripped) < 2:
            st.error("Location / Landmark is required.")
            return
        if uploaded_files and len(uploaded_files) > MAX_IMAGES_PER_COMPLAINT:
            st.error(f"You can attach at most {MAX_IMAGES_PER_COMPLAINT} photos per complaint.")
            return

        try:
            sim_res = find_similar_open_complaints(
                dept["id"],
                text_stripped,
                latitude=chosen_lat,
                longitude=chosen_lon,
                pincode=sel_pincode or None,
                state_id=sel_state_id,
                district_id=sel_district_id,
                mandal_id=sel_mandal_id,
                area_id=sel_area_id,
            )
            matches = sim_res.get("matches", [])
            if matches:
                st.session_state["citizen_dup_check"] = {
                    "dept_id": dept["id"],
                    "dept_name": dept["name"],
                    "text": text_stripped,
                    "location": loc_stripped,
                    "files": [
                        (f.name, f.getvalue()) for f in (uploaded_files or [])
                    ],
                    "latitude": chosen_lat,
                    "longitude": chosen_lon,
                    "pincode": sel_pincode or None,
                    "state_id": sel_state_id,
                    "district_id": sel_district_id,
                    "mandal_id": sel_mandal_id,
                    "area_id": sel_area_id,
                    "matches": matches,
                    "method": sim_res.get("method", "tfidf_fallback"),
                }
                st.rerun()

            incident_id = submit_complaint(
                user,
                dept["id"],
                text_stripped,
                loc_stripped,
                files=uploaded_files or None,
                latitude=chosen_lat,
                longitude=chosen_lon,
                pincode=sel_pincode or None,
                state_id=sel_state_id,
                district_id=sel_district_id,
                mandal_id=sel_mandal_id,
                area_id=sel_area_id,
            )
            saved = next(
                (c for c in get_complaints(user) if c["incident_id"] == incident_id),
                None,
            )
            priority_text = saved["priority"] if saved else "Assigned"
            ml_hint = ""
            if saved and saved.get("ml_suggested_department") and (
                saved["ml_suggested_department"].lower() != dept["name"].lower()
            ):
                conf_pct = round(float(saved.get("ml_confidence") or 0.0) * 100)
                ml_hint = (
                    f" Note: AI suggested '{saved['ml_suggested_department']}' ({conf_pct}% confidence); "
                    "an administrator may review category routing if needed."
                )
            est_info = predict_resolution_hours(
                dept["name"],
                priority_text,
                district_name=saved.get("district_name", "") if saved else "",
                created_at=saved.get("created_at") if saved else None,
                text=text_stripped,
            )
            st.session_state["citizen_last_ticket"] = {
                "incident_id": incident_id,
                "department":  dept["name"],
                "priority":    priority_text,
                "ml_hint":     ml_hint,
                "est_label":   est_info.get("label", ""),
            }
            st.session_state.pop("citizen_selected_dept_id", None)
            st.session_state["citizen_form_gen"] = form_gen + 1
            st.rerun()
        except PermissionError as exc:
            st.error(f"Permission denied: {exc}")
        except ValueError as exc:
            st.error(f"Validation error: {exc}")
        except Exception as exc:
            st.error(f"Unexpected error: {exc}")

    dup_ctx = st.session_state.get("citizen_dup_check")
    if dup_ctx and dup_ctx.get("dept_id") == dept["id"]:
        n_sim = len(dup_ctx["matches"])
        st.warning(
            f"{n_sim} similar open complaint(s) found nearby (detector: {dup_ctx['method']}). "
            "You can support an existing issue instead of filing a duplicate, or continue anyway."
        )
        for m in dup_ctx["matches"][:5]:
            mc1, mc2 = st.columns([4, 1], vertical_alignment="center")
            with mc1:
                st.markdown(
                    f"**#{m['incident_id']}** ({m['status']}, Priority: {m['priority']}, "
                    f"{m['distance_m']} m away, sim {int(m['similarity'] * 100)}%, "
                    f"Reports: {m['report_count']})  \n{m['text_preview']}"
                )
            with mc2:
                can_sup = int(m["user_id"]) != int(user["id"])
                if st.button(
                    "Support Existing",
                    key=f"citizen_sup_btn_{m['complaint_id']}_{form_gen}",
                    disabled=not can_sup,
                    width="stretch",
                ):
                    try:
                        sup_res = support_complaint(user, m["complaint_id"])
                        st.session_state.pop("citizen_dup_check", None)
                        st.session_state["citizen_last_ticket"] = {
                            "incident_id": m["incident_id"],
                            "department": dept["name"],
                            "priority": m["priority"],
                            "ml_hint": f" Supported existing cluster (total reports: {sup_res.get('report_count', 1)}).",
                        }
                        st.session_state.pop("citizen_selected_dept_id", None)
                        st.session_state["citizen_form_gen"] = form_gen + 1
                        st.rerun()
                    except (PermissionError, ValueError) as exc:
                        st.error(str(exc))

        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("Continue Submitting as New Complaint", key=f"citizen_force_new_{form_gen}", type="primary"):
                try:
                    incident_id = submit_complaint(
                        user,
                        dup_ctx["dept_id"],
                        dup_ctx["text"],
                        dup_ctx["location"],
                        files=dup_ctx["files"] or None,
                        latitude=dup_ctx["latitude"],
                        longitude=dup_ctx["longitude"],
                        pincode=dup_ctx["pincode"],
                        state_id=dup_ctx["state_id"],
                        district_id=dup_ctx["district_id"],
                        mandal_id=dup_ctx["mandal_id"],
                        area_id=dup_ctx["area_id"],
                    )
                    st.session_state.pop("citizen_dup_check", None)
                    st.session_state["citizen_last_ticket"] = {
                        "incident_id": incident_id,
                        "department": dept["name"],
                        "priority": "Awaiting Assignment",
                        "ml_hint": "",
                    }
                    st.session_state.pop("citizen_selected_dept_id", None)
                    st.session_state["citizen_form_gen"] = form_gen + 1
                    st.rerun()
                except (PermissionError, ValueError) as exc:
                    st.error(str(exc))
        with cc2:
            if st.button("Cancel / Edit Description", key=f"citizen_cancel_dup_{form_gen}"):
                st.session_state.pop("citizen_dup_check", None)
                st.rerun()


def _render_citizen_feedback_section(user: dict, detail: dict) -> None:
    """Display feedback history and, for Resolved complaints within 7 days, Confirm/Reopen form."""
    feedback_list = get_complaint_feedback(user, detail["id"])
    if feedback_list:
        st.markdown("#### Resolution Feedback History")
        for fb in feedback_list:
            outcome = "Confirmed Resolved" if fb["accepted"] else "Marked Not Resolved (Reopened)"
            comment_esc = html.escape(fb["comment"] or "No comment").replace("\n", "<br/>")
            render_html(
                f"""
                <div class="cgs-card">
                    <b>{html.escape(outcome)}</b> &nbsp;|&nbsp; Rating: {fb['rating']}/5
                    <br/><small class="cgs-muted">Submitted on {html.escape(fb['created_at'])} UTC</small>
                    <p style="margin-top:6px;margin-bottom:0;">{comment_esc}</p>
                </div>
                """
            )

    if detail.get("status") != "Resolved":
        return

    current_cycle = int(detail.get("reopen_count", 0))
    already_responded = any(fb["resolution_cycle"] == current_cycle for fb in feedback_list)
    if already_responded:
        return

    resolved_dt = parse_utc(detail.get("resolved_at"))
    if resolved_dt is not None:
        age_secs = (datetime.now(timezone.utc) - resolved_dt).total_seconds()
        if age_secs > AUTO_CLOSE_DAYS * 86400:
            st.info("The 7-day feedback window for this resolution has expired.")
            return

    st.markdown("#### Resolution Confirmation")
    st.info(
        "This complaint has been marked Resolved. Confirm resolution to close it, "
        "or report it as not resolved within 7 days to reopen it."
    )

    ver = f"{detail['id']}_{current_cycle}"
    rating = st.slider(
        "Satisfaction Rating (1 = Poor, 5 = Excellent)",
        min_value=1,
        max_value=5,
        value=5,
        key=f"citizen_fb_rating_{ver}",
    )
    fb_comment = st.text_area(
        "Comment (optional when confirming; required when reopening)",
        placeholder="Share your feedback or explain why the issue persists...",
        key=f"citizen_fb_comment_{ver}",
    )

    col_ok, col_reopen = st.columns(2)
    with col_ok:
        if st.button("Confirm resolved", key=f"citizen_fb_confirm_{ver}", type="primary", width="stretch"):
            try:
                submit_feedback(user, detail["id"], accepted=True, rating=int(rating), comment=fb_comment.strip())
                st.session_state["citizen_my_flash"] = (
                    f"Complaint {detail['incident_id']} confirmed resolved and closed."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))

    with col_reopen:
        if st.button("Not resolved", key=f"citizen_fb_reopen_{ver}", width="stretch"):
            try:
                submit_feedback(user, detail["id"], accepted=False, rating=int(rating), comment=fb_comment.strip())
                st.session_state["citizen_my_flash"] = (
                    f"Complaint {detail['incident_id']} reopened and returned for follow-up."
                )
                st.rerun()
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))


def render_citizen_page(user: dict) -> None:
    display_name = html.escape(str(user.get("full_name", user.get("username", "Citizen"))))
    render_html(
        f"""
        <div class="cgs-top-line"></div>
        <h2 style="margin:0 0 4px;">Welcome, <span class="cgs-accent">{display_name}</span></h2>
        <p class="cgs-muted" style="margin:0 0 24px;">Citizen Portal - Submit and track your grievances</p>
        """
    )

    tab_submit, tab_my, tab_privacy = st.tabs(["Submit Complaint", "My Complaints", "Privacy & Data Export"])

    with tab_submit:
        if "citizen_last_ticket" in st.session_state:
            ticket = st.session_state.pop("citizen_last_ticket")
            est_part = (
                f" | Estimated Resolution (estimate only): {ticket['est_label']}"
                if ticket.get("est_label")
                else ""
            )
            st.success(
                f"Complaint submitted. Ticket ID: {ticket['incident_id']} | "
                f"Department: {ticket['department']} | "
                f"Detected Priority: {ticket['priority']}{est_part}."
                f"{ticket.get('ml_hint', '')}"
            )

        active_depts = get_active_departments()
        selected_dept_id = st.session_state.get("citizen_selected_dept_id")
        selected_dept = next((d for d in active_depts if d["id"] == selected_dept_id), None)

        if selected_dept is None:
            if selected_dept_id is not None:
                st.session_state.pop("citizen_selected_dept_id", None)
            _render_department_selection(active_depts)
        else:
            _render_complaint_form(user, selected_dept)

    with tab_my:
        if "citizen_my_flash" in st.session_state:
            st.success(st.session_state.pop("citizen_my_flash"))

        st.markdown("### My Submitted Complaints")
        try:
            complaints = get_complaints(user)
        except PermissionError as exc:
            st.error(f"Permission denied: {exc}")
            return
        except Exception as exc:
            st.error(f"Could not load complaints: {exc}")
            return

        if not complaints:
            st.info("You have not submitted any complaints yet. Use the Submit Complaint tab to get started.")
            return

        total = len(complaints)
        pending = sum(1 for c in complaints if c.get("status") == "Pending")
        resolved = sum(1 for c in complaints if c.get("status") in ("Resolved", "Closed"))

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Submitted", total)
        col2.metric("Pending", pending)
        col3.metric("Resolved / Closed", resolved)

        st.markdown("---")
        st.dataframe(build_complaints_df(complaints), width="stretch", hide_index=True)

        st.markdown("---")
        st.markdown("### Complaint Detail and History")

        incident_ids = [c.get("incident_id") for c in complaints if c.get("incident_id")]
        select_key = "citizen_detail_select"
        if select_key in st.session_state and st.session_state[select_key] not in incident_ids:
            st.session_state[select_key] = incident_ids[0]

        selected_id = st.selectbox(
            "Select an Incident ID to view status, photos, and remarks",
            options=incident_ids,
            key=select_key,
        )

        if selected_id:
            matched = next((c for c in complaints if c.get("incident_id") == selected_id), None)
            if matched:
                try:
                    detail = get_complaint_detail(user, matched["id"])
                except PermissionError as exc:
                    st.error(f"Permission denied: {exc}")
                    return
                except Exception as exc:
                    st.error(f"Could not load complaint detail: {exc}")
                    return

                render_complaint_card(detail)
                render_attachments(user, detail["id"], "citizen_my")

                remaining = MAX_IMAGES_PER_COMPLAINT - int(detail.get("photo_count", 0))
                if detail.get("status") not in ("Resolved", "Closed") and remaining > 0:
                    more_gen = st.session_state.get("citizen_more_upload_gen", 0)
                    with st.expander(f"Add more photos ({remaining} remaining)"):
                        more_files = st.file_uploader(
                            "Select additional photos (JPG/PNG/WebP, max 5 MB each)",
                            type=["jpg", "jpeg", "png", "webp"],
                            accept_multiple_files=True,
                            key=f"citizen_more_photos_{detail['id']}_{more_gen}",
                        )
                        st.caption("Privacy notice: EXIF and GPS location data are removed automatically.")
                        if st.button("Upload Photos", key=f"citizen_more_btn_{detail['id']}_{more_gen}"):
                            if not more_files:
                                st.error("Select at least one photo to upload.")
                            else:
                                try:
                                    add_images(user, detail["id"], more_files)
                                    st.session_state["citizen_more_upload_gen"] = more_gen + 1
                                    st.session_state["citizen_my_flash"] = "Photos attached successfully."
                                    st.rerun()
                                except (PermissionError, ValueError) as exc:
                                    st.error(str(exc))

                if detail.get("status") not in ("Resolved", "Closed", "Withdrawn"):
                    if detail.get("withdrawal_requested"):
                        st.warning(
                            f"Withdrawal requested: {detail.get('withdrawal_request_reason', '')} (Pending Admin review)."
                        )
                    else:
                        w_label = (
                            "Withdraw Complaint"
                            if detail.get("status") in ("Submitted", "Awaiting Assignment", "Pending") and detail.get("assigned_to") is None
                            else "Request Complaint Withdrawal (Requires Admin Approval)"
                        )
                        with st.expander(w_label):
                            w_reason = st.text_input(
                                "Reason for withdrawal (required, min 5 chars)",
                                key=f"citizen_withdraw_reason_{detail['id']}",
                            )
                            if st.button(w_label, key=f"citizen_withdraw_btn_{detail['id']}"):
                                try:
                                    res_w = withdraw_complaint(user, detail["id"], w_reason.strip())
                                    st.session_state["citizen_my_flash"] = (
                                        "Complaint withdrawn."
                                        if res_w.get("withdrawal_action") == "withdrawn"
                                        else "Withdrawal request submitted to Admin."
                                    )
                                    st.rerun()
                                except (PermissionError, ValueError) as exc:
                                    st.error(str(exc))

                _render_citizen_feedback_section(user, detail)
                st.markdown("#### Status History and Remarks")
                render_history(detail["id"], user)

    with tab_privacy:
        st.markdown("### My Privacy & DPDP Act 2023 Tools")
        st.markdown(get_privacy_notice_markdown())
        st.markdown("---")
        st.markdown("#### Export My Personal Data")
        col_ex1, col_ex2 = st.columns(2)
        with col_ex1:
            json_pkg = export_own_data(user, "json")
            st.download_button(
                "Download My Data (JSON)",
                data=json_pkg["content"],
                file_name=json_pkg["filename"],
                mime="application/json",
                key=f"cit_export_json_{user['id']}",
            )
        with col_ex2:
            csv_pkg = export_own_data(user, "csv")
            st.download_button(
                "Download My Data (CSV)",
                data=csv_pkg["content"],
                file_name=csv_pkg["filename"],
                mime="text/csv",
                key=f"cit_export_csv_{user['id']}",
            )

        st.markdown("---")
        with st.expander("Delete / Anonymize My Account"):
            st.warning(
                "This permanently anonymizes your username, full name, phone, and email while retaining "
                "de-identified civic complaint statistics and audit records as required for governance."
            )
            del_reason = st.text_input("Reason (optional)", key=f"cit_anon_reason_{user['id']}")
            del_confirm = st.checkbox("I understand this action is permanent", key=f"cit_anon_confirm_{user['id']}")
            if st.button("Anonymize & Delete My Account", key=f"cit_anon_btn_{user['id']}"):
                if not del_confirm:
                    st.error("Confirm the checkbox before proceeding.")
                else:
                    try:
                        anonymize_own_account(user, del_reason.strip())
                        st.session_state.clear()
                        st.success("Your account has been anonymized and deactivated.")
                        st.rerun()
                    except (PermissionError, ValueError) as exc:
                        st.error(str(exc))
