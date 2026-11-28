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
from core.public_status import build_ticket_tracking_url, generate_ticket_qr_png
from core.departments import get_active_departments
from core.images import MAX_IMAGES_PER_COMPLAINT, add_images
from core.locations import (
    build_formatted_address,
    get_areas,
    get_countries,
    get_default_country,
    get_default_state,
    get_districts,
    get_mandals,
    get_states,
    get_towns_villages,
    lookup_pincode,
)
from core.moderation import generate_captcha_challenge, is_captcha_enabled, verify_captcha_answer
from core.sla import AUTO_CLOSE_DAYS, parse_utc
from geo.digipin import check_digipin_against_location
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
    """Step 2: Structured Address Form (Country > State > District > Mandal > Town/Village > Area > Pincode + DIGIPIN) & photos."""
    col_hdr, col_btn = st.columns([4, 1], vertical_alignment="center")
    with col_hdr:
        st.markdown(f"### Step 2: File Complaint - {dept['name']}")
        st.caption(dept["description"])
    with col_btn:
        if st.button("Change department", key="citizen_change_dept_btn", width="stretch"):
            st.session_state.pop("citizen_selected_dept_id", None)
            st.rerun()

    form_gen = st.session_state.get("citizen_form_gen", 0)

    # Interactive structured address (lives OUTSIDE st.form so cascading & pincode callbacks rerun immediately)
    st.markdown("#### Structured Address")
    st.caption(
        "Privacy notice: House number, street, landmark, and DIGIPIN are personal data visible only to you "
        "and authorized staff handling your grievance, and are included in your DPDP data export/erasure."
    )

    country_key = f"cit_addr_country_{form_gen}"
    state_key = f"cit_addr_state_{form_gen}"
    dist_key = f"cit_addr_dist_{form_gen}"
    mandal_key = f"cit_addr_mandal_{form_gen}"
    tv_key = f"cit_addr_tv_{form_gen}"
    area_key = f"cit_addr_area_{form_gen}"
    pin_key = f"cit_addr_pin_{form_gen}"
    conflict_key = f"cit_addr_pin_conflict_{form_gen}"

    def _clear_children(from_level: str) -> None:
        order = ("country", "state", "district", "mandal", "tv", "area")
        keys_map = {
            "country": state_key,
            "state": dist_key,
            "district": mandal_key,
            "mandal": tv_key,
            "tv": area_key,
        }
        start_idx = order.index(from_level)
        for lvl in order[start_idx:]:
            k = keys_map.get(lvl)
            if k:
                st.session_state[k] = None
        if from_level in ("country", "state", "district", "mandal", "tv"):
            st.session_state[area_key] = None
            st.session_state.pop(conflict_key, None)

    def _on_country_change() -> None:
        _clear_children("country")
        st.session_state[pin_key] = ""

    def _on_state_change() -> None:
        _clear_children("state")
        st.session_state[pin_key] = ""

    def _on_dist_change() -> None:
        _clear_children("district")
        st.session_state[pin_key] = ""

    def _on_mandal_change() -> None:
        _clear_children("mandal")
        st.session_state[pin_key] = ""

    def _on_tv_change() -> None:
        _clear_children("tv")
        st.session_state[pin_key] = ""

    def _apply_pincode_info(pin_info: dict) -> None:
        st.session_state[country_key] = pin_info["country_id"]
        st.session_state[state_key] = pin_info["state_id"]
        st.session_state[dist_key] = pin_info["district_id"]
        st.session_state[mandal_key] = None
        st.session_state[tv_key] = None
        areas_pin = pin_info["areas"]
        if len(areas_pin) == 1:
            st.session_state[area_key] = areas_pin[0]["area_id"]
            if areas_pin[0].get("mandal_id"):
                st.session_state[mandal_key] = areas_pin[0]["mandal_id"]
            if areas_pin[0].get("town_village_id"):
                st.session_state[tv_key] = areas_pin[0]["town_village_id"]
        else:
            st.session_state[area_key] = None
        st.session_state.pop(conflict_key, None)

    def _on_pincode_change() -> None:
        raw_p = str(st.session_state.get(pin_key) or "").strip()
        st.session_state.pop(conflict_key, None)
        if len(raw_p) == 6 and raw_p.isdigit():
            pin_info = lookup_pincode(raw_p)
            if pin_info is not None:
                cur_dist = st.session_state.get(dist_key)
                if cur_dist is not None and int(cur_dist) != int(pin_info["district_id"]):
                    st.session_state[conflict_key] = pin_info
                else:
                    _apply_pincode_info(pin_info)

    countries = get_countries(active_only=True)
    default_country = get_default_country()
    if country_key not in st.session_state and default_country:
        st.session_state[country_key] = default_country["id"]

    sel_country_id: int | None = st.session_state.get(country_key)
    sel_state_id: int | None = None
    sel_district_id: int | None = None
    sel_mandal_id: int | None = None
    sel_tv_id: int | None = None
    sel_area_id: int | None = None
    sel_pincode: str = ""
    centroid_lat: float | None = None
    centroid_lon: float | None = None
    area_name_lbl = ""
    tv_name_lbl = ""
    mandal_name_lbl = ""
    dist_name_lbl = ""
    state_name_lbl = ""
    country_name_lbl = ""
    pin_lookup_info: dict | None = None

    # Top Pincode Lookup box (auto-fills Country/State/District/Area or detects conflict)
    p_col1, p_col2 = st.columns([1, 2], vertical_alignment="bottom")
    with p_col1:
        entered_pin = st.text_input(
            "6-Digit Pincode (auto-fills address)",
            max_chars=6,
            placeholder="e.g. 522002",
            key=pin_key,
            on_change=_on_pincode_change,
        ).strip()
    with p_col2:
        if entered_pin:
            if not (len(entered_pin) == 6 and entered_pin.isdigit()):
                st.error("Pincode must be exactly 6 digits.")
            else:
                pin_lookup_info = lookup_pincode(entered_pin)
                if pin_lookup_info is None:
                    st.error(f"Pincode '{entered_pin}' was not found in the official India Post directory.")
                elif len(pin_lookup_info["areas"]) > 1 and st.session_state.get(area_key) is None:
                    st.info(
                        f"Pincode {entered_pin} covers {len(pin_lookup_info['areas'])} areas in "
                        f"{pin_lookup_info['district_name']}. Please select your Area / Post Office below."
                    )
                else:
                    st.caption(
                        f"Pincode matched: {pin_lookup_info['district_name']}, {pin_lookup_info['state_name']}"
                    )

    conflict_info = st.session_state.get(conflict_key)
    if conflict_info:
        st.warning(
            f"Pincode {conflict_info['pincode']} belongs to {conflict_info['district_name']}, "
            f"{conflict_info['state_name']}, which differs from your selected District."
        )
        if st.button(
            f"Use Pincode Location ({conflict_info['district_name']}, {conflict_info['state_name']})",
            key=f"cit_apply_pin_conflict_{form_gen}",
            type="primary",
        ):
            _apply_pincode_info(conflict_info)
            st.rerun()

    # Cascading Dropdowns (hidden when a child level has no rows in DB)
    if countries:
        c_ids = [c["id"] for c in countries]
        c_lbls = {c["id"]: c["name"] for c in countries}
        if st.session_state.get(country_key) not in c_ids:
            st.session_state[country_key] = default_country["id"] if (default_country and default_country["id"] in c_ids) else c_ids[0]

        r1c1, r1c2, r1c3 = st.columns(3)
        with r1c1:
            sel_country_id = st.selectbox(
                "Country",
                options=c_ids,
                format_func=lambda cid: c_lbls.get(cid, str(cid)),
                key=country_key,
                on_change=_on_country_change,
            )
            country_name_lbl = c_lbls.get(sel_country_id, "")

        states = get_states(country_id=sel_country_id, active_only=True)
        default_state = get_default_state(country_id=sel_country_id)
        if states:
            s_ids = [s["id"] for s in states]
            s_lbls = {s["id"]: s["name"] for s in states}
            if state_key not in st.session_state or st.session_state.get(state_key) not in s_ids:
                st.session_state[state_key] = default_state["id"] if (default_state and default_state["id"] in s_ids) else s_ids[0]
            with r1c2:
                sel_state_id = st.selectbox(
                    "State",
                    options=s_ids,
                    format_func=lambda sid: s_lbls.get(sid, str(sid)),
                    key=state_key,
                    on_change=_on_state_change,
                )
                state_name_lbl = s_lbls.get(sel_state_id, "")

        districts = get_districts(sel_state_id, active_only=True) if sel_state_id is not None else []
        if districts:
            d_ids = [None, *[d["id"] for d in districts]]
            d_lbls = {None: "-- Select District --", **{d["id"]: d["name"] for d in districts}}
            d_map = {d["id"]: d for d in districts}
            if st.session_state.get(dist_key) not in d_ids:
                st.session_state[dist_key] = None
            with r1c3:
                sel_district_id = st.selectbox(
                    "District *",
                    options=d_ids,
                    format_func=lambda did: d_lbls.get(did, str(did)),
                    key=dist_key,
                    on_change=_on_dist_change,
                )
                if sel_district_id is not None:
                    d_obj = d_map[sel_district_id]
                    dist_name_lbl = d_obj["name"]
                    centroid_lat = d_obj.get("latitude")
                    centroid_lon = d_obj.get("longitude")

        # Sub-district (Mandal) -> Town/Village -> Area/Locality row (levels with 0 rows skipped)
        if sel_district_id is not None:
            mandals = get_mandals(sel_district_id, active_only=True)
            sub_cols = st.columns(3)
            col_cursor = 0

            if mandals:
                m_ids = [None, *[m["id"] for m in mandals]]
                m_lbls = {None: "-- All / Select Sub-district --", **{m["id"]: m["name"] for m in mandals}}
                if st.session_state.get(mandal_key) not in m_ids:
                    st.session_state[mandal_key] = None
                with sub_cols[col_cursor]:
                    sel_mandal_id = st.selectbox(
                        "Sub-district (Mandal / Taluk)",
                        options=m_ids,
                        format_func=lambda mid: m_lbls.get(mid, str(mid)),
                        key=mandal_key,
                        on_change=_on_mandal_change,
                    )
                    if sel_mandal_id is not None:
                        mandal_name_lbl = m_lbls.get(sel_mandal_id, "")
                col_cursor += 1

            tvs = get_towns_villages(sel_district_id, mandal_id=sel_mandal_id, active_only=True)
            if tvs:
                tv_ids = [None, *[tv["id"] for tv in tvs]]
                tv_lbls = {None: "-- Select Town / Village --", **{tv["id"]: tv["name"] for tv in tvs}}
                if st.session_state.get(tv_key) not in tv_ids:
                    st.session_state[tv_key] = None
                with sub_cols[col_cursor]:
                    sel_tv_id = st.selectbox(
                        "Town / Village",
                        options=tv_ids,
                        format_func=lambda tid: tv_lbls.get(tid, str(tid)),
                        key=tv_key,
                        on_change=_on_tv_change,
                    )
                    if sel_tv_id is not None:
                        tv_name_lbl = tv_lbls.get(sel_tv_id, "")
                col_cursor += 1

            if (
                pin_lookup_info is not None
                and int(pin_lookup_info["district_id"]) == int(sel_district_id)
                and sel_tv_id is None
                and sel_mandal_id is None
            ):
                areas = [
                    {
                        "id": a["area_id"],
                        "name": a["area_name"],
                        "pincode": pin_lookup_info["pincode"],
                        "latitude": a.get("latitude"),
                        "longitude": a.get("longitude"),
                        "mandal_id": a.get("mandal_id"),
                        "town_village_id": a.get("town_village_id"),
                    }
                    for a in pin_lookup_info["areas"]
                ]
            else:
                areas = get_areas(
                    sel_district_id,
                    mandal_id=sel_mandal_id,
                    town_village_id=sel_tv_id,
                    active_only=True,
                )

            if areas:
                a_ids = [None, *[a["id"] for a in areas]]
                a_lbls = {
                    None: "-- Select Area / Locality --",
                    **{a["id"]: f"{a['name']} ({a.get('pincode') or ''})" for a in areas},
                }
                a_map = {a["id"]: a for a in areas}
                if st.session_state.get(area_key) not in a_ids:
                    st.session_state[area_key] = None

                def _on_area_change() -> None:
                    chosen_aid = st.session_state.get(area_key)
                    if chosen_aid in a_map:
                        a_pin = str(a_map[chosen_aid].get("pincode") or "").strip()
                        if a_pin and not st.session_state.get(pin_key):
                            st.session_state[pin_key] = a_pin

                with sub_cols[min(col_cursor, 2)]:
                    sel_area_id = st.selectbox(
                        "Area / Locality (Post Office / Ward)",
                        options=a_ids,
                        format_func=lambda aid: a_lbls.get(aid, str(aid)),
                        key=area_key,
                        on_change=_on_area_change,
                    )
                    if sel_area_id is not None and sel_area_id in a_map:
                        ca = a_map[sel_area_id]
                        area_name_lbl = ca["name"]
                        sel_pincode = str(ca.get("pincode") or "").strip() or entered_pin
                        if ca.get("latitude") is not None and ca.get("longitude") is not None:
                            centroid_lat = ca["latitude"]
                            centroid_lon = ca["longitude"]
    else:
        st.caption("Location directory not yet loaded.")

    if not sel_pincode and len(entered_pin) == 6 and entered_pin.isdigit():
        sel_pincode = entered_pin

    # Free-text structured address fields + optional DIGIPIN (NO map picker anywhere)
    ft1, ft2 = st.columns(2)
    house_no = ft1.text_input(
        "House / Door / Flat Number (optional)",
        max_chars=80,
        placeholder="e.g. D.No. 4-12 / Flat 202",
        key=f"cit_addr_house_{form_gen}",
    )
    street = ft2.text_input(
        "Street / Colony / Road (optional if Landmark given)",
        max_chars=150,
        placeholder="e.g. Arundelpet 2nd Line",
        key=f"cit_addr_street_{form_gen}",
    )
    ft3, ft4 = st.columns([2, 1])
    landmark = ft3.text_input(
        "Landmark or Locality Description (required if no Street is entered)",
        max_chars=200,
        placeholder="e.g. Opposite ZP High School / Near Water Tank",
        key=f"cit_addr_landmark_{form_gen}",
    )
    digipin_input = ft4.text_input(
        "DIGIPIN (optional, 10 chars)",
        max_chars=14,
        placeholder="e.g. 4T396F42L7 or 4T3-96F-42L7",
        key=f"cit_addr_digipin_{form_gen}",
    ).strip()

    dp_preview = check_digipin_against_location(digipin_input, centroid_lat, centroid_lon)
    if dp_preview["provided"] and dp_preview["warning"]:
        st.warning(dp_preview["warning"])

    formatted_preview = build_formatted_address(
        house_no=house_no.strip(),
        street=street.strip(),
        landmark=landmark.strip(),
        area_name=area_name_lbl,
        town_village_name=tv_name_lbl,
        mandal_name=mandal_name_lbl,
        district_name=dist_name_lbl,
        state_name=state_name_lbl,
        pincode=sel_pincode,
        country_name=country_name_lbl,
        digipin=dp_preview.get("formatted") or dp_preview.get("normalized") or "",
    )
    if formatted_preview:
        st.info(f"**Address Summary (Review before submitting):** {formatted_preview}")

    st.markdown("#### Complaint Details")
    cap_on = is_captcha_enabled()
    if cap_on and f"cit_cap_{form_gen}" not in st.session_state:
        st.session_state[f"cit_cap_{form_gen}"] = generate_captcha_challenge()

    complaint_text = st.text_area(
        "Complaint Description (required, min 10 characters)",
        placeholder="Describe the issue clearly...",
        height=140,
        key=f"citizen_complaint_desc_{form_gen}",
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
        submitted = st.button("Submit Complaint", key=f"citizen_submit_btn_{form_gen}", type="primary")
    with col_chk:
        preview_clicked = st.button("Preview AI Hint", key=f"citizen_preview_ai_btn_{form_gen}")

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
        house_stripped = house_no.strip()
        street_stripped = street.strip()
        landmark_stripped = landmark.strip()

        if len(text_stripped) < 10:
            st.error("Please describe your complaint in at least 10 characters.")
            return
        if countries and not sel_pincode and sel_district_id is None:
            st.error("Please enter a valid 6-digit pincode or select a District.")
            return
        if not street_stripped and not landmark_stripped:
            st.error("Please enter either a Street / Colony / Road or a Landmark / Locality description.")
            return
        if uploaded_files and len(uploaded_files) > MAX_IMAGES_PER_COMPLAINT:
            st.error(f"You can attach at most {MAX_IMAGES_PER_COMPLAINT} photos per complaint.")
            return

        try:
            sim_res = find_similar_open_complaints(
                dept["id"],
                text_stripped,
                digipin=digipin_input or None,
                pincode=sel_pincode or None,
                state_id=sel_state_id,
                district_id=sel_district_id,
                mandal_id=sel_mandal_id,
                town_village_id=sel_tv_id,
                area_id=sel_area_id,
            )
            matches = sim_res.get("matches", [])
            if matches:
                st.session_state["citizen_dup_check"] = {
                    "dept_id": dept["id"],
                    "dept_name": dept["name"],
                    "text": text_stripped,
                    "house_no": house_stripped,
                    "street": street_stripped,
                    "landmark": landmark_stripped,
                    "digipin": digipin_input,
                    "files": [
                        (f.name, f.getvalue()) for f in (uploaded_files or [])
                    ],
                    "pincode": sel_pincode or None,
                    "country_id": sel_country_id,
                    "state_id": sel_state_id,
                    "district_id": sel_district_id,
                    "mandal_id": sel_mandal_id,
                    "town_village_id": sel_tv_id,
                    "area_id": sel_area_id,
                    "matches": matches,
                    "method": sim_res.get("method", "tfidf_fallback"),
                    "matched_by": sim_res.get("matched_by", "area"),
                }
                st.rerun()

            incident_id = submit_complaint(
                user,
                dept["id"],
                text_stripped,
                files=uploaded_files or None,
                pincode=sel_pincode or None,
                country_id=sel_country_id,
                state_id=sel_state_id,
                district_id=sel_district_id,
                mandal_id=sel_mandal_id,
                town_village_id=sel_tv_id,
                area_id=sel_area_id,
                house_no=house_stripped,
                street=street_stripped,
                landmark=landmark_stripped,
                digipin=digipin_input,
                require_street_or_landmark=True,
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
            if saved and saved.get("digipin_flagged"):
                ml_hint += " Note: Entered DIGIPIN was flagged as unverified/outside area for staff review."
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
        matched_lbl = str(dup_ctx.get("matched_by") or "area").replace("_", " ")
        st.warning(
            f"{n_sim} similar open complaint(s) nearby (matched by {matched_lbl}; detector: {dup_ctx['method']}). "
            "You can support an existing issue instead of filing a duplicate, or continue anyway."
        )
        for m in dup_ctx["matches"][:5]:
            mc1, mc2 = st.columns([4, 1], vertical_alignment="center")
            with mc1:
                m_loc_lbl = str(m.get("match_level") or matched_lbl).replace("_", " ")
                st.markdown(
                    f"**#{m['incident_id']}** ({m['status']}, Priority: {m['priority']}, "
                    f"match: {m_loc_lbl}, sim {int(m['similarity'] * 100)}%, "
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
                        files=dup_ctx["files"] or None,
                        pincode=dup_ctx["pincode"],
                        country_id=dup_ctx.get("country_id"),
                        state_id=dup_ctx["state_id"],
                        district_id=dup_ctx["district_id"],
                        mandal_id=dup_ctx["mandal_id"],
                        town_village_id=dup_ctx.get("town_village_id"),
                        area_id=dup_ctx["area_id"],
                        house_no=dup_ctx.get("house_no", ""),
                        street=dup_ctx.get("street", ""),
                        landmark=dup_ctx.get("landmark", ""),
                        digipin=dup_ctx.get("digipin", ""),
                        require_street_or_landmark=True,
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
            track_url = build_ticket_tracking_url(ticket["incident_id"])
            qr_col1, qr_col2 = st.columns([1, 4], vertical_alignment="center")
            with qr_col1:
                st.image(generate_ticket_qr_png(ticket["incident_id"]), width=130, caption="Scan to Track")
            with qr_col2:
                st.markdown(f"**Public Status Tracking Link:** `{track_url}`")
                st.download_button(
                    "Download Ticket QR (PNG)",
                    data=generate_ticket_qr_png(ticket["incident_id"]),
                    file_name=f"qr_{ticket['incident_id']}.png",
                    mime="image/png",
                    key=f"citizen_dl_qr_{ticket['incident_id']}",
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
                with st.expander(f"QR Code & Public Tracking Link — #{detail['incident_id']}"):
                    q_url = build_ticket_tracking_url(detail["incident_id"])
                    qc1, qc2 = st.columns([1, 4], vertical_alignment="center")
                    with qc1:
                        st.image(generate_ticket_qr_png(detail["incident_id"]), width=120)
                    with qc2:
                        st.markdown(f"**Tracking URL:** `{q_url}`")
                        st.download_button(
                            "Download Ticket QR (PNG)",
                            data=generate_ticket_qr_png(detail["incident_id"]),
                            file_name=f"qr_{detail['incident_id']}.png",
                            mime="image/png",
                            key=f"citizen_my_qr_{detail['id']}",
                        )
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
