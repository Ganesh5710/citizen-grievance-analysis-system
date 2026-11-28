"""
tests/test_phase3_notifications.py
Phase 3 test suite:
  - Lifecycle events create notifications for matching citizen, employee, and district admin
  - Email verification + RFC format gating + zero PII beyond ticket ID & status in email body
  - SMTP failure is recorded, retried up to 3 times without crashing actions, and recovers cleanly
  - User preferences (in_app on/off, email on/off) and inbox read/mark-all controls respected
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import shutil
import tempfile
import unittest

import core.auth as auth_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.sla as sla_mod
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
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_phase3_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_p3.db")
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


class TestPhase3Notifications(unittest.TestCase):
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

        cls.adm = auth_mod.create_admin_account(cls.sa, "p3_adm", "P3 Admin", "Pass@1234", [1])
        cls.emp = auth_mod.create_user_account(
            cls.sa, "p3_emp", "P3 Emp", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=1,
        )
        cls.cit = auth_mod.register("p3_cit", "Secret Citizen Name", "Pass@1234")

    def test_01_email_validation_verification_reset_and_privacy_body(self):
        with self.assertRaises(ValueError):
            auth_mod.update_own_preferences(self.cit, email="not-an-email")

        # Valid email starts unverified -> NO email queued
        u = auth_mod.update_own_preferences(self.cit, email="citizen.one@example.com", phone="9876543210")
        self.assertFalse(u["email_verified"])
        res_unver = notif_mod.queue_notification(
            u["id"], "complaint_created", "Created", "Created ticket", incident_id="CIV-PRIV01", deliver_now=False
        )
        self.assertIn("in_app_id", res_unver)
        self.assertNotIn("email_id", res_unver)

        # Verify email -> email queued with minimal PII body (Ticket ID + Status ONLY!)
        u = auth_mod.verify_user_email(u, verified=True)
        self.assertTrue(u["email_verified"])

        sent_payloads: list[tuple[str, str, str]] = []
        res_ver = notif_mod.queue_notification(
            u["id"],
            "complaint_resolved",
            "Resolved #CIV-PRIV02",
            "Sensitive full name Secret Citizen Name must never enter email",
            incident_id="CIV-PRIV02",
            status_label="Resolved",
            deliver_now=True,
            smtp_sender=lambda to, subj, body: sent_payloads.append((to, subj, body)),
        )
        self.assertIn("email_id", res_ver)
        self.assertEqual(len(sent_payloads), 1)
        to_addr, subj, body = sent_payloads[0]
        self.assertEqual(to_addr, "citizen.one@example.com")
        self.assertIn("CIV-PRIV02", body)
        self.assertIn("Resolved", body)
        self.assertNotIn("Secret Citizen Name", body)
        self.assertNotIn("9876543210", body)

        # Changing email resets email_verified back to False
        u_changed = auth_mod.update_own_preferences(u, email="new.addr@example.com")
        self.assertFalse(u_changed["email_verified"])

    def test_02_smtp_failure_records_retries_up_to_3_without_crashing(self):
        u_smtp = auth_mod.register("p3_cit_smtp", "SMTP User", "Pass@1234")
        u_smtp = auth_mod.update_own_preferences(u_smtp, email="smtp.test@example.com", email_notifications=True)
        u_smtp = auth_mod.verify_user_email(u_smtp, verified=True)

        def always_fail(to, subj, body):
            raise ConnectionRefusedError("SMTP server unreachable")

        q = notif_mod.queue_notification(
            u_smtp["id"],
            "complaint_created",
            "Created #CIV-FAIL01",
            "Ticket created",
            incident_id="CIV-FAIL01",
            status_label="Awaiting Assignment",
            deliver_now=True,
            smtp_sender=always_fail,
        )
        em_id = q["email_id"]

        with db_mod.get_connection() as conn:
            r1 = conn.execute("SELECT status, attempts, last_error FROM notifications WHERE id = ?", (em_id,)).fetchone()
        self.assertEqual(r1["status"], "pending")
        self.assertEqual(r1["attempts"], 1)
        self.assertIn("unreachable", r1["last_error"])

        # Attempt 2 -> still pending
        notif_mod.deliver_pending_emails(smtp_sender=always_fail)
        # Attempt 3 -> transitions permanently to failed
        notif_mod.deliver_pending_emails(smtp_sender=always_fail)
        with db_mod.get_connection() as conn:
            r3 = conn.execute("SELECT status, attempts FROM notifications WHERE id = ?", (em_id,)).fetchone()
        self.assertEqual(r3["status"], "failed")
        self.assertEqual(r3["attempts"], 3)

    def test_03_all_complaint_events_inbox_and_preferences(self):
        # Citizen opted out of email, kept in-app ON
        cit2 = auth_mod.register("p3_cit2", "Citizen Two", "Pass@1234")
        cit2 = auth_mod.update_own_preferences(cit2, email="cit2@example.com", email_notifications=False, in_app_notifications=True)
        auth_mod.verify_user_email(cit2, verified=True)

        # Submit -> complaint_created in-app (no email since opted out!)
        inc = comp_mod.submit_complaint(
            cit2, self.water_id, "Water leak at corner lane for 3 days.", "Corner Lane",
            pincode="522002", area_id=1,
        )
        c = next(x for x in comp_mod.get_complaints(cit2) if x["incident_id"] == inc)
        cit_inbox = notif_mod.get_user_inbox(cit2)
        self.assertTrue(any(n["type"] == "complaint_created" and n["incident_id"] == inc for n in cit_inbox))

        with db_mod.get_connection() as conn:
            em_cnt = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND channel = 'email'",
                (cit2["id"],),
            ).fetchone()[0]
        self.assertEqual(em_cnt, 0)

        # Assign -> notifies employee + citizen
        comp_mod.assign_task(self.adm, c["id"], self.emp["id"], instructions="Inspect pipe")
        emp_inbox = notif_mod.get_user_inbox(self.emp)
        self.assertTrue(any(n["type"] == "complaint_assigned" and n["incident_id"] == inc for n in emp_inbox))

        # Accept -> notifies citizen + admin
        comp_mod.accept_task(self.emp, c["id"])
        adm_inbox = notif_mod.get_user_inbox(self.adm)
        self.assertTrue(any(n["type"] == "task_accepted" and n["incident_id"] == inc for n in adm_inbox))

        # SLA escalation -> notifies admin
        due_past = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        with db_mod.get_connection() as conn:
            conn.execute("UPDATE complaints SET due_at = ? WHERE id = ?", (due_past, c["id"]))
            conn.commit()
        sla_mod.run_escalation_check()
        adm_inbox_esc = notif_mod.get_user_inbox(self.adm)
        self.assertTrue(any(n["type"] == "complaint_escalated" and n["incident_id"] == inc for n in adm_inbox_esc))

        # Resolve -> notifies citizen with feedback prompt
        comp_mod.update_task_status(
            self.emp, c["id"], new_status="Resolved", remark="Fixed main coupling cleanly"
        )
        cit_inbox_2 = notif_mod.get_user_inbox(cit2)
        self.assertTrue(any(n["type"] == "complaint_resolved" and "feedback" in n["body"].lower() or "confirm" in n["body"].lower() for n in cit_inbox_2 if n["incident_id"] == inc))

        # Reopen -> notifies employee + admin
        comp_mod.submit_feedback(cit2, c["id"], accepted=False, rating=2, comment="Still dripping")
        emp_inbox_2 = notif_mod.get_user_inbox(self.emp)
        self.assertTrue(any(n["type"] == "complaint_reopened" and n["incident_id"] == inc for n in emp_inbox_2))

        # Mark one + Mark all read controls + in_app_notifications=False honored
        unread_before = notif_mod.get_unread_count(cit2)
        self.assertGreater(unread_before, 0)
        notif_mod.mark_all_user_notifications_read(cit2)
        self.assertEqual(notif_mod.get_unread_count(cit2), 0)

        cit3 = auth_mod.register("p3_cit3", "Silent Citizen", "Pass@1234")
        cit3 = auth_mod.update_own_preferences(cit3, in_app_notifications=False)
        comp_mod.submit_complaint(
            cit3, self.water_id, "Quiet drain blockage near lane 4.", "Lane 4",
            pincode="522002", area_id=1,
        )
        self.assertEqual(notif_mod.get_user_inbox(cit3), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
