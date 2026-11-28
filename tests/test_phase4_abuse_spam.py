"""
tests/test_phase4_abuse_spam.py
Phase 4 test suite:
  - Gibberish, identical repeated submissions, and EN/HI/TE abuse word detection routed to review_queue
  - Genuine Telugu/Hindi Unicode complaints are NOT false-positived as gibberish
  - Daily submission limit (10) and cooldown enforcement
  - Admin confirms/releases suspected spam; repeated confirmed spam blocks user; Super Admin ONLY can lift block (audit-logged)
  - Optional CAPTCHA arithmetic challenge helper
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

import core.auth as auth_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.ml_service as ml_svc
import core.moderation as mod_mod

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
        "CGS_SPAM_BLOCK_THRESHOLD",
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_phase4_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_p4.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"
    os.environ["CGS_DAILY_SUBMISSION_LIMIT"] = "10"
    os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"
    os.environ["CGS_SPAM_BLOCK_THRESHOLD"] = "3"
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


class TestPhase4AbuseAndSpam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db_mod.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (1, 1, 'Guntur', 1)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (1, 1, 'Arundelpet', 16.3067, 80.4365, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.commit()

        cls.sa = auth_mod.login("superadmin", "SeedTest@123")
        assert cls.sa is not None
        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_id = depts["Water"]
        cls.adm = auth_mod.create_admin_account(cls.sa, "p4_adm", "P4 Admin", "Pass@1234", [1])
        cls.emp = auth_mod.create_user_account(
            cls.sa, "p4_emp", "P4 Emp", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=1,
        )

    def _get_by_inc(self, inc_id: str) -> dict:
        return next(c for c in comp_mod.get_complaints(self.sa) if c["incident_id"] == inc_id)

    def test_01_gibberish_repeat_and_multilingual_abuse_flagged(self):
        cit = auth_mod.register("p4_cit1", "Moderation Tester", "Pass@1234")

        # Valid Telugu & Hindi Unicode text must NOT be flagged as gibberish or abuse
        inc_te = comp_mod.submit_complaint(
            cit, self.water_id, "అరుండెల్‌పేటలో గత రెండు రోజులుగా త్రాగునీరు రావడం లేదు.", "Main Road",
            pincode="522002", area_id=1,
        )
        c_te = self._get_by_inc(inc_te)
        self.assertEqual(c_te["status"], "Awaiting Assignment")
        self.assertEqual(c_te["is_spam"], 0)

        inc_hi = comp_mod.submit_complaint(
            cit, self.water_id, "पिछले दो दिनों से हमारे मोहल्ले में पीने का पानी नहीं आ रहा है।", "Lane 3",
            pincode="522002", area_id=1,
        )
        c_hi = self._get_by_inc(inc_hi)
        self.assertEqual(c_hi["status"], "Awaiting Assignment")
        self.assertEqual(c_hi["is_spam"], 0)

        # Gibberish flagged -> status='Submitted', is_spam=1, queued as Suspected spam
        inc_gib = comp_mod.submit_complaint(
            cit, self.water_id, "xzqwkjhgf mnbvcxz qwrtyplkjhgfds", "Somewhere",
            pincode="522002", area_id=1,
        )
        c_gib = self._get_by_inc(inc_gib)
        self.assertEqual(c_gib["status"], "Submitted")
        self.assertEqual(c_gib["is_spam"], 1)
        self.assertIsNone(c_gib["assigned_to"])

        # Identical repeat by same user flagged
        inc_dup = comp_mod.submit_complaint(
            cit, self.water_id, "పిछले दो दिनों से हमारे मोहल्ले में पीने का पानी नहीं आ रहा है।".replace("పి", "पि"), "Lane 3",
            pincode="522002", area_id=1,
        )
        c_dup = self._get_by_inc(inc_dup)
        self.assertEqual(c_dup["status"], "Submitted")
        self.assertEqual(c_dup["is_spam"], 1)

        # Multilingual profanity (EN / HI / TE) flagged
        for idx, toxic in enumerate([
            "You stupid moron fix the water pipe right now",
            "Yeh bakwas chutiya system water supply band hai",
            "donga yedava officers water leak repair cheyandi",
        ]):
            inc_ab = comp_mod.submit_complaint(
                cit, self.water_id, toxic, f"Loc {idx}",
                pincode="522002", area_id=1,
            )
            c_ab = self._get_by_inc(inc_ab)
            self.assertEqual(c_ab["is_spam"], 1)
            self.assertEqual(c_ab["status"], "Submitted")

        pending = ml_svc.get_review_queue(self.adm, status_filter="Pending")
        self.assertTrue(any(r["incident_id"] == inc_gib and "Suspected spam" in r["reason"] for r in pending))

    def test_02_daily_limit_and_cooldown_enforced(self):
        cit_rate = auth_mod.register("p4_cit_rate", "Rate Tester", "Pass@1234")
        prev_cd = os.environ.get("CGS_SUBMISSION_COOLDOWN_SECONDS")
        try:
            # First test cooldown = 30s blocks immediate 2nd submission
            os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "30"
            comp_mod.submit_complaint(
                cit_rate, self.water_id, "First genuine water pipe issue report.", "Loc 1",
                pincode="522002", area_id=1,
            )
            with self.assertRaises(ValueError) as ctx_cd:
                comp_mod.submit_complaint(
                    cit_rate, self.water_id, "Second complaint right away.", "Loc 2",
                    pincode="522002", area_id=1,
                )
            self.assertIn("wait", str(ctx_cd.exception).lower())

            # Now disable cooldown and hit daily limit = 10
            os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"
            for i in range(2, 11):
                comp_mod.submit_complaint(
                    cit_rate, self.water_id, f"Unique valid complaint number {i} on water line.", f"Street {i}",
                    pincode="522002", area_id=1,
                )
            with self.assertRaises(ValueError) as ctx_lim:
                comp_mod.submit_complaint(
                    cit_rate, self.water_id, "Eleventh complaint today exceeds limit.", "Street 11",
                    pincode="522002", area_id=1,
                )
            self.assertIn("limit", str(ctx_lim.exception).lower())
        finally:
            if prev_cd is None:
                os.environ.pop("CGS_SUBMISSION_COOLDOWN_SECONDS", None)
            else:
                os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = prev_cd

    def test_03_confirm_spam_blocks_user_and_only_super_admin_lifts_block(self):
        cit_sp = auth_mod.register("p4_cit_spammer", "Repeat Spammer", "Pass@1234")

        # File 4 flagged items (1 to Release as valid, 3 to Confirm Spam -> triggers block)
        flagged_incs = []
        for i in range(4):
            inc = comp_mod.submit_complaint(
                cit_sp, self.water_id, f"aaaaaaaabbbbcccc {i} spam noise payload", f"Spot {i}",
                pincode="522002", area_id=1,
            )
            flagged_incs.append(inc)

        rq_items = [
            r for r in ml_svc.get_review_queue(self.adm, status_filter="Pending")
            if r["incident_id"] in flagged_incs
        ]
        self.assertEqual(len(rq_items), 4)

        # Release the 1st one -> becomes Awaiting Assignment
        ml_svc.resolve_review_item(self.adm, rq_items[0]["id"], "Marked Valid", note="False alarm, genuine user")
        released = self._get_by_inc(rq_items[0]["incident_id"])
        self.assertEqual(released["status"], "Awaiting Assignment")
        self.assertEqual(released["is_spam"], 0)

        # Confirm spam on remaining 3 -> triggers temporary block!
        for r_item in rq_items[1:]:
            ml_svc.resolve_review_item(self.adm, r_item["id"], "Confirmed Spam", note="Repeated gibberish spam")

        with self.assertRaises(PermissionError):
            comp_mod.submit_complaint(
                cit_sp, self.water_id, "Genuine water pipe leak after being blocked.", "Main Road",
                pincode="522002", area_id=1,
            )

        # Admin & Employee CANNOT lift spam block
        for actor in (self.adm, self.emp, cit_sp):
            with self.assertRaises(PermissionError):
                mod_mod.lift_spam_block(actor, cit_sp["id"], "Attempting unauthorized unblock")

        # Super Admin lifts block (requires reason >= 5 chars)
        with self.assertRaises(ValueError):
            mod_mod.lift_spam_block(self.sa, cit_sp["id"], "ok")
        mod_mod.lift_spam_block(self.sa, cit_sp["id"], "User apologized and verified real grievance")

        # User can now submit again + audit logged
        inc_after = comp_mod.submit_complaint(
            cit_sp, self.water_id, "Genuine water supply leakage near temple road.", "Temple Road",
            pincode="522002", area_id=1,
        )
        self.assertIsNotNone(self._get_by_inc(inc_after))
        logs = mod_mod.get_moderation_audit_logs(self.sa)
        self.assertTrue(
            any(log_entry["action"] == "lift_spam_block" and log_entry["target_user_id"] == cit_sp["id"] for log_entry in logs)
        )

        # Captcha challenge helper check
        q, ans = mod_mod.generate_captcha_challenge()
        self.assertIn("?", q)
        self.assertTrue(mod_mod.verify_captcha_answer(ans, str(ans)))
        self.assertFalse(mod_mod.verify_captcha_answer(ans, str(ans + 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
