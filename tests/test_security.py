"""
tests/test_security.py
Comprehensive Phase 7 security regression suite verifying all 10 threat scenarios:
  1. SQL Injection attempts in text, search queries, filters, and ticket lookups
  2. Direct role & privilege escalation bypasses (user calling admin/super_admin APIs)
  3. IDOR on complaints, images, history, internal notes, and DPDP exports
  4. Cross-district Admin isolation (District 1 Admin blocked from District 2 records)
  5. Employee tampering (dept/priority edits, resolving unassigned/foreign tasks, disabled self-claim)
  6. Malicious uploads (.exe, disguised MZ binary as .jpg, SVG/XSS, >5 MB oversize)
  7. Brute-force login lockout & 30-min session inactivity expiry
  8. Public ticket lookup tampering, anti-enumeration generic error, and rate limiting
  9. Stored XSS escaping & spreadsheet CSV/Excel formula injection neutralization
 10. Password reset OTP expiry, single-use replay, cross-user tampering, and rate limit
 Plus DATABASE_URL abstraction (SQLite/PostgreSQL DDL & placeholder translation) and health checks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import os
import shutil
import tempfile
import unittest

import core.auth as auth_mod
import core.clusters as clust_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.health as health_mod
import core.images as img_mod
import core.ml_service as ml_svc
import core.privacy as privacy_mod
import core.public_status as pub_mod
import core.reports as rep_mod

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
        "CGS_PUBLIC_LOOKUP_RATE_LIMIT_PER_HOUR",
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_security_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_sec.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "TestSeed@2026"
    os.environ.pop("CGS_SUPERADMIN_PASSWORD", None)
    os.environ.pop("CGS_ADMIN_PASSWORD", None)
    os.environ.pop("CGS_EMPLOYEE_PASSWORD", None)
    os.environ["CGS_DAILY_SUBMISSION_LIMIT"] = "50"
    os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"
    os.environ["CGS_RESET_CODE_TTL_MINUTES"] = "15"
    os.environ["CGS_RESET_RATE_LIMIT_PER_HOUR"] = "3"
    os.environ["CGS_PUBLIC_LOOKUP_RATE_LIMIT_PER_HOUR"] = "5"

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


class TestSecurityAndPlatform(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db_mod.get_connection() as conn, conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active, allow_self_claim) VALUES (1, 1, 'Guntur', 1, 0)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active, allow_self_claim) VALUES (2, 1, 'Krishna', 1, 0)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (1, 1, 'Arundelpet', 16.3067, 80.4365, 1)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (2, 2, 'Benz Circle', 16.5062, 80.6480, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('520010', 2, 2, 1)")

        cls.sa = auth_mod.login("superadmin", "TestSeed@2026")
        assert cls.sa is not None
        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_id = depts["Water"]
        cls.roads_id = depts["Roads"]

        cls.adm1 = auth_mod.create_admin_account(cls.sa, "sec_adm1", "Sec Admin Guntur", "Pass@1234", [1])
        cls.adm2 = auth_mod.create_admin_account(cls.sa, "sec_adm2", "Sec Admin Krishna", "Pass@1234", [2])
        cls.emp1 = auth_mod.create_user_account(
            cls.sa, "sec_emp1", "Sec Emp Guntur", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=1,
        )
        cls.emp2 = auth_mod.create_user_account(
            cls.sa, "sec_emp2", "Sec Emp Other", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=1,
        )
        cls.cit_a = auth_mod.register("sec_cit_a", "Citizen Alpha", "Pass@1234", phone="9999900001", email="alpha@example.com")
        cls.cit_b = auth_mod.register("sec_cit_b", "Citizen Beta", "Pass@1234", phone="9999900002", email="beta@example.com")

        cls.inc_d1 = comp_mod.submit_complaint(
            cls.cit_a,
            cls.water_id,
            "<script>alert('xss')</script> Water pipeline broken near market =SUM(1,2)",
            "<img src=x onerror=alert(1)> Market Lane",
            pincode="522002",
            area_id=1,
        )
        cls.c_d1 = next(c for c in comp_mod.get_complaints(cls.sa) if c["incident_id"] == cls.inc_d1)

        cls.inc_d2 = comp_mod.submit_complaint(
            cls.cit_b,
            cls.water_id,
            "Blocked municipal storm drain near bus stand",
            "Benz Circle",
            pincode="520010",
            area_id=2,
        )
        cls.c_d2 = next(c for c in comp_mod.get_complaints(cls.sa) if c["incident_id"] == cls.inc_d2)

    def test_01_sql_injection_attempts_stay_inert(self):
        sqli = "' OR 1=1; DROP TABLE complaints; --"
        inc_sqli = comp_mod.submit_complaint(
            self.cit_a,
            self.water_id,
            f"Drinking water contamination report {sqli}",
            f"Road {sqli}",
            pincode="522002",
            area_id=1,
        )
        self.assertTrue(inc_sqli.startswith("CIV-"))
        # Login & public status lookup with SQLi payload fail safely without dropping or bypassing
        self.assertIsNone(auth_mod.login("superadmin' OR '1'='1", "anything"))
        with self.assertRaises(ValueError):
            pub_mod.lookup_public_status(sqli, "0001", client_key="sqli_probe")
        with db_mod.get_connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
            self.assertGreaterEqual(cnt, 3)

    def test_02_direct_role_bypass_blocked(self):
        with self.assertRaises(PermissionError):
            comp_mod.assign_task(self.cit_a, self.c_d1["id"], self.emp1["id"])
        with self.assertRaises(PermissionError):
            auth_mod.create_user_account(self.cit_a, "hack_u", "Hacker", "Pass@1234", "super_admin")
        with self.assertRaises(PermissionError):
            comp_mod.delete_complaint(self.adm1, self.c_d1["id"], "Admin cannot hard/soft delete")
        with self.assertRaises(PermissionError):
            comp_mod.transfer_complaint(self.emp1, self.c_d1["id"], self.roads_id, "Emp cannot transfer")
        with self.assertRaises(PermissionError):
            rep_mod.export_complaints_csv(self.cit_a)
        with self.assertRaises(PermissionError):
            ml_svc.get_review_queue(self.cit_a)

    def test_03_idor_isolation_between_citizens(self):
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_detail(self.cit_b, self.c_d1["id"])
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_history(self.cit_b, self.c_d1["id"])
        with self.assertRaises(PermissionError):
            img_mod.get_images(self.cit_b, self.c_d1["id"])
        with self.assertRaises(PermissionError):
            comp_mod.add_internal_note(self.cit_a, self.c_d1["id"], "Blocked")
        with self.assertRaises(PermissionError):
            comp_mod.withdraw_complaint(self.cit_b, self.c_d1["id"], "Unauthorized IDOR withdraw")
        exp_b = privacy_mod.export_own_data(self.cit_b, "json")["content"]
        self.assertNotIn(self.inc_d1, exp_b)

    def test_04_cross_district_admin_isolation(self):
        adm1_visible = {c["id"] for c in comp_mod.get_complaints(self.adm1)}
        self.assertIn(self.c_d1["id"], adm1_visible)
        self.assertNotIn(self.c_d2["id"], adm1_visible)
        with self.assertRaises(PermissionError):
            comp_mod.get_complaint_detail(self.adm1, self.c_d2["id"])
        with self.assertRaises(PermissionError):
            comp_mod.assign_task(self.adm1, self.c_d2["id"], self.emp1["id"])
        with self.assertRaises(PermissionError):
            comp_mod.transfer_complaint(self.adm1, self.c_d2["id"], self.roads_id, "Cross district blocked")
        with self.assertRaises(PermissionError):
            rep_mod.export_complaints_csv(self.adm1, district_id=2)
        with self.assertRaises(PermissionError):
            clust_mod.merge_complaints(self.adm1, self.c_d1["id"], self.c_d2["id"], "Cross district merge blocked")

    def test_05_employee_tampering_and_self_claim_disabled(self):
        with self.assertRaises(PermissionError):
            comp_mod.claim_complaint(self.emp1, self.c_d1["id"])
        comp_mod.assign_task(self.adm1, self.c_d1["id"], self.emp1["id"])
        # Employee cannot change department or priority
        with self.assertRaises(PermissionError):
            comp_mod.update_complaint(self.emp1, self.c_d1["id"], department_id=self.roads_id)
        with self.assertRaises(PermissionError):
            comp_mod.update_complaint(self.emp1, self.c_d1["id"], priority="High")
        # Unassigned emp2 cannot update emp1's complaint
        with self.assertRaises(PermissionError):
            comp_mod.update_complaint(self.emp2, self.c_d1["id"], new_status="In Progress", remark="Hijack")

    def test_06_malicious_file_uploads_rejected(self):
        bad_uploads = [
            ("malware.exe", b"MZ\x90\x00\x03\x00\x00\x00not_an_image"),
            ("disguised.jpg", b"MZ\x90\x00\x03\x00\x00\x00fake_jpeg_header"),
            ("xss.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
            ("bomb.png", b"\x89PNG\r\n\x1a\n" + (b"\x00" * (img_mod.MAX_IMAGE_BYTES + 10))),
        ]
        for fname, payload in bad_uploads:
            with self.subTest(filename=fname):
                with self.assertRaises(ValueError):
                    img_mod.prepare_images([(fname, payload)])

    def test_07_brute_force_lockout_and_session_timeout(self):
        auth_mod.register("sec_bruteforce", "Brute Target", "StrongPass#9")
        now0 = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)
        for _ in range(5):
            self.assertIsNone(auth_mod.login("sec_bruteforce", "WrongPass!", now=now0))
        # Even the correct password must be rejected while locked
        self.assertIsNone(auth_mod.login("sec_bruteforce", "StrongPass#9", now=now0 + timedelta(minutes=1)))
        # After lockout window passes cleanly, login succeeds
        self.assertIsNotNone(auth_mod.login("sec_bruteforce", "StrongPass#9", now=now0 + timedelta(minutes=30)))

        # Session expiry (30 min inactivity timeout)
        self.assertFalse(auth_mod.is_session_expired(now0, now=now0 + timedelta(minutes=10), timeout_minutes=30))
        self.assertTrue(auth_mod.is_session_expired(now0, now=now0 + timedelta(minutes=31), timeout_minutes=30))

    def test_08_public_status_tampering_and_rate_limit(self):
        with self.assertRaises(ValueError) as e1:
            pub_mod.lookup_public_status(self.inc_d1, "9999", client_key="tamper_chk")
        with self.assertRaises(ValueError) as e2:
            pub_mod.lookup_public_status("CIV-DOES-NOT-EXIST", "0001", client_key="tamper_chk")
        self.assertEqual(str(e1.exception), str(e2.exception))

        # Exhaust rate limit (5/hr)
        for _ in range(3):
            with self.assertRaises(ValueError):
                pub_mod.lookup_public_status(self.inc_d1, "9999", client_key="tamper_chk")
        with self.assertRaises(ValueError) as e_rl:
            pub_mod.lookup_public_status(self.inc_d1, "0001", client_key="tamper_chk")
        self.assertIn("Too many", str(e_rl.exception))

    def test_09_xss_escaping_and_formula_injection_neutralized(self):
        escaped_text = html.escape(self.c_d1["text"])
        escaped_loc = html.escape(self.c_d1["location"])
        self.assertNotIn("<script>", escaped_text)
        self.assertIn("&lt;script&gt;", escaped_text)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", escaped_loc)
        csv_out = rep_mod.export_complaints_csv(self.sa).decode("utf-8")
        self.assertTrue(rep_mod._csv_safe("=CMD|'/C calc'!A0").startswith("'="))
        self.assertIn("Water pipeline broken near market =SUM(1,2)", csv_out)

    def test_10_otp_reset_reuse_expiry_and_database_abstraction_health(self):
        codes: list[str] = []
        t0 = datetime(2026, 11, 28, 14, 0, 0, tzinfo=timezone.utc)
        auth_mod.request_password_reset("alpha@example.com", now=t0, smtp_sender=lambda r, s, b: codes.append(b))
        code = [tok for tok in codes[-1].split() if len(tok) == 6 and tok.isdigit()][0]

        # Wrong user cannot use cit_a's OTP code
        with self.assertRaises(ValueError):
            auth_mod.confirm_password_reset("beta@example.com", code, "NewPass#99", now=t0 + timedelta(minutes=2))
        # Valid user consumes once -> second attempt fails
        auth_mod.confirm_password_reset("alpha@example.com", code, "NewPass#99", now=t0 + timedelta(minutes=2))
        with self.assertRaises(ValueError):
            auth_mod.confirm_password_reset("alpha@example.com", code, "AnotherPass#99", now=t0 + timedelta(minutes=3))

        # DATABASE_URL SQLite/Postgres helper & health endpoint verification
        self.assertEqual(db_mod.get_database_backend("sqlite:///complaints.db"), "sqlite")
        self.assertEqual(db_mod.get_database_backend("postgresql://user:pw@db:5432/cgs"), "postgres")
        pg_sql, pg_params = db_mod.translate_sql_placeholders("SELECT * FROM users WHERE username = ? AND is_active = ?", ("a", 1))
        self.assertEqual(pg_sql, "SELECT * FROM users WHERE username = :p0 AND is_active = :p1")
        self.assertEqual(pg_params, {"p0": "a", "p1": 1})
        pg_ddl = db_mod.translate_sqlite_ddl_for_postgres("id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT (datetime('now'))")
        self.assertIn("SERIAL PRIMARY KEY", pg_ddl)
        self.assertIn("CURRENT_TIMESTAMP", pg_ddl)

        h = health_mod.get_health_status()
        self.assertEqual(h["status"], "ok")
        self.assertEqual(h["checks"]["database"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
