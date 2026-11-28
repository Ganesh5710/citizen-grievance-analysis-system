"""
tests/test_phase5_selfservice.py
Phase 5 test suite:
  - Citizen forgot-password (hashed single-use 15-min code, expiry, rate limit, generic response)
  - Complaint withdrawal (direct pre-assignment vs Admin-approved/rejected post-assignment)
  - Department transfer with mandatory reason (resets assignment & SLA, preserves history, notifies citizen)
  - Internal staff notes hidden from citizens and unauthorized employees
  - DPDP Act 2023 JSON/CSV personal export + account anonymization while keeping statistical records
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import tempfile
import unittest

import core.auth as auth_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.privacy as privacy_mod
import notifications.service as notif_mod

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
        "CGS_RESET_CODE_TTL_MINUTES",
        "CGS_RESET_RATE_LIMIT_PER_HOUR",
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_phase5_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_p5.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "TestSeed@2026"
    os.environ.pop("CGS_SUPERADMIN_PASSWORD", None)
    os.environ.pop("CGS_ADMIN_PASSWORD", None)
    os.environ.pop("CGS_EMPLOYEE_PASSWORD", None)
    os.environ["CGS_DAILY_SUBMISSION_LIMIT"] = "50"
    os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"
    os.environ["CGS_RESET_CODE_TTL_MINUTES"] = "15"
    os.environ["CGS_RESET_RATE_LIMIT_PER_HOUR"] = "3"

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


def _get_ap_location() -> dict:
    with db_mod.get_connection() as conn:
        row = conn.execute(
            """
            SELECT p.pincode, a.id AS area_id, a.mandal_id, a.district_id, d.state_id
            FROM pincodes p
            JOIN areas a ON a.id = p.area_id
            JOIN districts d ON d.id = a.district_id
            ORDER BY d.id ASC, a.id ASC LIMIT 1
            """
        ).fetchone()
    if row is not None:
        return dict(row)

    with db_mod.get_connection() as conn, conn:
        conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
        conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (1, 1, 'Guntur', 1)")
        conn.execute(
            "INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) "
            "VALUES (1, 1, 'Arundelpet', 16.3067, 80.4365, 1)"
        )
        conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
    return {
        "pincode": "522002",
        "area_id": 1,
        "mandal_id": None,
        "district_id": 1,
        "state_id": 1,
    }


class TestPhase5SelfService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loc = _get_ap_location()
        cls.sa = auth_mod.login("superadmin", "TestSeed@2026")
        assert cls.sa is not None
        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_id = depts["Water"]
        cls.elec_id = depts["Electricity"]

        cls.admin = auth_mod.create_admin_account(
            cls.sa, "p5_admin", "Phase 5 District Admin", "Pass@1234", [cls.loc["district_id"]]
        )
        cls.emp_water = auth_mod.create_user_account(
            cls.sa, "p5_emp_water", "P5 Water Officer", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=cls.loc["district_id"],
        )
        cls.emp_elec = auth_mod.create_user_account(
            cls.sa, "p5_emp_elec", "P5 Elec Officer", "Pass@1234", "employee",
            department_id=cls.elec_id, district_id=cls.loc["district_id"],
        )

        cls.cit1 = auth_mod.register("p5_citizen1", "Suresh Rao", "Pass@1234", email="suresh.p5@example.com")
        cls.cit2 = auth_mod.register("p5_citizen2", "Lakshmi Devi", "Pass@1234", email="lakshmi.p5@example.com")

    def _get_by_inc(self, inc_id: str) -> dict:
        return next(c for c in comp_mod.get_complaints(self.sa) if c["incident_id"] == inc_id)

    def test_01_forgot_password_reset_ttl_single_use_and_rate_limit(self):
        sent_bodies: list[str] = []

        def fake_sender(recipient: str, subject: str, body: str):
            sent_bodies.append(body)

        t0 = datetime(2026, 11, 28, 10, 0, 0, tzinfo=timezone.utc)
        msg_unknown = auth_mod.request_password_reset("no_such_user@example.com", now=t0, smtp_sender=fake_sender)
        msg_valid = auth_mod.request_password_reset("suresh.p5@example.com", now=t0, smtp_sender=fake_sender)
        self.assertEqual(msg_unknown, msg_valid)
        self.assertEqual(len(sent_bodies), 1)
        code = [tok for tok in sent_bodies[-1].split() if len(tok) == 6 and tok.isdigit()][0]

        # Expired after 16 minutes
        with self.assertRaises(ValueError):
            auth_mod.confirm_password_reset(
                "suresh.p5@example.com",
                code,
                "NewPass#2026",
                now=t0 + timedelta(minutes=16),
            )

        # Valid inside 15 minutes
        res = auth_mod.confirm_password_reset(
            "p5_citizen1",
            code,
            "NewPass#2026",
            now=t0 + timedelta(minutes=5),
        )
        self.assertEqual(res["username"], "p5_citizen1")

        # Single-use enforced
        with self.assertRaises(ValueError):
            auth_mod.confirm_password_reset(
                "p5_citizen1",
                code,
                "AnotherPass#2026",
                now=t0 + timedelta(minutes=6),
            )

        self.assertIsNotNone(auth_mod.login("p5_citizen1", "NewPass#2026"))

        # Rate limit check (CGS_RESET_RATE_LIMIT_PER_HOUR = 3)
        auth_mod.register("p5_reset_rl", "Rate Limit User", "Pass@1234", email="ratelimit.p5@example.com")
        for _ in range(3):
            auth_mod.request_password_reset("ratelimit.p5@example.com", now=t0, smtp_sender=fake_sender)
        with self.assertRaises(ValueError):
            auth_mod.request_password_reset(
                "ratelimit.p5@example.com",
                now=t0 + timedelta(minutes=1),
                smtp_sender=fake_sender,
            )

    def test_02_withdraw_direct_vs_admin_reviewed_and_transfer(self):
        # 1. Pre-assignment withdrawal goes straight to Withdrawn
        inc_pre = comp_mod.submit_complaint(
            self.cit1,
            self.water_id,
            "Water pipeline leakage near school gate since 2 days",
            "Brodipet Gate",
            pincode=self.loc["pincode"],
            area_id=self.loc["area_id"],
        )
        c_pre = self._get_by_inc(inc_pre)
        self.assertEqual(c_pre["status"], "Awaiting Assignment")
        res_pre = comp_mod.withdraw_complaint(self.cit1, c_pre["id"], "Issue got resolved locally by residents")
        self.assertEqual(res_pre["status"], "Withdrawn")
        self.assertEqual(res_pre["withdrawal_action"], "withdrawn")

        # Other citizen cannot withdraw cit1 complaint
        inc_asg = comp_mod.submit_complaint(
            self.cit1,
            self.water_id,
            "Drinking water valve broken and muddy supply for 3 days",
            "Main Bazaar",
            pincode=self.loc["pincode"],
            area_id=self.loc["area_id"],
        )
        c_asg = self._get_by_inc(inc_asg)
        with self.assertRaises(PermissionError):
            comp_mod.withdraw_complaint(self.cit2, c_asg["id"], "Attempting unauthorized withdraw")

        # Assign to water employee -> post-assignment withdraw becomes a pending request
        comp_mod.assign_task(self.admin, c_asg["id"], self.emp_water["id"])
        req_w = comp_mod.withdraw_complaint(self.cit1, c_asg["id"], "Submitting duplicate request by mistake")
        self.assertEqual(req_w["withdrawal_action"], "requested")
        self.assertTrue(req_w["withdrawal_requested"])
        self.assertEqual(req_w["status"], "Assigned")

        # Employee cannot approve withdrawal
        with self.assertRaises(PermissionError):
            comp_mod.review_withdrawal_request(self.emp_water, c_asg["id"], True, "Unauthorized")

        # Admin rejects
        rej = comp_mod.review_withdrawal_request(self.admin, c_asg["id"], False, "Field team already dispatched")
        self.assertFalse(rej["withdrawal_requested"])
        self.assertEqual(rej["status"], "Assigned")

        # Re-request & approve works cleanly on another assigned complaint
        inc_app = comp_mod.submit_complaint(
            self.cit1,
            self.water_id,
            "Blocked municipal water tap near temple lane for 2 days",
            "Temple Lane",
            pincode=self.loc["pincode"],
            area_id=self.loc["area_id"],
        )
        c_app = self._get_by_inc(inc_app)
        comp_mod.assign_task(self.admin, c_app["id"], self.emp_water["id"])
        comp_mod.withdraw_complaint(self.cit1, c_app["id"], "Issue fixed privately at home")
        app_res = comp_mod.review_withdrawal_request(self.admin, c_app["id"], True, "Confirmed resolved")
        self.assertEqual(app_res["status"], "Withdrawn")

        # Transfer between departments requires >=5 char reason, resets assignment & SLA, notifies citizen
        with self.assertRaises(ValueError):
            comp_mod.transfer_complaint(self.admin, c_asg["id"], self.elec_id, "no")
        with self.assertRaises(PermissionError):
            comp_mod.transfer_complaint(self.emp_water, c_asg["id"], self.elec_id, "Valid reason here")

        transferred = comp_mod.transfer_complaint(
            self.admin,
            c_asg["id"],
            self.elec_id,
            "Pump wiring fault belongs to Electricity team",
        )
        self.assertEqual(transferred["department"], "Electricity")
        self.assertIsNone(transferred["assigned_to"])
        self.assertEqual(transferred["status"], "Awaiting Assignment")
        history = comp_mod.get_complaint_history(self.admin, c_asg["id"])
        self.assertTrue(any(h["field_name"] == "department" and h["new_value"] == "Electricity" for h in history))
        inbox = notif_mod.get_user_inbox(self.cit1, limit=20)
        self.assertTrue(any(n["complaint_id"] == c_asg["id"] and n["type"] == "complaint_transferred" for n in inbox))

    def test_03_internal_notes_hidden_from_citizens_and_other_staff(self):
        inc = comp_mod.submit_complaint(
            self.cit1,
            self.elec_id,
            "Transformer sparks intermittently near hospital road since 1 day",
            "Hospital Road",
            pincode=self.loc["pincode"],
            area_id=self.loc["area_id"],
        )
        c = self._get_by_inc(inc)
        comp_mod.assign_task(self.admin, c["id"], self.emp_elec["id"])

        note = comp_mod.add_internal_note(
            self.emp_elec, c["id"], "Inspecting 11kV line load privately before field crew arrival."
        )
        self.assertIn("11kV", note["note"])
        staff_notes = comp_mod.get_internal_notes(self.admin, c["id"])
        self.assertEqual(len(staff_notes), 1)

        with self.assertRaises(PermissionError):
            comp_mod.get_internal_notes(self.cit1, c["id"])
        with self.assertRaises(PermissionError):
            comp_mod.add_internal_note(self.cit1, c["id"], "Citizen injection attempt")
        with self.assertRaises(PermissionError):
            comp_mod.get_internal_notes(self.emp_water, c["id"])

    def test_04_dpdp_export_and_account_anonymization(self):
        comp_mod.submit_complaint(
            self.cit2,
            self.elec_id,
            "Overhead power wire hanging dangerously over footpath for 2 days",
            "Brodipet Lane 4",
            pincode=self.loc["pincode"],
            area_id=self.loc["area_id"],
        )
        exp_json = privacy_mod.export_own_data(self.cit2, "json")
        payload = json.loads(exp_json["content"])
        self.assertEqual(payload["profile"]["username"], "p5_citizen2")
        self.assertGreaterEqual(len(payload["complaints"]), 1)
        self.assertTrue(
            all("suresh" not in c["text"].lower() and "11kv" not in c["text"].lower() for c in payload["complaints"])
        )
        exp_csv = privacy_mod.export_own_data(self.cit2, "csv")
        self.assertIn("incident_id", exp_csv["content"])

        anon = privacy_mod.anonymize_own_account(self.cit2, "DPDP user deletion test")
        self.assertEqual(anon["anonymized_user_id"], self.cit2["id"])
        self.assertTrue(anon["anonymized_username"].startswith("anon_user_"))
        self.assertIsNone(auth_mod.login("p5_citizen2", "Pass@1234"))
        with db_mod.get_connection() as conn:
            u_row = conn.execute(
                "SELECT username, full_name, email, is_active, anonymized_at FROM users WHERE id = ?",
                (self.cit2["id"],),
            ).fetchone()
            self.assertEqual(u_row["is_active"], 0)
            self.assertIsNotNone(u_row["anonymized_at"])
            self.assertEqual(u_row["email"], "")
            self.assertEqual(u_row["full_name"], "Anonymized Citizen")
            c_cnt = conn.execute(
                "SELECT COUNT(*) FROM complaints WHERE user_id = ? AND is_deleted = 0",
                (self.cit2["id"],),
            ).fetchone()[0]
            self.assertEqual(c_cnt, len(payload["complaints"]))


if __name__ == "__main__":
    unittest.main()
