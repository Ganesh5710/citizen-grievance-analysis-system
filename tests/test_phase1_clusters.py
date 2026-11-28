"""
tests/test_phase1_clusters.py
Phase 1 test suite:
  - Radius-bounded (300 m) same-department duplicate detection + embedding fallback
  - Citizen support_complaint uniqueness & self-support guard
  - Cluster count priority escalation (>=3 Medium, >=10 High, never lowers, actor='system')
  - Jurisdiction-scoped Admin merge & unmerge with mandatory reason + audit log
  - Resolving primary complaint auto-resolves cluster siblings & creates notifications
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import core.auth as auth_mod
import core.clusters as cl_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod

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
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_phase1_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_p1.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"
    os.environ["CGS_DAILY_SUBMISSION_LIMIT"] = "50"
    os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"
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


class TestPhase1DuplicateClusters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db_mod.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (1, 1, 'Guntur', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (2, 1, 'Visakhapatnam', 1)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (1, 1, 'Arundelpet', 16.30670, 80.43650, 1)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (2, 2, 'MVP Colony', 17.73800, 83.33200, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('530001', 2, 2, 1)")
            conn.commit()

        cls.sa = auth_mod.login("superadmin", "SeedTest@123")
        assert cls.sa is not None
        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_id = depts["Water"]
        cls.roads_id = depts["Roads"]

        cls.adm_gnt = auth_mod.create_admin_account(cls.sa, "p1_adm_gnt", "Gnt Admin", "Pass@1234", [1])
        cls.adm_vzg = auth_mod.create_admin_account(cls.sa, "p1_adm_vzg", "Vzg Admin", "Pass@1234", [2])
        cls.emp_gnt_w = auth_mod.create_user_account(
            cls.sa, "p1_emp_w", "Gnt Water Emp", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=1,
        )
        cls.citizens = [
            auth_mod.register(f"p1_cit_{i}", f"Citizen {i}", "Pass@1234")
            for i in range(12)
        ]

    def _get_by_inc(self, inc_id: str) -> dict:
        return next(c for c in comp_mod.get_complaints(self.sa) if c["incident_id"] == inc_id)

    def test_01_radius_and_fallback_similarity_detection(self):
        inc = comp_mod.submit_complaint(
            self.citizens[0],
            self.water_id,
            "Main drinking water valve leaking near Arundelpet park junction",
            "Park Junction",
            pincode="522002",
            area_id=1,
            latitude=16.30670,
            longitude=80.43650,
        )
        base = self._get_by_inc(inc)

        # ~100m away, same dept, similar text -> detected
        near_res = cl_mod.find_similar_open_complaints(
            self.water_id,
            "Drinking water valve leaking heavily near Arundelpet park junction",
            latitude=16.30740,
            longitude=80.43650,
            force_tfidf=True,
        )
        self.assertEqual(near_res["method"], "tfidf_fallback")
        self.assertTrue(any(m["complaint_id"] == base["id"] for m in near_res["matches"]))

        # ~1,100m away -> NOT detected within 300m radius
        far_res = cl_mod.find_similar_open_complaints(
            self.water_id,
            "Drinking water valve leaking heavily near Arundelpet park junction",
            latitude=16.31700,
            longitude=80.43650,
        )
        self.assertFalse(any(m["complaint_id"] == base["id"] for m in far_res["matches"]))

        # Different department within 100m -> NOT detected
        other_dept_res = cl_mod.find_similar_open_complaints(
            self.roads_id,
            "Drinking water valve leaking heavily near Arundelpet park junction",
            latitude=16.30740,
            longitude=80.43650,
        )
        self.assertFalse(any(m["complaint_id"] == base["id"] for m in other_dept_res["matches"]))

    def test_02_support_uniqueness_self_guard_and_threshold_escalation(self):
        # Start a Low priority complaint (mild phrasing)
        inc = comp_mod.submit_complaint(
            self.citizens[0],
            self.roads_id,
            "Small faded lane paint near corner temple wall",
            "Corner Wall",
            pincode="522002",
            area_id=1,
        )
        c = self._get_by_inc(inc)
        if c["priority"] != "Low":
            comp_mod.update_complaint(self.adm_gnt, c["id"], priority="Low", remark="Reset Low for support threshold test")
            c = comp_mod.get_complaint_detail(self.adm_gnt, c["id"])

        # Own complaint support blocked
        with self.assertRaises(ValueError):
            cl_mod.support_complaint(self.citizens[0], c["id"])

        # Staff cannot support
        with self.assertRaises(PermissionError):
            cl_mod.support_complaint(self.emp_gnt_w, c["id"])

        # Citizen 1 supports -> report_count = 2 (1 member + 1 support)
        s1 = cl_mod.support_complaint(self.citizens[1], c["id"])
        self.assertEqual(s1["report_count"], 2)

        # Duplicate support by Citizen 1 blocked
        with self.assertRaises(ValueError):
            cl_mod.support_complaint(self.citizens[1], c["id"])

        # Citizen 2 supports -> report_count = 3 -> Low rises to Medium with system audit row!
        s2 = cl_mod.support_complaint(self.citizens[2], c["id"])
        self.assertEqual(s2["report_count"], 3)
        c_mid = comp_mod.get_complaint_detail(self.adm_gnt, c["id"])
        self.assertEqual(c_mid["priority"], "Medium")

        # Citizens 3..9 support -> report_count reaches 10 -> Medium rises to High!
        for idx in range(3, 10):
            last_s = cl_mod.support_complaint(self.citizens[idx], c["id"])
        self.assertEqual(last_s["report_count"], 10)
        c_hi = comp_mod.get_complaint_detail(self.adm_gnt, c["id"])
        self.assertEqual(c_hi["priority"], "High")

        hist = comp_mod.get_complaint_history(self.adm_gnt, c["id"])
        self.assertTrue(any(h["actor_username"] == "system" and h["field_name"] == "priority" and h["new_value"] == "High" for h in hist))

    def test_03_merge_unmerge_jurisdiction_and_primary_resolution(self):
        inc_pri = comp_mod.submit_complaint(
            self.citizens[0],
            self.water_id,
            "Burst water pipe near Arundelpet clock tower road",
            "Clock Tower",
            pincode="522002",
            area_id=1,
        )
        inc_dup = comp_mod.submit_complaint(
            self.citizens[1],
            self.water_id,
            "Same broken water pipe leaking near Arundelpet tower",
            "Clock Tower East",
            pincode="522002",
            area_id=1,
        )
        inc_vzg = comp_mod.submit_complaint(
            self.citizens[2],
            self.water_id,
            "Water pipe leak in MVP Colony Visakhapatnam",
            "MVP Sector 1",
            pincode="530001",
            area_id=2,
        )
        c_pri = self._get_by_inc(inc_pri)
        c_dup = self._get_by_inc(inc_dup)
        c_vzg = self._get_by_inc(inc_vzg)

        # Missing reason fails
        with self.assertRaises(ValueError):
            cl_mod.merge_complaints(self.adm_gnt, c_pri["id"], c_dup["id"], "")

        # Employee cannot merge
        with self.assertRaises(PermissionError):
            cl_mod.merge_complaints(self.emp_gnt_w, c_pri["id"], c_dup["id"], "Duplicate pipe issue")

        # Cross-jurisdiction admin blocked from merging VZG complaint into GNT complaint
        with self.assertRaises(PermissionError):
            cl_mod.merge_complaints(self.adm_gnt, c_pri["id"], c_vzg["id"], "Cross district attempt")

        # Merge works within jurisdiction
        merged = cl_mod.merge_complaints(self.adm_gnt, c_pri["id"], c_dup["id"], "Same leaking pipe at clock tower")
        self.assertEqual(merged["member_count"], 2)

        # Support added on primary raises count to 3 -> ensures priority at least Medium
        cl_mod.support_complaint(self.citizens[4], c_pri["id"])

        # Unmerge & re-merge check
        unmerged = cl_mod.unmerge_complaint(self.adm_gnt, c_dup["id"], "Re-checking separation")
        self.assertEqual(unmerged["member_count"], 1)
        cl_mod.merge_complaints(self.adm_gnt, c_pri["id"], c_dup["id"], "Confirmed same main valve")

        # Assign and resolve primary complaint -> auto-resolves linked sibling + notifies reporter & supporter!
        comp_mod.assign_task(self.adm_gnt, c_pri["id"], self.emp_gnt_w["id"], instructions="Repair valve")
        comp_mod.update_task_status(
            self.emp_gnt_w,
            c_pri["id"],
            new_status="Resolved",
            remark="Replaced burst main valve and pressure-tested line cleanly.",
        )

        c_dup_after = comp_mod.get_complaint_detail(self.adm_gnt, c_dup["id"])
        self.assertEqual(c_dup_after["status"], "Resolved")

        dup_hist = comp_mod.get_complaint_history(self.adm_gnt, c_dup["id"])
        self.assertTrue(
            any(h["actor_username"] == "system" and h["new_value"] == "Resolved" for h in dup_hist)
        )

        # Notifications written for sibling reporter (citizen 1) & supporter (citizen 4)
        with db_mod.get_connection() as conn:
            notif_uids = {
                r["user_id"]
                for r in conn.execute("SELECT user_id FROM notifications WHERE user_id IS NOT NULL").fetchall()
            }
        self.assertIn(self.citizens[1]["id"], notif_uids)
        self.assertIn(self.citizens[4]["id"], notif_uids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
