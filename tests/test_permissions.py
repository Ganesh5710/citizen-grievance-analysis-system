"""
Acceptance tests for the Citizen Grievance System.
Compatible with both stdlib unittest and pytest:
    python -m unittest discover -s tests -v
    python -m pytest tests/ -v
"""

import importlib
import os
import shutil
import sqlite3
import tempfile
import unittest

_TMP_DIR = tempfile.mkdtemp(prefix="cgs_test_")
_TMP_DB = os.path.join(_TMP_DIR, "test_complaints.db")
os.environ["CGS_DB_PATH"] = _TMP_DB

import core.auth as auth_mod
import core.classifier as classifier_mod
import core.complaints as comp_mod
import core.database as db_mod


def tearDownModule():
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


class TestCitizenGrievanceSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.superadmin = auth_mod.login("superadmin", "Admin@123")
        assert cls.superadmin is not None, "Seeded superadmin missing"
        cls.demo_admin = auth_mod.login("admin", "Admin@123")
        assert cls.demo_admin is not None, "Seeded admin missing"
        cls.demo_employee = auth_mod.login("employee", "Admin@123")
        assert cls.demo_employee is not None, "Seeded employee missing"

        cls.employee = auth_mod.create_staff_account(
            cls.superadmin, "emp_fixture", "Test Emp", "Employee@123", "employee"
        )
        cls.admin_user = auth_mod.create_staff_account(
            cls.superadmin, "admin_fixture", "Test Admin", "Admin@123", "admin"
        )
        cls.citizen_a = auth_mod.register("citizen_a", "Citizen A", "Password@1")
        cls.citizen_b = auth_mod.register("citizen_b", "Citizen B", "Password@2")

        cls.incident_id_a = comp_mod.submit_complaint(
            cls.citizen_a,
            "Water pipe leakage near main road urgent repair needed",
            "North Zone",
        )
        complaints_a = comp_mod.get_complaints(cls.citizen_a)
        cls.complaint_a = next(
            c for c in complaints_a if c["incident_id"] == cls.incident_id_a
        )

    # ------------------------------------------------------------------
    # 1. A user cannot see another user's complaints
    # ------------------------------------------------------------------
    def test_1_user_cannot_see_another_users_complaints(self):
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_detail(self.citizen_b, self.complaint_a["id"])

        b_complaints = comp_mod.get_complaints(self.citizen_b)
        b_ids = [c["id"] for c in b_complaints]
        self.assertNotIn(self.complaint_a["id"], b_ids)

    # ------------------------------------------------------------------
    # 2. user, admin, super_admin calling update_complaint() raises PermissionError
    # ------------------------------------------------------------------
    def test_2_non_employee_cannot_update_complaint(self):
        for actor in (self.citizen_a, self.admin_user, self.superadmin, None):
            with self.assertRaises(PermissionError):
                comp_mod.update_complaint(
                    actor, self.complaint_a["id"], "Resolved", "unauthorized"
                )

    # ------------------------------------------------------------------
    # 3. Employee can update status, and a complaint_updates row is created
    # ------------------------------------------------------------------
    def test_3_employee_update_creates_audit_row(self):
        comp_mod.update_complaint(
            self.employee, self.complaint_a["id"], "In Progress", "Dispatching crew"
        )
        updated = comp_mod.get_complaint_detail(self.employee, self.complaint_a["id"])
        self.assertEqual(updated["status"], "In Progress")

        history = comp_mod.get_complaint_history(self.citizen_a, self.complaint_a["id"])
        self.assertGreaterEqual(len(history), 1)
        latest = history[-1]
        self.assertEqual(latest["old_status"], "Pending")
        self.assertEqual(latest["new_status"], "In Progress")
        self.assertEqual(latest["remark"], "Dispatching crew")
        self.assertEqual(latest["employee_username"], self.employee["username"])

    # ------------------------------------------------------------------
    # 4. Public sign-up cannot create a non-user role
    # ------------------------------------------------------------------
    def test_4_public_signup_always_creates_user_and_validates(self):
        new_user = auth_mod.register("newcitizen99", "New Citizen", "Secure@Pass1")
        self.assertEqual(new_user["role"], "user")

        with self.assertRaises(ValueError):
            auth_mod.register("someone22", "Someone", "short")
        with self.assertRaises(ValueError):
            auth_mod.register("ab", "Short User", "ValidPass@1")
        with self.assertRaises(ValueError):
            auth_mod.register("bad user!", "Bad Char", "ValidPass@1")
        with self.assertRaises(ValueError):
            auth_mod.register("validname1", " ", "ValidPass@1")

    # ------------------------------------------------------------------
    # 5. Only super_admin can call create_staff_account()
    # ------------------------------------------------------------------
    def test_5_only_super_admin_can_create_staff_account(self):
        created = auth_mod.create_staff_account(
            self.superadmin, "new_emp_x", "New Emp", "EmpPass@1", "employee"
        )
        self.assertEqual(created["role"], "employee")

        for actor in (self.citizen_a, self.employee, self.admin_user, None):
            with self.assertRaises(PermissionError):
                auth_mod.create_staff_account(
                    actor, "unauth_emp", "Unauth", "EmpPass@1", "employee"
                )

        with self.assertRaises(ValueError):
            auth_mod.create_staff_account(
                self.superadmin, "bad_staff", "Bad Role", "EmpPass@1", "user"
            )

    # ------------------------------------------------------------------
    # 6. A user cannot read the history of someone else's complaint
    # ------------------------------------------------------------------
    def test_6_user_cannot_read_others_history(self):
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_history(self.citizen_b, self.complaint_a["id"])

    # ------------------------------------------------------------------
    # 7. Data persists after restarting the app (module reload + fresh connection)
    # ------------------------------------------------------------------
    def test_7_data_persists_after_restart(self):
        # Reload database/auth/complaints to simulate a full app restart
        importlib.reload(db_mod)
        importlib.reload(auth_mod)
        importlib.reload(comp_mod)

        # Demo seed must not wipe or re-insert when users already exist
        self.assertFalse(db_mod.seed_demo_accounts())

        citizen_relogged = auth_mod.login("citizen_a", "Password@1")
        self.assertIsNotNone(citizen_relogged)

        persisted = comp_mod.get_complaints(citizen_relogged)
        match = next((c for c in persisted if c["incident_id"] == self.incident_id_a), None)
        self.assertIsNotNone(match)
        self.assertEqual(match["department"], "Water")

    # ------------------------------------------------------------------
    # Additional service-layer & security checks
    # ------------------------------------------------------------------
    def test_staff_cannot_submit_complaint(self):
        for actor in (self.employee, self.admin_user, self.superadmin):
            with self.assertRaises(PermissionError):
                comp_mod.submit_complaint(actor, "Broken streetlight near plaza")

    def test_login_failures_return_none(self):
        self.assertIsNone(auth_mod.login("nonexistent_user", "WrongPass@1"))
        self.assertIsNone(auth_mod.login("citizen_a", "WrongPassword123"))

    def test_classifier_departments_and_priorities(self):
        self.assertEqual(classifier_mod.classify("Water pipeline burst urgent"), ("Water", "High"))
        self.assertEqual(classifier_mod.classify("Pothole on main road"), ("Roads", "Medium"))
        self.assertEqual(classifier_mod.classify("Street transformer sparking fire"), ("Electricity", "High"))
        self.assertEqual(classifier_mod.classify("Garbage overflow on street"), ("Sanitation", "Medium"))
        self.assertEqual(classifier_mod.classify("Traffic signal stuck"), ("Traffic", "Medium"))
        self.assertEqual(classifier_mod.classify("Need bench at park"), ("General", "Low"))

    def test_foreign_keys_pragma_enabled(self):
        conn = db_mod.get_connection()
        try:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            self.assertEqual(fk, 1)
        finally:
            conn.close()

    def test_legacy_db_migration(self):
        legacy_db = os.path.join(_TMP_DIR, "legacy_test.db")
        conn = sqlite3.connect(legacy_db)
        conn.execute(
            "CREATE TABLE complaints (id TEXT, complaint TEXT, department TEXT, "
            "confidence TEXT, priority TEXT, location TEXT, status TEXT, time TEXT)"
        )
        conn.execute(
            "INSERT INTO complaints VALUES ('CG-1', 'old', 'Water', '90%', 'Low', 'Zone A', 'Pending', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        prev = os.environ.get("CGS_DB_PATH")
        try:
            os.environ["CGS_DB_PATH"] = legacy_db
            db_mod.init_db()
            check_conn = db_mod.get_connection()
            try:
                cols = {r["name"] for r in check_conn.execute("PRAGMA table_info(complaints)").fetchall()}
                self.assertIn("incident_id", cols)
                self.assertIn("user_id", cols)
                legacy_rows = check_conn.execute("SELECT COUNT(*) FROM complaints_legacy_v1").fetchone()[0]
                self.assertEqual(legacy_rows, 1)
            finally:
                check_conn.close()
        finally:
            if prev is not None:
                os.environ["CGS_DB_PATH"] = prev


if __name__ == "__main__":
    unittest.main()
