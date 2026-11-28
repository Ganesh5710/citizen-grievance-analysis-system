"""
Comprehensive test suite covering Phases 1-5:
  - Department selection & migration
  - Secure image uploads (Pillow validation, EXIF/GPS strip, downscale, limits, RBAC)
  - Assignment (auto-assign, department scoping, claim, admin reassign)
  - SLA policies, due_at calculation/recalculation, Level 1/2 escalations
  - Citizen feedback, reopen cycle, 7-day window & auto-close, closing remark >= 10 chars
  - Performance analytics (per-department, per-employee, employee self-summary)
  - Regression tests (RBAC, user lifecycle, soft delete, audit log, persistence)

Run with:
    python -m unittest discover -s tests -v
    python -m pytest tests/ -v
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import importlib
import os
import shutil
import sqlite3
import tempfile
import unittest

from PIL import Image

_TMP_DIR = tempfile.mkdtemp(prefix="cgs_full_test_")
_TMP_DB = os.path.join(_TMP_DIR, "test_complaints.db")
_TMP_UPLOADS = os.path.join(_TMP_DIR, "uploads")
os.environ["CGS_DB_PATH"] = _TMP_DB
os.environ["CGS_UPLOADS_DIR"] = _TMP_UPLOADS
os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"

import core.auth as auth_mod
import core.classifier as classifier_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.images as img_mod
import core.locations as loc_mod
import core.performance as perf_mod
import core.sla as sla_mod
import core.wards as ward_mod


def _make_image_bytes(fmt: str = "JPEG", size: tuple[int, int] = (200, 150), with_exif: bool = False) -> bytes:
    img = Image.new("RGB", size, color=(40, 120, 200))
    buf = BytesIO()
    if with_exif and fmt == "JPEG":
        exif = img.getexif()
        exif[0x010F] = "TestCamera"         # Make
        exif[0x013B] = "Photographer"       # Artist
        exif[0x8825] = {1: "N", 2: (12.0,)} # GPSInfo IFD pointer / data
        img.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


def tearDownModule():
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


class TestCitizenGrievanceFullSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["CGS_DB_PATH"] = _TMP_DB
        os.environ["CGS_UPLOADS_DIR"] = _TMP_UPLOADS
        os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"
        db_mod.init_db()
        db_mod.seed_demo_accounts()
        with db_mod.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active, allow_self_claim) VALUES (1, 1, 'Guntur', 1, 0)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (1, 1, 'Arundelpet S.O', 16.3067, 80.4365, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.commit()
        sa_first = auth_mod.login("superadmin", "SeedTest@123")
        assert sa_first is not None and sa_first["must_change_password"] is True
        cls.superadmin = auth_mod.change_own_password(sa_first, "SeedTest@123", "SuperSecret@123")

        adm_first = auth_mod.login("admin", "SeedTest@123")
        assert adm_first is not None and adm_first["must_change_password"] is True
        cls.admin_demo = auth_mod.change_own_password(adm_first, "SeedTest@123", "AdminSecret@123")

        emp_first = auth_mod.login("employee", "SeedTest@123")
        assert emp_first is not None and emp_first["must_change_password"] is True
        cls.emp_water_1 = auth_mod.change_own_password(emp_first, "SeedTest@123", "EmpSecret@123")
        cls.emp_water_1 = auth_mod.set_user_jurisdiction(cls.superadmin, cls.emp_water_1["id"], 1)

        cls.depts = dept_mod.get_active_departments()
        cls.water = next(d for d in cls.depts if d["name"] == "Water")
        cls.roads = next(d for d in cls.depts if d["name"] == "Roads")
        cls.elec = next(d for d in cls.depts if d["name"] == "Electricity")
        cls.traffic = next(d for d in cls.depts if d["name"] == "Traffic")
        cls.sanitation = next(d for d in cls.depts if d["name"] == "Sanitation")

        cls.emp_roads = auth_mod.create_user_account(
            cls.superadmin, "emp_roads", "Roads Emp", "Roads@123", "employee",
            department_id=cls.roads["id"], district_id=1,
        )
        cls.admin_user = auth_mod.create_admin_account(
            cls.superadmin, "adm_main", "Main Admin", "Admin@123", district_ids=[1]
        )
        cls.citizen_a = auth_mod.register("citizen_a", "Citizen A", "Password@1")
        cls.citizen_b = auth_mod.register("citizen_b", "Citizen B", "Password@2")

    # ==================================================================
    # PHASE 1: DEPARTMENTS (Tests 1-5)
    # ==================================================================

    def test_01_citizen_submits_to_any_active_dept_and_saved_matches(self):
        # Text mentions "water" but citizen selects Traffic — must stay Traffic
        inc = comp_mod.submit_complaint(
            self.citizen_a,
            self.traffic["id"],
            "Waterlogged traffic junction signal is broken",
            "Main Square",
            pincode="522002",
            area_id=1,
        )
        saved = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc)
        self.assertEqual(saved["department_id"], self.traffic["id"])
        self.assertEqual(saved["department"], "Traffic")

    def test_02_nonexistent_or_deactivated_dept_raises_value_error(self):
        with self.assertRaises(ValueError):
            comp_mod.submit_complaint(
                self.citizen_a, 999999, "Valid complaint text here", "Block A", pincode="522002", area_id=1
            )

        custom = dept_mod.create_department(self.superadmin, "Parks", "Parks and open grounds")
        inc_before = comp_mod.submit_complaint(
            self.citizen_a, custom["id"], "Broken bench in city park", "Central Park", pincode="522002", area_id=1
        )
        dept_mod.set_department_active(self.superadmin, custom["id"], False)

        # Hidden from active citizen list
        self.assertNotIn(custom["id"], [d["id"] for d in dept_mod.get_active_departments()])

        # Rejected for new submissions
        with self.assertRaises(ValueError):
            comp_mod.submit_complaint(
                self.citizen_a, custom["id"], "Park gate broken near east", "East Gate", pincode="522002", area_id=1
            )

        # Old complaint remains intact
        staff_list = comp_mod.get_complaints(self.admin_user)
        old_c = next(c for c in staff_list if c["incident_id"] == inc_before)
        self.assertEqual(old_c["department"], "Parks")

    def test_03_only_users_can_submit_complaints(self):
        for actor in (self.emp_water_1, self.admin_user, self.superadmin, None):
            with self.assertRaises(PermissionError):
                comp_mod.submit_complaint(
                    actor, self.water["id"], "Staff cannot submit complaint", "City Hall", pincode="522002", area_id=1
                )

    def test_04_priority_auto_detected_via_classify(self):
        inc = comp_mod.submit_complaint(
            self.citizen_a,
            self.elec["id"],
            "Transformer fire near school gate",
            "School Gate",
            pincode="522002",
            area_id=1,
        )
        saved = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc)
        self.assertEqual(saved["priority"], "High")
        self.assertEqual(classifier_mod.classify("fire hazard")[1], "High")

    # ==================================================================
    # PHASE 2: IMAGES (Tests 6-9)
    # ==================================================================

    def test_05_image_upload_validation_limits_and_formats(self):
        jpg_bytes = _make_image_bytes("JPEG")
        png_bytes = _make_image_bytes("PNG")
        webp_bytes = _make_image_bytes("WEBP")

        inc = comp_mod.submit_complaint(
            self.citizen_a,
            self.water["id"],
            "Water pipe burst with attached photo evidence",
            "Market Street",
            pincode="522002",
            area_id=1,
            files=[("leak.jpg", jpg_bytes), ("map.png", png_bytes)],
        )
        comp = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc)
        self.assertEqual(img_mod.count_images(comp["id"]), 2)

        # Add 3rd valid WebP image later
        img_mod.add_images(self.citizen_a, comp["id"], [("extra.webp", webp_bytes)])
        self.assertEqual(img_mod.count_images(comp["id"]), 3)

        # 4th image must be rejected
        with self.assertRaises(ValueError):
            img_mod.add_images(self.citizen_a, comp["id"], [("fourth.jpg", jpg_bytes)])

        # Renamed non-image & SVG rejected
        inc2 = comp_mod.submit_complaint(
            self.citizen_a,
            self.water["id"],
            "Another water leak for invalid upload tests",
            "Lane 2",
            pincode="522002",
            area_id=1,
        )
        comp2 = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc2)

        with self.assertRaises(ValueError):
            img_mod.add_images(self.citizen_a, comp2["id"], [("fake.jpg", b"NOT_AN_IMAGE_PAYLOAD")])
        with self.assertRaises(ValueError):
            img_mod.add_images(
                self.citizen_a,
                comp2["id"],
                [("xss.png", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")],
            )
        # Over 5 MB rejected
        oversized = b"A" * (5 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            img_mod.add_images(self.citizen_a, comp2["id"], [("huge.jpg", oversized)])

    def test_06_image_access_control_and_removal(self):
        jpg_bytes = _make_image_bytes("JPEG")
        inc = comp_mod.submit_complaint(
            self.citizen_a,
            self.water["id"],
            "Water supply contamination photo proof",
            "Ward 5",
            pincode="522002",
            area_id=1,
            files=[("sample.jpg", jpg_bytes)],
        )
        comp = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc)
        comp_mod.assign_task(self.admin_user, comp["id"], self.emp_water_1["id"])

        # Owner, assigned employee, admin, superadmin can view
        self.assertEqual(len(img_mod.get_images(self.citizen_a, comp["id"])), 1)
        self.assertEqual(len(img_mod.get_images(self.emp_water_1, comp["id"])), 1)
        self.assertEqual(len(img_mod.get_images(self.admin_user, comp["id"])), 1)
        self.assertEqual(len(img_mod.get_images(self.superadmin, comp["id"])), 1)

        # Other citizen & other-department employee get PermissionError
        with self.assertRaises(PermissionError):
            img_mod.get_images(self.citizen_b, comp["id"])
        with self.assertRaises(PermissionError):
            img_mod.get_images(self.emp_roads, comp["id"])
        with self.assertRaises(PermissionError):
            img_mod.add_images(self.citizen_b, comp["id"], [("intrude.jpg", jpg_bytes)])

        # Staff cannot add_images
        for staff in (self.emp_water_1, self.admin_user, self.superadmin):
            with self.assertRaises(PermissionError):
                img_mod.add_images(staff, comp["id"], [("staff.jpg", jpg_bytes)])

        # Employee & citizen cannot remove_image
        img_id = img_mod.get_images(self.admin_user, comp["id"])[0]["id"]
        for actor in (self.citizen_a, self.emp_water_1):
            with self.assertRaises(PermissionError):
                img_mod.remove_image(actor, img_id, "forbidden")

        # Admin removes image -> soft deleted + audit logged + disappears from get_images
        img_mod.remove_image(self.admin_user, img_id, "Blurry duplicate")
        self.assertEqual(img_mod.count_images(comp["id"]), 0)
        self.assertEqual(img_mod.get_images(self.admin_user, comp["id"]), [])

        hist = comp_mod.get_complaint_history(self.admin_user, comp["id"])
        self.assertTrue(any(h["field_name"] == "image_removed" for h in hist))

    def test_07_exif_gps_stripped_downscaled_and_persists(self):
        # 2400x1800 JPEG with EXIF/GPS tags
        raw_with_exif = _make_image_bytes("JPEG", size=(2400, 1800), with_exif=True)
        with Image.open(BytesIO(raw_with_exif)) as orig_probe:
            self.assertTrue(len(orig_probe.getexif()) > 0)

        inc = comp_mod.submit_complaint(
            self.citizen_a,
            self.roads["id"],
            "Large pothole photo with GPS metadata",
            "Highway Exit 3",
            pincode="522002",
            area_id=1,
            files=[("../../evil/path name.jpg", raw_with_exif)],
        )
        comp = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc)
        stored_list = img_mod.get_images(self.citizen_a, comp["id"])
        self.assertEqual(len(stored_list), 1)
        stored = stored_list[0]

        # Path traversal characters stripped from original filename & stored uses UUID
        self.assertNotIn("..", stored["stored_filename"])
        self.assertNotIn("evil", stored["stored_filename"])

        with Image.open(BytesIO(stored["data"])) as cleaned_img:
            self.assertEqual(len(cleaned_img.getexif()), 0)
            self.assertLessEqual(max(cleaned_img.size), 1600)

        # Reload modules and confirm persistence
        importlib.reload(img_mod)
        reloaded = img_mod.get_images(self.citizen_a, comp["id"])
        self.assertEqual(len(reloaded), 1)

    # ==================================================================
    # PHASE 3: ASSIGNMENT, SLA, ESCALATION, FEEDBACK (Tests 10-14)
    # ==================================================================

    def test_08_no_district_rejected_no_auto_assign_and_suggested_employee(self):
        dept_lb = dept_mod.create_department(self.superadmin, "Lighting", "Street lights maintenance")
        e1 = auth_mod.create_user_account(
            self.superadmin, "emp_lb1", "LB Emp 1", "Pass@1234", "employee",
            department_id=dept_lb["id"], district_id=1,
        )
        e2 = auth_mod.create_user_account(
            self.superadmin, "emp_lb2", "LB Emp 2", "Pass@1234", "employee",
            department_id=dept_lb["id"], district_id=1,
        )

        # Complaint without a district/pincode MUST be rejected
        with self.assertRaises(ValueError):
            comp_mod.submit_complaint(self.citizen_a, dept_lb["id"], "Streetlight out at corner 1", "Corner 1")

        # Valid submission enters Awaiting Assignment with NO auto-assignment
        i1 = comp_mod.submit_complaint(
            self.citizen_a, dept_lb["id"], "Streetlight out at corner 1", "Corner 1", pincode="522002", area_id=1
        )
        c1 = next(c for c in comp_mod.get_complaints(self.admin_user) if c["incident_id"] == i1)
        self.assertEqual(c1["status"], "Awaiting Assignment")
        self.assertIsNone(c1["assigned_to"])

        # Admin sees least-loaded suggested employee (0 vs 0 -> e1) and assigns explicitly
        sug1 = comp_mod.get_suggested_employee(self.admin_user, c1["id"])
        self.assertIsNotNone(sug1)
        self.assertEqual(sug1["id"], e1["id"])
        comp_mod.assign_task(self.admin_user, c1["id"], e1["id"])

        # 2nd complaint also starts unassigned, suggested employee is now e2 (0 vs 1)
        i2 = comp_mod.submit_complaint(
            self.citizen_a, dept_lb["id"], "Streetlight out at corner 2", "Corner 2", pincode="522002", area_id=1
        )
        c2 = next(c for c in comp_mod.get_complaints(self.admin_user) if c["incident_id"] == i2)
        self.assertEqual(c2["status"], "Awaiting Assignment")
        self.assertIsNone(c2["assigned_to"])
        sug2 = comp_mod.get_suggested_employee(self.admin_user, c2["id"])
        self.assertEqual(sug2["id"], e2["id"])

        # Department with no active employees -> no suggestion, stays unassigned
        dept_empty = dept_mod.create_department(self.superadmin, "DrainageEmpty", "Storm drains")
        i3 = comp_mod.submit_complaint(
            self.citizen_a, dept_empty["id"], "Storm drain clogged near bridge", "Bridge", pincode="522002", area_id=1
        )
        c3 = next(c for c in comp_mod.get_complaints(self.admin_user) if c["incident_id"] == i3)
        self.assertIsNone(c3["assigned_to"])
        self.assertIsNone(comp_mod.get_suggested_employee(self.admin_user, c3["id"]))

    def test_09_employee_scope_claim_restricted_and_admin_reassign(self):
        dept_c = dept_mod.create_department(self.superadmin, "Housing", "Public housing maintenance")
        inc = comp_mod.submit_complaint(
            self.citizen_a, dept_c["id"], "Roof leakage in municipal quarters", "Block D",
            pincode="522002", area_id=1,
        )
        comp = next(c for c in comp_mod.get_complaints(self.admin_user) if c["incident_id"] == inc)
        self.assertIsNone(comp["assigned_to"])

        h_emp1 = auth_mod.create_user_account(
            self.superadmin, "emp_house1", "House Emp 1", "Pass@1234", "employee",
            department_id=dept_c["id"], district_id=1,
        )
        h_emp2 = auth_mod.create_user_account(
            self.superadmin, "emp_house2", "House Emp 2", "Pass@1234", "employee",
            department_id=dept_c["id"], district_id=1,
        )

        # Default allow_self_claim=False -> even matching employee CANNOT claim!
        with self.assertRaises(PermissionError):
            comp_mod.claim_complaint(h_emp1, comp["id"])

        # Enable self-claim for district 1
        loc_mod.set_district_self_claim(self.admin_user, 1, True)

        # Roads employee still cannot view or claim Housing complaint
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_detail(self.emp_roads, comp["id"])
        with self.assertRaises(PermissionError):
            comp_mod.claim_complaint(self.emp_roads, comp["id"])

        # Housing employee cannot edit unassigned complaint before claiming
        with self.assertRaises(PermissionError):
            comp_mod.update_complaint(h_emp1, comp["id"], new_status="In Progress")

        # Claim works once Admin enabled self-claim for that district
        claimed = comp_mod.claim_complaint(h_emp1, comp["id"])
        self.assertEqual(claimed["assigned_to"], h_emp1["id"])
        self.assertEqual(claimed["status"], "Assigned")

        # Disable self-claim back to default OFF
        loc_mod.set_district_self_claim(self.admin_user, 1, False)

        # h_emp2 cannot edit complaint assigned to h_emp1
        with self.assertRaises(PermissionError):
            comp_mod.update_complaint(h_emp2, comp["id"], new_status="In Progress")

        # Employee & user cannot assign_complaint; admin can reassign
        for actor in (h_emp1, self.citizen_a):
            with self.assertRaises(PermissionError):
                comp_mod.assign_complaint(actor, comp["id"], h_emp2["id"])

        reassigned = comp_mod.assign_complaint(self.admin_user, comp["id"], h_emp2["id"], "Reassigning")
        self.assertEqual(reassigned["assigned_to"], h_emp2["id"])

        # Changing department clears assignment & returns to Awaiting Assignment (never auto-assigns)
        comp_mod.update_complaint(
            self.admin_user,
            comp["id"],
            department_id=self.sanitation["id"],
            remark="Moved to Sanitation",
        )
        after_dept = comp_mod.get_complaint_detail(self.admin_user, comp["id"])
        self.assertEqual(after_dept["department_id"], self.sanitation["id"])
        self.assertEqual(after_dept["status"], "Awaiting Assignment")
        self.assertIsNone(after_dept["assigned_to"])

    def test_10_sla_due_at_recalc_and_escalations(self):
        # Customize Water / High SLA to 4 hours
        sla_mod.update_sla_policy(self.superadmin, self.water["id"], "High", 4)
        with self.assertRaises(PermissionError):
            sla_mod.update_sla_policy(self.admin_user, self.water["id"], "High", 10)

        inc = comp_mod.submit_complaint(
            self.citizen_a,
            self.water["id"],
            "Urgent main water pipe burst on avenue",
            "Avenue 9",
            pincode="522002",
            area_id=1,
        )
        comp = next(c for c in comp_mod.get_complaints(self.admin_user) if c["incident_id"] == inc)
        created_dt = sla_mod.parse_utc(comp["created_at"])
        due_dt = sla_mod.parse_utc(comp["due_at"])
        self.assertEqual((due_dt - created_dt).total_seconds(), 4 * 3600)

        # Changing priority to Low (120h) recalculates due_at
        comp_mod.update_complaint(self.admin_user, comp["id"], priority="Low", remark="Downgraded priority")
        comp_low = comp_mod.get_complaint_detail(self.admin_user, comp["id"])
        due_low_dt = sla_mod.parse_utc(comp_low["due_at"])
        self.assertEqual((due_low_dt - created_dt).total_seconds(), 120 * 3600)

        # Simulate 2 hours past due -> Level 1 escalation with actor = 'system'
        t_lvl1 = due_low_dt + timedelta(hours=2)
        res1 = sla_mod.run_escalation_check(now=t_lvl1)
        self.assertGreaterEqual(res1["escalated_to_1"], 1)
        c_lvl1 = comp_mod.get_complaint_detail(self.admin_user, comp["id"], now=t_lvl1)
        self.assertEqual(c_lvl1["escalation_level"], 1)
        self.assertTrue(c_lvl1["is_overdue"])

        # Simulate 26 hours past due -> Level 2 escalation with actor = 'system'
        t_lvl2 = due_low_dt + timedelta(hours=26)
        res2 = sla_mod.run_escalation_check(now=t_lvl2)
        self.assertGreaterEqual(res2["escalated_to_2"], 1)
        c_lvl2 = comp_mod.get_complaint_detail(self.admin_user, comp["id"], now=t_lvl2)
        self.assertEqual(c_lvl2["escalation_level"], 2)

        hist = comp_mod.get_complaint_history(self.admin_user, comp["id"])
        sys_rows = [h for h in hist if h["actor_username"] == "system" and h["field_name"] == "escalation_level"]
        self.assertGreaterEqual(len(sys_rows), 2)

    def test_11_feedback_reopen_closing_remark_and_7day_autoclose(self):
        dept_fb = dept_mod.create_department(self.superadmin, "FeedbackDept", "Feedback workflow testing")
        emp_fb = auth_mod.create_user_account(
            self.superadmin, "emp_fb", "FB Emp", "Pass@1234", "employee",
            department_id=dept_fb["id"], district_id=1,
        )
        inc = comp_mod.submit_complaint(
            self.citizen_a, dept_fb["id"], "Drainage cover broken near entrance", "Entrance 1",
            pincode="522002", area_id=1,
        )
        comp = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc)
        comp_mod.assign_task(self.admin_user, comp["id"], emp_fb["id"])

        # Resolving with remark < 10 chars fails
        with self.assertRaises(ValueError):
            comp_mod.update_complaint(emp_fb, comp["id"], new_status="Resolved", remark="Fixed")

        # Resolve with valid closing remark >= 10 chars
        comp_mod.update_complaint(
            emp_fb, comp["id"], new_status="Resolved", remark="Replaced concrete cover and sealed edge"
        )

        # Citizen cannot attach more photos once Resolved
        with self.assertRaises(ValueError):
            img_mod.add_images(self.citizen_a, comp["id"], [("late.jpg", _make_image_bytes("JPEG"))])

        # Other citizen cannot submit feedback
        with self.assertRaises(PermissionError):
            comp_mod.submit_feedback(self.citizen_b, comp["id"], accepted=True, rating=5)

        # Reopening requires a comment
        with self.assertRaises(ValueError):
            comp_mod.submit_feedback(self.citizen_a, comp["id"], accepted=False, rating=2, comment="")

        # Reopen works: status=Reopened, reopen_count=1, escalation_level=0, due_at reset from now, same employee kept
        reopened = comp_mod.submit_feedback(
            self.citizen_a, comp["id"], accepted=False, rating=2, comment="Cover is still loose"
        )
        self.assertEqual(reopened["status"], "Reopened")
        self.assertEqual(reopened["reopen_count"], 1)
        self.assertEqual(reopened["escalation_level"], 0)
        self.assertEqual(reopened["assigned_to"], emp_fb["id"])

        # Resolve again
        comp_mod.update_complaint(
            emp_fb, comp["id"], new_status="Resolved", remark="Bolted heavy iron frame securely"
        )

        # Feedback after 7 days fails
        resolved_comp = comp_mod.get_complaint_detail(self.citizen_a, comp["id"])
        resolved_dt = sla_mod.parse_utc(resolved_comp["resolved_at"])
        with self.assertRaises(ValueError):
            comp_mod.submit_feedback(
                self.citizen_a,
                comp["id"],
                accepted=True,
                rating=5,
                now=resolved_dt + timedelta(days=8),
            )

        # Confirm resolved within 7 days -> Closed, and duplicate feedback in same cycle fails
        closed = comp_mod.submit_feedback(
            self.citizen_a, comp["id"], accepted=True, rating=5, comment="Great work"
        )
        self.assertEqual(closed["status"], "Closed")
        with self.assertRaises(ValueError):
            comp_mod.submit_feedback(self.citizen_a, comp["id"], accepted=True, rating=4)

        # Auto-close after 7 days on another complaint without citizen response
        inc2 = comp_mod.submit_complaint(
            self.citizen_a, dept_fb["id"], "Loose curb stones near park entrance", "Park Gate",
            pincode="522002", area_id=1,
        )
        comp2 = next(c for c in comp_mod.get_complaints(self.citizen_a) if c["incident_id"] == inc2)
        comp_mod.assign_task(self.admin_user, comp2["id"], emp_fb["id"])
        comp_mod.update_complaint(
            emp_fb, comp2["id"], new_status="Resolved", remark="Reset curb stones with cement"
        )
        comp2_res = comp_mod.get_complaint_detail(self.admin_user, comp2["id"])
        res2_dt = sla_mod.parse_utc(comp2_res["resolved_at"])

        auto_summary = sla_mod.run_escalation_check(now=res2_dt + timedelta(days=7, minutes=5))
        self.assertGreaterEqual(auto_summary["auto_closed"], 1)
        comp2_after = comp_mod.get_complaint_detail(self.admin_user, comp2["id"])
        self.assertEqual(comp2_after["status"], "Closed")

    # ==================================================================
    # PHASE 4: PERFORMANCE ANALYTICS (Test 15)
    # ==================================================================

    def test_12_performance_metrics_calculation(self):
        # Employee/citizen cannot access full admin performance metrics
        with self.assertRaises(PermissionError):
            perf_mod.get_performance_metrics(self.emp_water_1)
        with self.assertRaises(PermissionError):
            perf_mod.get_performance_metrics(self.citizen_a)

        metrics = perf_mod.get_performance_metrics(self.admin_user)
        fb_dept = next(d for d in metrics["by_department"] if d["department"] == "FeedbackDept")
        self.assertEqual(fb_dept["handled"], 2)
        self.assertEqual(fb_dept["resolved"], 2)
        self.assertEqual(fb_dept["open"], 0)
        self.assertEqual(fb_dept["sla_met_pct"], 100.0)
        self.assertEqual(fb_dept["reopen_rate_pct"], 50.0)
        # FeedbackDept had ratings 2 and 5 -> avg 3.5
        self.assertEqual(fb_dept["avg_rating"], 3.5)

        emp_fb_m = next(e for e in metrics["by_employee"] if e["username"] == "emp_fb")
        self.assertEqual(emp_fb_m["handled"], 2)
        self.assertEqual(emp_fb_m["avg_rating"], 3.5)

    # ==================================================================
    # PHASE 5: REGRESSION & MIGRATION (Tests 16-17)
    # ==================================================================

    def test_13_user_management_security_and_regression(self):
        # Public signup strictly creates user
        u = auth_mod.register("reg_user1", "Reg User", "ValidPass@1")
        self.assertEqual(u["role"], "user")

        # Self & last super_admin protection
        with self.assertRaises(ValueError):
            auth_mod.set_user_active(self.superadmin, self.superadmin["id"], False)
        with self.assertRaises(ValueError):
            auth_mod.change_user_role(self.superadmin, self.superadmin["id"], "admin")

        # Employee creation requires department_id
        with self.assertRaises(ValueError):
            auth_mod.create_user_account(self.superadmin, "nodept_emp", "No Dept", "Pass@1234", "employee")

        # Deactivate citizen_b -> cannot login, old complaints preserved
        inc_b = comp_mod.submit_complaint(
            self.citizen_b, self.water["id"], "Water meter leaking outside house", "House 12",
            pincode="522002", area_id=1,
        )
        auth_mod.set_user_active(self.superadmin, self.citizen_b["id"], False)
        self.assertIsNone(auth_mod.login("citizen_b", "Password@2"))
        self.assertIn(inc_b, [c["incident_id"] for c in comp_mod.get_complaints(self.admin_user)])
        auth_mod.set_user_active(self.superadmin, self.citizen_b["id"], True)

        # Password reset forces change on next login
        temp_pw = auth_mod.reset_user_password(self.superadmin, u["id"])
        logged = auth_mod.login("reg_user1", temp_pw)
        self.assertIsNotNone(logged)
        self.assertTrue(logged["must_change_password"])
        changed = auth_mod.change_own_password(logged, temp_pw, "FinalPass@9")
        self.assertFalse(changed["must_change_password"])

        # Only super_admin can delete complaint
        target = next(c for c in comp_mod.get_complaints(self.admin_user) if c["incident_id"] == inc_b)
        with self.assertRaises(PermissionError):
            comp_mod.delete_complaint(self.admin_user, target["id"], "Forbidden")
        comp_mod.delete_complaint(self.superadmin, target["id"], "Test cleanup")
        self.assertNotIn(inc_b, [c["incident_id"] for c in comp_mod.get_complaints(self.superadmin)])

    def test_14_migration_preserves_users_complaints_and_audit_history(self):
        mig_db = os.path.join(_TMP_DIR, "mig_phase3_test.db")
        conn = sqlite3.connect(mig_db)
        conn.executescript("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','employee','admin','super_admin')),
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO users (id, username, full_name, password_hash, salt, role)
            VALUES
                (1, 'cit_old', 'Old Citizen', '00', '00', 'user'),
                (2, 'emp_old', 'Old Emp', '00', '00', 'employee');

            CREATE TABLE complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT UNIQUE NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id),
                text TEXT NOT NULL,
                location TEXT DEFAULT '',
                department TEXT NOT NULL,
                priority TEXT NOT NULL CHECK(priority IN ('High','Medium','Low')),
                status TEXT NOT NULL DEFAULT 'Pending'
                           CHECK(status IN ('Pending','Assigned','In Progress','Resolved')),
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO complaints (id, incident_id, user_id, text, location, department, priority, status)
            VALUES
                (1, 'CIV-MIG00001', 1, 'Water leak', 'Lane 1', 'Water', 'High', 'Pending'),
                (2, 'CIV-MIG00002', 1, 'Unknown dept', 'Lane 2', 'LegacyDept', 'Low', 'Resolved');

            CREATE TABLE complaint_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_id INTEGER NOT NULL REFERENCES complaints(id),
                employee_id INTEGER NOT NULL REFERENCES users(id),
                old_status TEXT,
                new_status TEXT,
                remark TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO complaint_updates (complaint_id, employee_id, old_status, new_status, remark)
            VALUES (1, 2, 'Pending', 'In Progress', 'Legacy audit note');
        """)
        conn.commit()
        conn.close()

        prev = os.environ.get("CGS_DB_PATH")
        try:
            os.environ["CGS_DB_PATH"] = mig_db
            db_mod.init_db()
            c = db_mod.get_connection()
            try:
                u1 = c.execute("SELECT is_active, department_id FROM users WHERE id = 1").fetchone()
                self.assertEqual(u1["is_active"], 1)
                self.assertIsNone(u1["department_id"])

                c1 = c.execute(
                    "SELECT d.name AS dept, c.due_at, c.escalation_level "
                    "FROM complaints c JOIN departments d ON d.id = c.department_id WHERE c.id = 1"
                ).fetchone()
                c2 = c.execute(
                    "SELECT d.name AS dept, c.resolved_at "
                    "FROM complaints c JOIN departments d ON d.id = c.department_id WHERE c.id = 2"
                ).fetchone()
                self.assertEqual(c1["dept"], "Water")
                self.assertIsNotNone(c1["due_at"])
                self.assertEqual(c1["escalation_level"], 0)
                self.assertEqual(c2["dept"], "General / Other")
                self.assertIsNotNone(c2["resolved_at"])

                cu = c.execute(
                    "SELECT actor_label, old_value, new_value, remark FROM complaint_updates WHERE complaint_id = 1"
                ).fetchone()
                self.assertEqual(cu["actor_label"], "emp_old")
                self.assertEqual(cu["old_value"], "Pending")
                self.assertEqual(cu["new_value"], "In Progress")
                self.assertEqual(cu["remark"], "Legacy audit note")
            finally:
                c.close()
        finally:
            if prev is not None:
                os.environ["CGS_DB_PATH"] = prev

    # ==================================================================
    # PHASE 0: HARDENING, LOCKOUT, SESSION TIMEOUT, WAL, WARDS, SYNTHETIC
    # ==================================================================

    def test_15_phase0_hardening_wards_and_synthetic_data(self):
        # WAL mode and busy_timeout enabled
        conn = db_mod.get_connection()
        try:
            jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
            bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(str(jm).lower(), "wal")
            self.assertGreaterEqual(int(bt), 5000)
        finally:
            conn.close()

        # Random one-time seed generation when no env password is set
        rand_db = os.path.join(_TMP_DIR, "rand_seed.db")
        prev_db = os.environ.get("CGS_DB_PATH")
        prev_seed = os.environ.pop("CGS_SEED_PASSWORD", None)
        try:
            os.environ["CGS_DB_PATH"] = rand_db
            db_mod.init_db()
            creds = db_mod.seed_demo_accounts()
            self.assertEqual(set(creds.keys()), {"superadmin", "admin", "employee"})
            for uname, pw in creds.items():
                self.assertGreaterEqual(len(pw), 12)
                self.assertNotEqual(pw, "Admin@123")
                logged = auth_mod.login(uname, pw)
                self.assertIsNotNone(logged)
                self.assertTrue(logged["must_change_password"])
        finally:
            if prev_seed is not None:
                os.environ["CGS_SEED_PASSWORD"] = prev_seed
            if prev_db is not None:
                os.environ["CGS_DB_PATH"] = prev_db

        # Rate limiting: 5 failures lock for 5 minutes
        lock_user = auth_mod.register("lock_victim", "Lock Victim", "RightPass@1")
        t0 = datetime(2026, 11, 28, 10, 0, 0, tzinfo=timezone.utc)
        for idx in range(5):
            self.assertIsNone(auth_mod.login("lock_victim", f"WrongPass{idx}", now=t0 + timedelta(seconds=idx)))
        self.assertTrue(auth_mod.is_login_locked("lock_victim", now=t0 + timedelta(minutes=2)))
        # Even the right password is rejected while locked
        self.assertIsNone(auth_mod.login("lock_victim", "RightPass@1", now=t0 + timedelta(minutes=3)))
        # After 5 minutes lockout expires and right password works
        self.assertFalse(auth_mod.is_login_locked("lock_victim", now=t0 + timedelta(minutes=6)))
        self.assertIsNotNone(auth_mod.login("lock_victim", "RightPass@1", now=t0 + timedelta(minutes=6)))

        # 30-minute session timeout
        self.assertFalse(auth_mod.is_session_expired(t0, now=t0 + timedelta(minutes=29)))
        self.assertTrue(auth_mod.is_session_expired(t0, now=t0 + timedelta(minutes=31)))

        # Hyderabad wards seeded & nearest ward helper matches coordinates accurately
        wards = ward_mod.get_all_wards()
        self.assertGreaterEqual(len(wards), 15)
        nearest = ward_mod.find_nearest_ward(17.4486, 78.3908)
        self.assertEqual(nearest["name"], "Madhapur")
        inc_w = comp_mod.submit_complaint(
            lock_user,
            self.water["id"],
            "Water pipe leaking near cyber towers",
            "Madhapur Main",
            pincode="522002",
            area_id=1,
            latitude=17.4490,
            longitude=78.3910,
        )
        saved_w = next(c for c in comp_mod.get_complaints(lock_user) if c["incident_id"] == inc_w)
        self.assertEqual(saved_w["ward_name"], "Madhapur")

        # Synthetic dataset exists, >= 5000 rows, multilingual, marked is_synthetic=True
        import csv
        csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "synthetic_complaints.csv")
        self.assertTrue(os.path.exists(csv_path))
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertGreaterEqual(len(rows), 5000)
        langs = {r["language"] for r in rows}
        self.assertEqual(langs, {"en", "te", "hi", "mixed"})
        self.assertTrue(all(r["is_synthetic"] == "True" for r in rows))



if __name__ == "__main__":
    unittest.main()
