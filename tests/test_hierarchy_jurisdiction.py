"""
tests/test_hierarchy_jurisdiction.py
Comprehensive test suite for Hierarchical Workflow & Location-Based Jurisdiction (AP Default):
  Test 1: Pincode lookup, cascading location selections, validation & centroid fallback
  Test 2: District & department jurisdiction isolation across Admins
  Test 3: Assignment rules (matching dept + jurisdiction, suggested least-loaded employee)
  Test 4: Employee restrictions (no assign/edit fields, assigned-only isolation, accept_task, after photos, closing note >= 10)
  Test 5: Unassigned escalation > 4h creating Super Admin alert & notification
  Test 6: Admin deactivation returns open district complaints to Awaiting Assignment & preserves history
  Test 7: Admin status override requires mandatory audit-logged reason
  Test 8: CSV loader reporting/mandal handling/idempotency + Public Transparency >= 5 suppression
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

import core.analytics_geo as geo_mod
import core.auth as auth_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.images as img_mod
import core.locations as loc_mod
import core.oversight as ov_mod

_SAVED_ENV: dict[str, str | None] = {}
_TMP_DIR: str = ""


def _make_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (180, 120), color=(30, 140, 90))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def setUpModule():
    global _TMP_DIR
    for k in (
        "CGS_DB_PATH",
        "CGS_UPLOADS_DIR",
        "CGS_SEED_PASSWORD",
        "CGS_SUPERADMIN_PASSWORD",
        "CGS_ADMIN_PASSWORD",
        "CGS_EMPLOYEE_PASSWORD",
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_hier_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_hier.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"
    os.environ.pop("CGS_SUPERADMIN_PASSWORD", None)
    os.environ.pop("CGS_ADMIN_PASSWORD", None)
    os.environ.pop("CGS_EMPLOYEE_PASSWORD", None)
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


class TestHierarchyAndJurisdiction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample_csv = Path(_TMP_DIR) / "ap_subset.csv"
        rows = [
            ["circlename", "regionname", "divisionname", "officename", "pincode", "officetype", "delivery", "district", "statename", "latitude", "longitude"],
            ["Andhra Pradesh Circle", "Vijayawada Region", "Guntur Division", "Arundelpet S.O", "522002", "PO", "Delivery", "Guntur", "ANDHRA PRADESH", "16.3067", "80.4365"],
            ["Andhra Pradesh Circle", "Vijayawada Region", "Guntur Division", "Brodipet B.O", "522002", "BO", "Non Delivery", "Guntur", "Andhra Pradesh", "16.3080", "80.4401"],
            ["Andhra Pradesh Circle", "Vijayawada Region", "Guntur Division", "Brodipet B.O", "522002", "BO", "Non Delivery", "Guntur", "Andhra Pradesh", "16.3080", "80.4401"],
            ["Andhra Pradesh Circle", "Visakhapatnam Region", "Visakhapatnam Division", "MVP Colony S.O", "530001", "PO", "Delivery", "Visakhapatnam", "ANDHRA PRADESH", "17.7380", "83.3320"],
            ["Andhra Pradesh Circle", "Kurnool Region", "Kurnool Division", "Nandyal R.S S.O", "518501", "PO", "Delivery", "Nandyal", "ANDHRA PRADESH", "15.4800", "78.4800"],
            ["Telangana Circle", "Hyderabad Region", "Hyderabad Division", "Ameerpet S.O", "500016", "PO", "Delivery", "Hyderabad", "TELANGANA", "17.4375", "78.4483"],
            ["Andhra Pradesh Circle", "Test Region", "Test Div", "BadPin S.O", "5220", "PO", "Delivery", "Guntur", "ANDHRA PRADESH", "", ""],
            ["Andhra Pradesh Circle", "Test Region", "Test Div", "MissingDist S.O", "522099", "PO", "Delivery", "", "ANDHRA PRADESH", "", ""],
        ]
        with open(cls.sample_csv, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(rows)

        cls.load_summary = loc_mod.load_locations_from_csv(cls.sample_csv)

        cls.superadmin = auth_mod.login("superadmin", "SeedTest@123")
        assert cls.superadmin is not None

        cls.citizen = auth_mod.register("ap_citizen_1", "AP Citizen One", "CitizenPass@123")

        cls.ap_state = loc_mod.get_default_state()
        assert cls.ap_state is not None
        districts = {d["name"]: d["id"] for d in loc_mod.get_districts(cls.ap_state["id"])}
        cls.guntur_id = districts["Guntur"]
        cls.vizag_id = districts["Visakhapatnam"]
        cls.nandyal_id = districts["Nandyal"]

        guntur_areas = loc_mod.get_areas(cls.guntur_id)
        cls.arundelpet_id = next(a["id"] for a in guntur_areas if "Arundelpet" in a["name"])
        cls.brodipet_id = next(a["id"] for a in guntur_areas if "Brodipet" in a["name"])
        vizag_areas = loc_mod.get_areas(cls.vizag_id)
        cls.mvp_id = vizag_areas[0]["id"]

        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_dept_id = depts["Water"]
        cls.elec_dept_id = depts["Electricity"]

        cls.admin_guntur = auth_mod.create_admin_account(
            cls.superadmin,
            "adm_guntur",
            "Guntur District Admin",
            "AdminPass@123",
            district_ids=[cls.guntur_id],
        )
        cls.admin_vizag = auth_mod.create_admin_account(
            cls.superadmin,
            "adm_vizag",
            "Vizag District Admin",
            "AdminPass@123",
            district_ids=[cls.vizag_id],
        )
        cls.admin_guntur_water_only = auth_mod.create_admin_account(
            cls.superadmin,
            "adm_guntur_w",
            "Guntur Water Admin",
            "AdminPass@123",
            district_ids=[cls.guntur_id],
            department_ids=[cls.water_dept_id],
        )

        cls.emp_guntur_w1 = auth_mod.create_user_account(
            cls.superadmin,
            "emp_gnt_w1",
            "Guntur Water Emp 1",
            "EmpPass@123",
            "employee",
            department_id=cls.water_dept_id,
            district_id=cls.guntur_id,
        )
        cls.emp_guntur_w2 = auth_mod.create_user_account(
            cls.superadmin,
            "emp_gnt_w2",
            "Guntur Water Emp 2",
            "EmpPass@123",
            "employee",
            department_id=cls.water_dept_id,
            district_id=cls.guntur_id,
        )
        cls.emp_guntur_e1 = auth_mod.create_user_account(
            cls.superadmin,
            "emp_gnt_e1",
            "Guntur Elec Emp 1",
            "EmpPass@123",
            "employee",
            department_id=cls.elec_dept_id,
            district_id=cls.guntur_id,
        )
        cls.emp_vizag_w1 = auth_mod.create_user_account(
            cls.superadmin,
            "emp_vzg_w1",
            "Vizag Water Emp 1",
            "EmpPass@123",
            "employee",
            department_id=cls.water_dept_id,
            district_id=cls.vizag_id,
        )

    def _submit_and_fetch(self, *args, **kwargs) -> dict:
        inc_id = comp_mod.submit_complaint(*args, **kwargs)
        rows = comp_mod.get_complaints(self.superadmin)
        match = next(r for r in rows if r["incident_id"] == inc_id)
        return comp_mod.get_complaint_detail(self.superadmin, match["id"])

    def test_01_pincode_lookup_cascading_and_submission_centroid_fallback(self):
        # 522002 covers both Arundelpet S.O and Brodipet B.O in Guntur
        pin_info = loc_mod.lookup_pincode("522002")
        self.assertIsNotNone(pin_info)
        self.assertEqual(pin_info["district_name"], "Guntur")
        self.assertEqual(len(pin_info["areas"]), 2)

        # Invalid & non-existent pincodes
        self.assertIsNone(loc_mod.lookup_pincode("5220"))
        with self.assertRaises(ValueError):
            comp_mod.submit_complaint(
                self.citizen,
                self.water_dept_id,
                "Water main leak near junction for 3 days.",
                pincode="599999",
            )
        with self.assertRaises(ValueError):
            comp_mod.submit_complaint(
                self.citizen,
                self.water_dept_id,
                "Water main leak near junction for 3 days.",
                pincode="12345",
            )

        # Valid pincode + area confirmation (no manual map pin -> centroid fallback!)
        c_gnt = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "No drinking water supply in Arundelpet since yesterday.",
            location="Near 2nd Line Temple",
            pincode="522002",
            area_id=self.arundelpet_id,
        )
        self.assertEqual(c_gnt["status"], "Awaiting Assignment")
        self.assertIsNone(c_gnt["assigned_to"])
        self.assertEqual(c_gnt["state_id"], self.ap_state["id"])
        self.assertEqual(c_gnt["district_id"], self.guntur_id)
        self.assertEqual(c_gnt["area_id"], self.arundelpet_id)
        self.assertEqual(c_gnt["pincode"], "522002")
        self.assertAlmostEqual(c_gnt["latitude"], 16.3067, places=3)
        self.assertAlmostEqual(c_gnt["longitude"], 80.4365, places=3)

        # Cascading selection (State -> District -> Area) auto-resolves pincode
        c_vzg = self._submit_and_fetch(
            self.citizen,
            self.elec_dept_id,
            "Streetlight pole sparking continuously in MVP Colony.",
            location="Sector 4 Road",
            state_id=self.ap_state["id"],
            district_id=self.vizag_id,
            area_id=self.mvp_id,
        )
        self.assertEqual(c_vzg["pincode"], "530001")
        self.assertEqual(c_vzg["district_id"], self.vizag_id)
        self.assertEqual(c_vzg["status"], "Awaiting Assignment")

    def test_02_admin_jurisdiction_isolation_district_and_department(self):
        c_gnt_elec = self._submit_and_fetch(
            self.citizen,
            self.elec_dept_id,
            "Power fluctuation damaging appliances in Brodipet.",
            pincode="522002",
            area_id=self.brodipet_id,
        )
        c_vzg_water = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "Pipeline burst near MVP Colony park.",
            state_id=self.ap_state["id"],
            district_id=self.vizag_id,
            area_id=self.mvp_id,
        )

        # Guntur Admin sees Guntur complaints, never Vizag complaints
        gnt_list = comp_mod.get_complaints(self.admin_guntur)
        self.assertTrue(all(c["district_id"] == self.guntur_id for c in gnt_list))
        self.assertNotIn(c_vzg_water["id"], [c["id"] for c in gnt_list])

        # Guntur Admin blocked from viewing/editing/assigning Vizag complaint
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_detail(self.admin_guntur, c_vzg_water["id"])
        with self.assertRaises(PermissionError):
            comp_mod.assign_task(self.admin_guntur, c_vzg_water["id"], self.emp_vizag_w1["id"])
        with self.assertRaises(PermissionError):
            comp_mod.update_task_status(
                self.admin_guntur,
                c_vzg_water["id"],
                new_status="In Progress",
                override_reason="Outside jurisdiction test",
            )

        # Department-scoped Guntur Water Admin does NOT see Guntur Electricity complaints
        gnt_w_list = comp_mod.get_complaints(self.admin_guntur_water_only)
        self.assertTrue(all(c["department_id"] == self.water_dept_id for c in gnt_w_list))
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_detail(self.admin_guntur_water_only, c_gnt_elec["id"])

        # Super Admin sees both districts
        sa_ids = {c["id"] for c in comp_mod.get_complaints(self.superadmin)}
        self.assertIn(c_gnt_elec["id"], sa_ids)
        self.assertIn(c_vzg_water["id"], sa_ids)

    def test_03_assign_task_dept_jurisdiction_matching_and_suggestion(self):
        c1 = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "Valve broken on Brodipet 4th lane.",
            pincode="522002",
            area_id=self.brodipet_id,
        )
        c2 = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "Contaminated water reported in Arundelpet.",
            pincode="522002",
            area_id=self.arundelpet_id,
        )

        # Mismatched district employee rejected
        with self.assertRaises(ValueError):
            comp_mod.assign_task(self.admin_guntur, c1["id"], self.emp_vizag_w1["id"])
        # Mismatched department employee rejected
        with self.assertRaises(ValueError):
            comp_mod.assign_task(self.admin_guntur, c1["id"], self.emp_guntur_e1["id"])

        due_future = (datetime.now(timezone.utc) + timedelta(hours=18)).strftime("%Y-%m-%d %H:%M:%S")
        assigned = comp_mod.assign_task(
            self.admin_guntur,
            c1["id"],
            self.emp_guntur_w1["id"],
            due_at=due_future,
            instructions="Inspect gate valve and submit pressure photo.",
        )
        self.assertEqual(assigned["status"], "Assigned")
        self.assertEqual(assigned["assigned_to"], self.emp_guntur_w1["id"])
        self.assertEqual(assigned["assigned_by"], self.admin_guntur["id"])
        self.assertIn("gate valve", assigned["assignment_instructions"])
        self.assertEqual(assigned["due_at"], due_future)

        # Suggested employee for c2 should prefer least-loaded active employee (emp_guntur_w2)
        suggested = comp_mod.get_suggested_employee(self.admin_guntur, c2["id"])
        self.assertIsNotNone(suggested)
        self.assertEqual(suggested["id"], self.emp_guntur_w2["id"])

    def test_04_employee_restrictions_accept_after_photos_and_resolve(self):
        task = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "Pipe leakage flooding road in Arundelpet for 3 days.",
            pincode="522002",
            area_id=self.arundelpet_id,
        )
        comp_mod.assign_task(
            self.admin_guntur,
            task["id"],
            self.emp_guntur_w1["id"],
            instructions="Fix leak and upload repaired photo.",
        )

        # Employee blocked from assigning/reassigning or editing dept/priority
        with self.assertRaises(PermissionError):
            comp_mod.assign_task(self.emp_guntur_w1, task["id"], self.emp_guntur_w2["id"])
        with self.assertRaises(PermissionError):
            comp_mod.update_complaint(self.emp_guntur_w1, task["id"], department_id=self.elec_dept_id)
        with self.assertRaises(PermissionError):
            comp_mod.update_complaint(self.emp_guntur_w1, task["id"], priority="Low")

        # Another employee (emp_guntur_w2) cannot view or update task assigned to emp_guntur_w1
        w2_visible_ids = {c["id"] for c in comp_mod.get_complaints(self.emp_guntur_w2)}
        self.assertNotIn(task["id"], w2_visible_ids)
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_detail(self.emp_guntur_w2, task["id"])
        with self.assertRaises(PermissionError):
            comp_mod.accept_task(self.emp_guntur_w2, task["id"])

        # Assigned employee accepts task
        accepted = comp_mod.accept_task(self.emp_guntur_w1, task["id"])
        self.assertEqual(accepted["status"], "In Progress")
        self.assertIsNotNone(accepted["accepted_at"])

        # Add 'after' resolution photo
        saved_after = img_mod.add_after_images(
            self.emp_guntur_w1,
            task["id"],
            [("repaired_pipe.jpg", _make_jpeg_bytes())],
        )
        self.assertEqual(len(saved_after), 1)
        self.assertEqual(saved_after[0]["photo_stage"], "after")

        # Resolve rejects short closing note < 10 chars
        with self.assertRaises(ValueError):
            comp_mod.update_task_status(
                self.emp_guntur_w1,
                task["id"],
                new_status="Resolved",
                remark="Fixed",
            )

        resolved = comp_mod.update_task_status(
            self.emp_guntur_w1,
            task["id"],
            new_status="Resolved",
            remark="Replaced damaged coupling and restored normal flow.",
        )
        self.assertEqual(resolved["status"], "Resolved")

    def test_05_unassigned_beyond_threshold_escalates_to_super_admin(self):
        stale = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "Unattended water complaint in Arundelpet.",
            pincode="522002",
            area_id=self.arundelpet_id,
        )
        now_dt = datetime.now(timezone.utc)
        old_created = (now_dt - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
        with db_mod.get_connection() as conn:
            conn.execute(
                "UPDATE complaints SET created_at = ?, updated_at = ? WHERE id = ?",
                (old_created, old_created, stale["id"]),
            )
            conn.commit()

        escalated_1 = ov_mod.run_unassigned_escalation_check(now=now_dt, threshold_hours=4)
        self.assertTrue(any(item["complaint_id"] == stale["id"] for item in escalated_1))

        refreshed = comp_mod.get_complaint_detail(self.superadmin, stale["id"])
        self.assertEqual(refreshed["unassigned_escalated"], 1)

        notifs = ov_mod.get_super_admin_notifications(self.superadmin, unread_only=True)
        matching = [n for n in notifs if n["complaint_id"] == stale["id"]]
        self.assertEqual(len(matching), 1)

        # Idempotent re-run does not duplicate notification for this ticket
        escalated_2 = ov_mod.run_unassigned_escalation_check(now=now_dt, threshold_hours=4)
        self.assertFalse(any(item["complaint_id"] == stale["id"] for item in escalated_2))

        ov_mod.mark_notification_read(self.superadmin, matching[0]["id"])
        after_read = ov_mod.get_super_admin_notifications(self.superadmin, unread_only=True)
        self.assertFalse(any(n["id"] == matching[0]["id"] for n in after_read))

    def test_06_deactivate_admin_returns_open_complaints_and_preserves_history(self):
        n_area_id = loc_mod.get_areas(self.nandyal_id)[0]["id"]
        adm_nandyal = auth_mod.create_admin_account(
            self.superadmin,
            "adm_nandyal",
            "Nandyal Admin",
            "AdminPass@123",
            district_ids=[self.nandyal_id],
        )
        emp_nandyal = auth_mod.create_user_account(
            self.superadmin,
            "emp_nandyal",
            "Nandyal Emp",
            "EmpPass@123",
            "employee",
            department_id=self.water_dept_id,
            district_id=self.nandyal_id,
        )
        c_n = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "Nandyal main tank valve leak urgent.",
            state_id=self.ap_state["id"],
            district_id=self.nandyal_id,
            area_id=n_area_id,
        )
        comp_mod.assign_task(adm_nandyal, c_n["id"], emp_nandyal["id"], instructions="Check valve")
        prior_history = comp_mod.get_complaint_history(self.superadmin, c_n["id"])
        prior_count = len(prior_history)

        auth_mod.set_user_active(self.superadmin, adm_nandyal["id"], False)

        # Deactivated admin cannot log in
        self.assertIsNone(auth_mod.login("adm_nandyal", "AdminPass@123"))

        after_c = comp_mod.get_complaint_detail(self.superadmin, c_n["id"])
        self.assertEqual(after_c["status"], "Awaiting Assignment")
        self.assertIsNone(after_c["assigned_to"])

        after_history = comp_mod.get_complaint_history(self.superadmin, c_n["id"])
        self.assertGreater(len(after_history), prior_count)
        self.assertTrue(
            any("adm_nandyal" in (h["remark"] or "") and h["new_value"] == "Awaiting Assignment" for h in after_history)
        )

    def test_07_admin_status_override_mandatory_reason_and_audit(self):
        c_ovr = self._submit_and_fetch(
            self.citizen,
            self.water_dept_id,
            "Street pipe burst in Brodipet urgent repair needed.",
            pincode="522002",
            area_id=self.brodipet_id,
        )
        with self.assertRaises(ValueError):
            comp_mod.update_task_status(
                self.admin_guntur,
                c_ovr["id"],
                new_status="In Progress",
                override_reason="",
            )
        with self.assertRaises(ValueError):
            comp_mod.update_task_status(
                self.admin_guntur,
                c_ovr["id"],
                new_status="In Progress",
                override_reason="ok",
            )

        updated = comp_mod.update_task_status(
            self.admin_guntur,
            c_ovr["id"],
            new_status="In Progress",
            override_reason="Emergency field team dispatched directly due to flooding.",
        )
        self.assertEqual(updated["status"], "In Progress")

        logs = ov_mod.get_audit_logs_filtered(
            self.superadmin,
            admin_user_id=self.admin_guntur["id"],
            district_id=self.guntur_id,
        )
        self.assertTrue(
            any(
                l["incident_id"] == c_ovr["incident_id"]
                and l["source"] == "admin_override"
                and "flooding" in (l["remark"] or "")
                for l in logs
            )
        )

    def test_08_loader_reporting_mandal_handling_idempotency_and_public_suppression(self):
        self.assertEqual(self.load_summary["loaded_mandals"], 0)
        self.assertFalse(self.load_summary["mandal_level_present"])
        self.assertGreaterEqual(self.load_summary["skipped_rows"], 2)
        self.assertGreaterEqual(len(self.load_summary["skipped_samples"]), 2)
        self.assertIn("lacks mandal", self.load_summary["notes"].lower())

        second_run = loc_mod.load_locations_from_csv(self.sample_csv)
        self.assertEqual(second_run["loaded_states"], 0)
        self.assertEqual(second_run["loaded_districts"], 0)
        self.assertEqual(second_run["loaded_areas"], 0)
        self.assertEqual(second_run["loaded_pincodes"], 0)

        mandal_csv = Path(_TMP_DIR) / "ap_with_mandal.csv"
        with open(mandal_csv, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows([
                ["statename", "district", "mandal", "officename", "pincode", "latitude", "longitude"],
                ["Andhra Pradesh", "Guntur", "Tenali Mandal", "Angalakuduru B.O", "522211", "16.2500", "80.6500"],
            ])
        mandal_run = loc_mod.load_locations_from_csv(mandal_csv)
        self.assertTrue(mandal_run["mandal_level_present"])
        self.assertEqual(mandal_run["loaded_mandals"], 1)
        mandals = loc_mod.get_mandals(self.guntur_id)
        self.assertTrue(any(m["name"] == "Tenali Mandal" for m in mandals))

        # Ensure Guntur >= 5 complaints, Vizag < 5 and Nandyal < 5 -> Public Transparency shows Guntur & suppresses < 5
        pub = geo_mod.get_public_transparency_by_district()
        shown_names = {d["district"] for d in pub["visible_districts"]}
        self.assertIn("Guntur", shown_names)
        self.assertNotIn("Visakhapatnam", shown_names)
        self.assertNotIn("Nandyal", shown_names)
        self.assertGreaterEqual(pub["suppressed_groups_count"], 1)
        for d in pub["visible_districts"]:
            self.assertGreaterEqual(d["total"], 5)
            self.assertNotIn("citizen_id", d)
            self.assertNotIn("username", d)


if __name__ == "__main__":
    unittest.main()
