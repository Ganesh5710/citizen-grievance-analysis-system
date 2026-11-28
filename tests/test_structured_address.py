"""
tests/test_structured_address.py
End-to-end verification of the Structured Citizen Address Form, Full Address Hierarchy,
Official India Post DIGIPIN, Location-based Duplicate Detection, Privacy & Streamlit AppTest:
  1. Parent filtering at all levels + parent change clearing children
  2. Pincode auto-fill, unknown/invalid rejection, multi-area selection, & pincode vs dropdown conflict
  3. Server-side mismatched hierarchy chain rejection (mismatched area/district/state/mandal/tv)
  4. Official DIGIPIN encode/decode test vectors, charset/length/bounds rejection, hyphenated vs plain, faraway flag
  5. Landmark-only village submission allowed; neither street nor landmark rejected
  6. District Admin routing + Admin/Super Admin location correction re-routing & audit log
  7. Duplicate detection priority cascade (DIGIPIN cell -> Area -> Pincode)
  8. Non-destructive DB migration preserving legacy complaints & backfilling formatted_address
  9. Idempotent India Post + LGD loaders with AP 2022 district normalization & skipped-row reporting
 10. Public transparency & status lookup omit house_no/street/landmark/DIGIPIN/formatted_address (<5 suppressed)
 11. Streamlit AppTest interactive cascading selects & pincode auto-fill without any map picker
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from streamlit.testing.v1 import AppTest

import core.analytics_geo as geo_mod
import core.auth as auth_mod
import core.clusters as cl_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.locations as loc_mod
import core.privacy as privacy_mod
import core.public_status as pub_mod
from geo.digipin import decode_digipin, encode_digipin, normalize_digipin

_SAVED_ENV: dict[str, str | None] = {}
_TMP_DIR = ""


def setUpModule():
    global _TMP_DIR
    for k in (
        "CGS_DB_PATH",
        "CGS_UPLOADS_DIR",
        "CGS_SEED_PASSWORD",
        "CGS_SUPERADMIN_PASSWORD",
        "CGS_ADMIN_PASSWORD",
        "CGS_EMPLOYEE_PASSWORD",
        "CGS_DAILY_SUBMISSION_LIMIT",
        "CGS_SUBMISSION_COOLDOWN_SECONDS",
        "CGS_DIGIPIN_MISMATCH_KM",
        "CGS_DUPLICATE_MATCH_LEVEL",
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_struct_addr_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_addr.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "TestSeed@2026"
    os.environ.pop("CGS_SUPERADMIN_PASSWORD", None)
    os.environ.pop("CGS_ADMIN_PASSWORD", None)
    os.environ.pop("CGS_EMPLOYEE_PASSWORD", None)
    os.environ["CGS_DAILY_SUBMISSION_LIMIT"] = "100"
    os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"
    os.environ["CGS_DIGIPIN_MISMATCH_KM"] = "25.0"
    os.environ["CGS_DUPLICATE_MATCH_LEVEL"] = "auto"

    db_mod.init_db()
    db_mod.seed_demo_accounts()


def tearDownModule():
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if _TMP_DIR:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)


class TestStructuredAddressAndDigipin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ip_csv = Path(_TMP_DIR) / "ip_sample.csv"
        ip_rows = [
            ["officename", "pincode", "district", "statename", "latitude", "longitude"],
            ["Arundelpet S.O", "522002", "GUNTUR", "ANDHRA PRADESH", "16.3067", "80.4365"],
            ["Brodipet B.O", "522002", "Guntur", "Andhra Pradesh", "16.3080", "80.4401"],
            ["Pagolu B.O", "521126", "KRISHNA", "ANDHRA PRADESH", "16.1200", "80.9300"],
            ["Atmakur S.O", "516247", "SPSR NELLORE", "ANDHRA PRADESH", "14.6200", "79.6200"],
            ["Kadapa H.O", "516001", "Y.S.R.", "ANDHRA PRADESH", "14.4673", "78.8242"],
            ["BadPin B.O", "522", "Guntur", "Andhra Pradesh", "", ""],
            ["MissingDist B.O", "522099", "", "Andhra Pradesh", "", ""],
            ["OtherState B.O", "500001", "Hyderabad", "Telangana", "17.3850", "78.4867"],
        ]
        with open(cls.ip_csv, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(ip_rows)

        cls.lgd_csv = Path(_TMP_DIR) / "lgd_sample.csv"
        lgd_rows = [
            ["State", "District", "Mandal", "Village", "Village Code", "Pincode", "Category"],
            ["Andhra Pradesh", "Guntur", "Guntur Urban", "Arundelpet", "590001", "522002", "Urban"],
            ["Andhra Pradesh", "Guntur", "Phirangipuram", "Phirangipuram Town", "590214", "522529", "Rural"],
            ["Andhra Pradesh", "Krishna", "Challapalli", "Pagolu", "591200", "521126", "Rural"],
            ["Andhra Pradesh", "Sri Potti Sriramulu Nellore", "Atmakur", "Atmakur", "592100", "516247", "Urban"],
            ["Andhra Pradesh", "Y.S.R. Kadapa", "Kadapa", "Kadapa", "593100", "516001", "Urban"],
            ["Andhra Pradesh", "Ananthapuramu", "", "NoMandalRow", "594100", "515001", "Rural"],
        ]
        with open(cls.lgd_csv, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(lgd_rows)

        # Also add a non-India / second State row directly for parent-child filtering tests
        cls.ip_rep_1 = loc_mod.load_locations_from_csv(cls.ip_csv)
        cls.lgd_rep_1 = loc_mod.load_lgd_villages_csv(cls.lgd_csv)

        cls.india = loc_mod.get_default_country()
        cls.ap = loc_mod.get_default_state(cls.india["id"])
        dists = {d["name"]: d["id"] for d in loc_mod.get_districts(cls.ap["id"])}
        cls.guntur_id = dists["Guntur"]
        cls.krishna_id = dists["Krishna"]
        cls.nellore_id = dists["Sri Potti Sriramulu Nellore"]
        cls.kadapa_id = dists["Y.S.R. Kadapa"]

        cls.sa = auth_mod.login("superadmin", "TestSeed@2026")
        assert cls.sa is not None
        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_id = depts["Water"]

        cls.adm_guntur = auth_mod.create_admin_account(cls.sa, "sa_guntur", "Guntur Admin", "Pass@1234", [cls.guntur_id])
        cls.adm_krishna = auth_mod.create_admin_account(cls.sa, "sa_krishna", "Krishna Admin", "Pass@1234", [cls.krishna_id])
        cls.emp_guntur = auth_mod.create_user_account(
            cls.sa, "emp_gnt_addr", "Gnt Officer", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=cls.guntur_id,
        )
        cls.cit1 = auth_mod.register("addr_cit_1", "Rama Rao", "Pass@1234", phone="9876501111", email="rama@example.com")
        cls.cit2 = auth_mod.register("addr_cit_2", "Sita Devi", "Pass@1234", phone="9876502222", email="sita@example.com")

    def test_01_loader_idempotent_district_normalization_and_skipped_rows(self):
        # Verify post-2022 AP aliases unified cleanly: SPSR NELLORE + Sri Potti Sriramulu Nellore -> same ID
        self.assertEqual(self.ip_rep_1["skipped_reasons"]["invalid_pincode"], 1)
        self.assertEqual(self.ip_rep_1["skipped_reasons"]["missing_district"], 1)
        self.assertEqual(self.ip_rep_1["skipped_reasons"]["filtered_other_state"], 1)
        self.assertEqual(self.lgd_rep_1["skipped_reasons"]["missing_mandal"], 1)
        self.assertIsNotNone(self.nellore_id)
        self.assertIsNotNone(self.kadapa_id)

        # Second load adds 0 duplicates (idempotent)
        ip_rep_2 = loc_mod.load_locations_from_csv(self.ip_csv)
        lgd_rep_2 = loc_mod.load_lgd_villages_csv(self.lgd_csv)
        self.assertEqual(ip_rep_2["loaded_districts"], 0)
        self.assertEqual(ip_rep_2["loaded_areas"], 0)
        self.assertEqual(ip_rep_2["loaded_pincodes"], 0)
        self.assertEqual(lgd_rep_2["loaded_mandals"], 0)
        self.assertEqual(lgd_rep_2["loaded_towns_villages"], 0)

    def test_02_parent_filters_children_at_every_level(self):
        mandals_gnt = loc_mod.get_mandals(self.guntur_id)
        mandals_krs = loc_mod.get_mandals(self.krishna_id)
        self.assertTrue(any(m["name"] == "Guntur Urban" for m in mandals_gnt))
        self.assertFalse(any(m["name"] == "Challapalli" for m in mandals_gnt))
        self.assertTrue(any(m["name"] == "Challapalli" for m in mandals_krs))

        m_gnt_urb = next(m["id"] for m in mandals_gnt if m["name"] == "Guntur Urban")
        tvs_urb = loc_mod.get_towns_villages(self.guntur_id, mandal_id=m_gnt_urb)
        self.assertEqual([t["name"] for t in tvs_urb], ["Arundelpet"])

        areas_tv = loc_mod.get_areas(self.guntur_id, mandal_id=m_gnt_urb, town_village_id=tvs_urb[0]["id"])
        self.assertTrue(any(a["name"] == "Arundelpet S.O" for a in areas_tv))
        self.assertFalse(any("Pagolu" in a["name"] for a in areas_tv))

    def test_03_pincode_auto_fill_and_server_side_mismatch_rejection(self):
        # Single-area pincode (521126 -> Pagolu, Krishna) vs multi-area pincode (522002 -> Arundelpet & Brodipet)
        p_single = loc_mod.lookup_pincode("521126")
        self.assertIsNotNone(p_single)
        self.assertEqual(p_single["district_id"], self.krishna_id)
        self.assertEqual(len(p_single["areas"]), 1)

        p_multi = loc_mod.lookup_pincode("522002")
        self.assertIsNotNone(p_multi)
        self.assertEqual(p_multi["district_id"], self.guntur_id)
        self.assertEqual(len(p_multi["areas"]), 2)

        self.assertIsNone(loc_mod.lookup_pincode("12345"))
        self.assertIsNone(loc_mod.lookup_pincode("599999"))

        # Server-side rejects mismatched chains even if UI is bypassed
        pagolu_area_id = p_single["areas"][0]["area_id"]
        with self.assertRaises(ValueError):
            # Guntur district with Krishna area
            loc_mod.resolve_location_selection(district_id=self.guntur_id, area_id=pagolu_area_id)
        with self.assertRaises(ValueError):
            # Krishna district with Guntur pincode
            loc_mod.resolve_location_selection(pincode="522002", district_id=self.krishna_id)
        with self.assertRaises(ValueError):
            # Guntur mandal paired with Krishna area
            m_gnt = loc_mod.get_mandals(self.guntur_id)[0]["id"]
            loc_mod.resolve_location_selection(district_id=self.guntur_id, mandal_id=m_gnt, area_id=pagolu_area_id)

    def test_04_digipin_official_test_vectors_validation_and_faraway_flag(self):
        # Official India Post test vectors
        self.assertEqual(encode_digipin(28.622788, 77.213033), "39J49LL8T4")
        self.assertEqual(encode_digipin(28.622788, 77.213033, hyphenated=True), "39J-49L-L8T4")
        self.assertEqual(encode_digipin(13.11179621, 80.20264269), "4T396F42L7")

        dec_plain = decode_digipin("39J49LL8T4")
        dec_hyph = decode_digipin("39J-49L-L8T4")
        self.assertEqual(dec_plain["digipin"], "39J49LL8T4")
        self.assertEqual(dec_hyph["digipin"], "39J49LL8T4")
        self.assertAlmostEqual(dec_plain["latitude"], 28.622788, places=4)
        self.assertAlmostEqual(dec_plain["longitude"], 77.213033, places=4)

        # Reject invalid length, characters ('0', '1', 'I', 'O', 'A'), and out-of-bounds coordinates
        for bad in ("", "39J49L", "39J49LL8T0", "39J49LL8TO", "39J-49-LL8T4", "123456789A"):
            with self.assertRaises(ValueError):
                normalize_digipin(bad)
        with self.assertRaises(ValueError):
            encode_digipin(45.0, 77.0)  # outside India maxLat 38.5
        with self.assertRaises(ValueError):
            encode_digipin(16.3, 105.0)  # outside India maxLon 99.5

        # Faraway DIGIPIN (Delhi Dak Bhawan in Guntur complaint) flags for staff review yet STILL submits cleanly
        p_gnt = loc_mod.lookup_pincode("522002")
        inc = comp_mod.submit_complaint(
            self.cit1,
            self.water_id,
            "Drinking water pipe burst near canal road junction for 2 days",
            pincode="522002",
            area_id=p_gnt["areas"][0]["area_id"],
            house_no="12-4",
            street="",
            landmark="Near Canal Road Arch",
            digipin="39J-49L-L8T4",
            require_street_or_landmark=True,
        )
        saved = next(c for c in comp_mod.get_complaints(self.sa) if c["incident_id"] == inc)
        self.assertEqual(saved["status"], "Awaiting Assignment")
        self.assertTrue(saved["digipin_flagged"])
        self.assertEqual(saved["digipin"], "39J-49L-L8T4")
        self.assertIn("Canal Road Arch", saved["formatted_address"])

    def test_05_village_landmark_vs_empty_street_and_landmark_and_privacy_export(self):
        p_krs = loc_mod.lookup_pincode("521126")
        pagolu_area_id = p_krs["areas"][0]["area_id"]

        # Reject when neither street nor landmark given (strict form mode)
        with self.assertRaises(ValueError):
            comp_mod.submit_complaint(
                self.cit1,
                self.water_id,
                "Village overhead tank valve leaking continuously since 2 days",
                pincode="521126",
                area_id=pagolu_area_id,
                street="",
                landmark="",
                require_street_or_landmark=True,
            )

        # Village complaint with NO street but a clear landmark succeeds
        inc_v = comp_mod.submit_complaint(
            self.cit1,
            self.water_id,
            "Village overhead tank valve leaking continuously since 2 days",
            pincode="521126",
            area_id=pagolu_area_id,
            house_no="D.No. 3-11",
            street="",
            landmark="Beside Gram Panchayat Building",
            digipin=encode_digipin(16.1200, 80.9300, hyphenated=True),
            require_street_or_landmark=True,
        )
        saved = next(c for c in comp_mod.get_complaints(self.sa) if c["incident_id"] == inc_v)
        self.assertEqual(saved["landmark"], "Beside Gram Panchayat Building")
        self.assertFalse(saved["digipin_flagged"])

        # DPDP export includes structured address & DIGIPIN
        exp = json.loads(privacy_mod.export_own_data(self.cit1, "json")["content"])
        v_rec = next(c for c in exp["complaints"] if c["incident_id"] == inc_v)
        self.assertEqual(v_rec["house_no"], "D.No. 3-11")
        self.assertEqual(v_rec["landmark"], "Beside Gram Panchayat Building")
        self.assertTrue(bool(v_rec["digipin"]))

    def test_06_location_correction_reroutes_district_and_audits(self):
        p_gnt = loc_mod.lookup_pincode("522002")
        inc = comp_mod.submit_complaint(
            self.cit1,
            self.water_id,
            "Broken drinking water supply main at boundary lane for 3 days",
            pincode="522002",
            area_id=p_gnt["areas"][0]["area_id"],
            street="Boundary Lane",
            require_street_or_landmark=True,
        )
        c = next(r for r in comp_mod.get_complaints(self.adm_guntur) if r["incident_id"] == inc)
        comp_mod.assign_task(self.adm_guntur, c["id"], self.emp_guntur["id"])

        # Admin corrects district from Guntur -> Krishna with mandatory reason
        with self.assertRaises(ValueError):
            comp_mod.update_complaint_location(
                self.adm_guntur, c["id"], reason="no", district_id=self.krishna_id
            )
        p_krs = loc_mod.lookup_pincode("521126")
        moved = comp_mod.update_complaint_location(
            self.adm_guntur,
            c["id"],
            reason="Verified street lies under Krishna district Pagolu circle",
            state_id=self.ap["id"],
            district_id=self.krishna_id,
            area_id=p_krs["areas"][0]["area_id"],
        )
        self.assertEqual(moved["district_id"], self.krishna_id)
        self.assertEqual(moved["status"], "Awaiting Assignment")
        self.assertIsNone(moved["assigned_to"])

        # Visible now to Krishna Admin, no longer visible to Guntur Admin
        krs_ids = {r["incident_id"] for r in comp_mod.get_complaints(self.adm_krishna)}
        gnt_ids = {r["incident_id"] for r in comp_mod.get_complaints(self.adm_guntur)}
        self.assertIn(inc, krs_ids)
        self.assertNotIn(inc, gnt_ids)
        hist = comp_mod.get_complaint_history(self.sa, c["id"])
        self.assertTrue(any(h["field_name"] == "location" and "Krishna" in h["new_value"] for h in hist))

    def test_07_duplicate_detection_cascade_digipin_area_pincode(self):
        p_multi = loc_mod.lookup_pincode("522002")
        arundel_id = next(a["area_id"] for a in p_multi["areas"] if "Arundelpet" in a["area_name"])
        brodipet_id = next(a["area_id"] for a in p_multi["areas"] if "Brodipet" in a["area_name"])

        dp_cell = encode_digipin(16.30670, 80.43650)
        dp_neighbor = encode_digipin(16.30672, 80.43651)  # ~2.5m neighbor

        inc_a = comp_mod.submit_complaint(
            self.cit1,
            self.water_id,
            "Burst municipal drinking water valve flooding street corner near library",
            pincode="522002",
            area_id=arundel_id,
            street="Library Road",
            digipin=dp_cell,
            require_street_or_landmark=True,
        )
        comp_a = next(c for c in comp_mod.get_complaints(self.sa) if c["incident_id"] == inc_a)

        # 1. DIGIPIN cell match takes priority when DIGIPIN neighbors exist
        res_dp = cl_mod.find_similar_open_complaints(
            self.water_id,
            "Drinking water valve burst flooding road corner near library",
            digipin=dp_neighbor,
            area_id=arundel_id,
            pincode="522002",
            force_tfidf=True,
        )
        self.assertEqual(res_dp["matched_by"], "digipin_cell")
        self.assertTrue(any(m["complaint_id"] == comp_a["id"] for m in res_dp["matches"]))

        # 2. Without DIGIPIN -> falls back to same area
        res_area = cl_mod.find_similar_open_complaints(
            self.water_id,
            "Drinking water valve burst flooding road corner near library",
            area_id=arundel_id,
            pincode="522002",
            force_tfidf=True,
        )
        self.assertEqual(res_area["matched_by"], "area")
        self.assertTrue(any(m["complaint_id"] == comp_a["id"] for m in res_area["matches"]))

        # 3. Different area (Brodipet) with SAME pincode (522002) -> falls back to same pincode!
        res_pin = cl_mod.find_similar_open_complaints(
            self.water_id,
            "Drinking water valve burst flooding road corner near library",
            area_id=brodipet_id,
            pincode="522002",
            force_tfidf=True,
        )
        self.assertEqual(res_pin["matched_by"], "pincode")
        self.assertTrue(any(m["complaint_id"] == comp_a["id"] for m in res_pin["matches"]))

        # If match_level='area', Brodipet won't match Arundelpet
        res_strict_area = cl_mod.find_similar_open_complaints(
            self.water_id,
            "Drinking water valve burst flooding road corner near library",
            area_id=brodipet_id,
            pincode="522002",
            match_level="area",
            force_tfidf=True,
        )
        self.assertFalse(any(m["complaint_id"] == comp_a["id"] for m in res_strict_area["matches"]))

    def test_08_migration_backfills_formatted_address_and_public_hides_pii(self):
        # Insert an older pre-migration row directly with empty formatted_address -> run init_db() -> verified backfill!
        with db_mod.get_connection() as conn, conn:
            conn.execute(
                """
                INSERT INTO complaints
                    (incident_id, user_id, text, location, department_id, state_id, district_id,
                     area_id, pincode, priority, status, formatted_address)
                VALUES ('CIV-LEGACY99', ?, 'Old legacy drainage pipe block near bus stand',
                        'Bus Stand Road', ?, ?, ?, ?, '522002', 'Medium', 'Awaiting Assignment', '')
                """,
                (self.cit1["id"], self.water_id, self.ap["id"], self.guntur_id, loc_mod.lookup_pincode("522002")["areas"][0]["area_id"]),
            )
        db_mod.init_db()
        with db_mod.get_connection() as conn:
            r = conn.execute(
                "SELECT country_id, formatted_address FROM complaints WHERE incident_id = 'CIV-LEGACY99'"
            ).fetchone()
            self.assertEqual(r["country_id"], 1)
            self.assertIn("Bus Stand Road", r["formatted_address"])
            self.assertIn("Guntur", r["formatted_address"])

        # Public dashboard & public ticket status omit house_no/street/landmark/digipin/formatted_address
        pub_dash = geo_mod.get_public_transparency_by_district()
        pub_str = json.dumps(pub_dash)
        self.assertNotIn("D.No. 3-11", pub_str)
        self.assertNotIn("Beside Gram Panchayat", pub_str)
        self.assertNotIn("39J", pub_str)
        # Fewer than 5 in Krishna -> Krishna suppressed
        self.assertGreaterEqual(pub_dash["suppressed_groups_count"], 1)

        pub_ticket = pub_mod.lookup_public_status("CIV-LEGACY99", "1111", client_key="addr_pub_chk")
        self.assertNotIn("formatted_address", pub_ticket)
        self.assertNotIn("house_no", pub_ticket)
        self.assertNotIn("digipin", pub_ticket)

    def test_09_streamlit_apptest_cascading_selects_and_pincode_autofill(self):
        at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
        at.session_state["user"] = self.cit1
        at.run()

        # Ensure app loaded cleanly for citizen and choose first active department ('Raise Complaint')
        self.assertEqual(len(at.exception), 0)
        raise_btns = [b for b in at.button if b.label == "Raise Complaint"]
        self.assertGreater(len(raise_btns), 0)
        raise_btns[0].click().run()

        # Ensure NO map / folium picker exists anywhere in citizen UI
        self.assertFalse(any("Latitude" in (ti.label or "") or "Drop Map Pin" in (e.label or "") for e in at.expander for ti in at.text_input))

        # 1) Cascading selects: State -> Guntur District -> Mandal -> Town/Village -> Area auto-fills pincode
        # Note: Streamlit AppTest selectbox.options / select() operate on formatted labels (and session_state holds underlying IDs)
        at.selectbox(key="cit_addr_dist_0").select("Guntur").run()
        mandal_sb = at.selectbox(key="cit_addr_mandal_0")
        self.assertIn("Guntur Urban", mandal_sb.options)
        self.assertNotIn("Challapalli", mandal_sb.options)
        mandal_sb.select("Guntur Urban").run()

        tv_sb = at.selectbox(key="cit_addr_tv_0")
        self.assertIn("Arundelpet", tv_sb.options)
        tv_sb.select("Arundelpet").run()

        area_sb = at.selectbox(key="cit_addr_area_0")
        arundel_lbl = next(lbl for lbl in area_sb.options if "Arundelpet S.O" in lbl)
        area_sb.select(arundel_lbl).run()
        self.assertEqual(at.text_input(key="cit_addr_pin_0").value, "522002")

        # Changing District clears Mandal/Town/Area & Pincode
        at.selectbox(key="cit_addr_dist_0").select("Krishna").run()
        self.assertEqual(at.text_input(key="cit_addr_pin_0").value, "")
        self.assertIsNone(at.session_state["cit_addr_mandal_0"])
        self.assertIsNone(at.session_state["cit_addr_area_0"])

        # 2) Entering a different pincode (522002 in Guntur) while Krishna is selected detects conflict & applies Guntur
        at.text_input(key="cit_addr_pin_0").input("522002").run()
        use_pin_btns = [b for b in at.button if "Use Pincode Location" in b.label]
        self.assertEqual(len(use_pin_btns), 1)
        use_pin_btns[0].click().run()
        self.assertEqual(at.session_state["cit_addr_dist_0"], self.guntur_id)

        # 3) Entering single-area pincode 521126 on a fresh session auto-fills District (Krishna) & preselects Pagolu B.O
        at2 = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20)
        at2.session_state["user"] = self.cit1
        at2.run()
        next(b for b in at2.button if b.label == "Raise Complaint").click().run()
        at2.text_input(key="cit_addr_pin_0").input("521126").run()
        self.assertEqual(at2.session_state["cit_addr_dist_0"], self.krishna_id)
        self.assertIsNotNone(at2.session_state["cit_addr_area_0"])


if __name__ == "__main__":
    unittest.main()
