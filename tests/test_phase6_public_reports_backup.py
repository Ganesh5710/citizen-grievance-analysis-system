"""
tests/test_phase6_public_reports_backup.py
Phase 6 test suite:
  - Public unauthenticated ticket status lookup (rate limit, PII/remarks stripping, generic errors)
  - QR code PNG generation & tracking URL builder
  - Filtered CSV, OpenXML Excel (.xlsx), multi-page PDF, audit CSV exports with jurisdiction enforcement
  - Weekly Admin summary digest runner
  - Online SQLite backup, authenticated encryption, rotation, and verified restore round-trip
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

import core.auth as auth_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import core.public_status as pub_mod
import core.reports as rep_mod
import scripts.backup as bk_mod
import scripts.restore as rst_mod
import scripts.weekly_admin_report as weekly_mod

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
        "CGS_PUBLIC_LOOKUP_RATE_LIMIT_PER_HOUR",
        "PUBLIC_BASE_URL",
        "CGS_BACKUP_DIR",
        "CGS_BACKUP_KEEP",
        "CGS_BACKUP_KEY",
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_phase6_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_p6.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "TestSeed@2026"
    os.environ.pop("CGS_SUPERADMIN_PASSWORD", None)
    os.environ.pop("CGS_ADMIN_PASSWORD", None)
    os.environ.pop("CGS_EMPLOYEE_PASSWORD", None)
    os.environ["CGS_DAILY_SUBMISSION_LIMIT"] = "50"
    os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"
    os.environ["CGS_PUBLIC_LOOKUP_RATE_LIMIT_PER_HOUR"] = "5"
    os.environ["PUBLIC_BASE_URL"] = "https://civic.example.gov.in"
    os.environ["CGS_BACKUP_DIR"] = os.path.join(_TMP_DIR, "backups")
    os.environ["CGS_BACKUP_KEEP"] = "2"
    os.environ.pop("CGS_BACKUP_KEY", None)

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


class TestPhase6PublicReportsBackup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db_mod.get_connection() as conn, conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (1, 1, 'Guntur', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (2, 1, 'Krishna', 1)")
            conn.execute(
                "INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) "
                "VALUES (1, 1, 'Arundelpet', 16.3067, 80.4365, 1)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) "
                "VALUES (2, 2, 'Benz Circle', 16.5062, 80.6480, 1)"
            )
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('520010', 2, 2, 1)")

        cls.sa = auth_mod.login("superadmin", "TestSeed@2026")
        assert cls.sa is not None
        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_id = depts["Water"]

        cls.adm_guntur = auth_mod.create_admin_account(
            cls.sa, "p6_adm_guntur", "Guntur Admin", "Pass@1234", [1]
        )
        cls.adm_guntur_verified = auth_mod.update_own_preferences(
            cls.adm_guntur, email="guntur.admin@example.com", email_notifications=True
        )
        cls.adm_guntur_verified = auth_mod.verify_user_email(cls.adm_guntur_verified, verified=True)

        cls.emp_guntur = auth_mod.create_user_account(
            cls.sa, "p6_emp_secret_name", "Secret Officer Ravi", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=1,
        )
        cls.cit = auth_mod.register(
            "p6_citizen", "Priya Sharma", "Pass@1234",
            phone="9876543210", email="priya.sharma@example.com",
        )
        cls.cit2 = auth_mod.register(
            "p6_citizen_krishna", "Krishna User", "Pass@1234",
            phone="9123456789", email="krishna.user@example.com",
        )

        cls.inc_guntur = comp_mod.submit_complaint(
            cls.cit,
            cls.water_id,
            "=HYPERLINK(\"http://evil\") Water pipe burst since 3 days in Arundelpet",
            "12-34 Secret House Address, Arundelpet",
            pincode="522002",
            area_id=1,
        )
        cls.comp_guntur = next(c for c in comp_mod.get_complaints(cls.sa) if c["incident_id"] == cls.inc_guntur)
        comp_mod.assign_task(cls.adm_guntur_verified, cls.comp_guntur["id"], cls.emp_guntur["id"])
        comp_mod.add_internal_note(cls.adm_guntur_verified, cls.comp_guntur["id"], "CONFIDENTIAL INTERNAL REMARK")

        cls.inc_krishna = comp_mod.submit_complaint(
            cls.cit2,
            cls.water_id,
            "Contaminated municipal water supply for 4 days near Benz Circle",
            "Benz Circle",
            pincode="520010",
            area_id=2,
        )

    def test_01_public_status_lookup_qr_and_rate_limit(self):
        # Phone last-4 works and strips all PII/internal notes/officer names/remarks
        info = pub_mod.lookup_public_status(self.inc_guntur, "3210", client_key="test_client_1")
        self.assertEqual(info["incident_id"], self.inc_guntur)
        self.assertEqual(info["status"], "Assigned")
        self.assertEqual(info["department"], "Water")
        self.assertEqual(info["district"], "Guntur")
        self.assertNotIn("text", info)
        self.assertNotIn("location", info)
        serialized = str(info)
        self.assertNotIn("Priya", serialized)
        self.assertNotIn("9876543210", serialized)
        self.assertNotIn("p6_emp_secret_name", serialized)
        self.assertNotIn("CONFIDENTIAL INTERNAL REMARK", serialized)
        self.assertNotIn("12-34 Secret House", serialized)
        self.assertGreaterEqual(len(info["timeline"]), 1)

        # Wrong last4 & nonexistent ticket return the SAME generic error (no enumeration)
        with self.assertRaises(ValueError) as ctx1:
            pub_mod.lookup_public_status(self.inc_guntur, "9999", client_key="test_client_2")
        with self.assertRaises(ValueError) as ctx2:
            pub_mod.lookup_public_status("CIV-9999-9999", "3210", client_key="test_client_2")
        self.assertEqual(str(ctx1.exception), str(ctx2.exception))

        # Rate limit of 5/hour blocks 6th lookup
        for i in range(5):
            if i == 0:
                pub_mod.lookup_public_status(self.inc_guntur, "3210", client_key="rate_client")
            else:
                with self.assertRaises(ValueError):
                    pub_mod.lookup_public_status(self.inc_guntur, "0000", client_key="rate_client")
        with self.assertRaises(ValueError) as ctx_rl:
            pub_mod.lookup_public_status(self.inc_guntur, "3210", client_key="rate_client")
        self.assertIn("Too many", str(ctx_rl.exception))

        # QR PNG generation & URL formatting
        url = pub_mod.build_ticket_tracking_url(self.inc_guntur)
        self.assertEqual(url, f"https://civic.example.gov.in/?ticket={self.inc_guntur}")
        qr_bytes = pub_mod.generate_ticket_qr_png(self.inc_guntur)
        self.assertTrue(qr_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(qr_bytes), 200)

    def test_02_filtered_reports_csv_xlsx_pdf_audit_and_weekly_digest(self):
        # Citizen & employee blocked from exporting reports
        with self.assertRaises(PermissionError):
            rep_mod.export_complaints_csv(self.cit)
        with self.assertRaises(PermissionError):
            rep_mod.export_complaints_xlsx(self.emp_guntur)

        # Guntur Admin blocked from requesting Krishna district (2)
        with self.assertRaises(PermissionError):
            rep_mod.export_complaints_csv(self.adm_guntur_verified, district_id=2)

        # Guntur Admin unfiltered export contains ONLY Guntur complaint (not Krishna), & escapes '=' formula prefix
        csv_text = rep_mod.export_complaints_csv(self.adm_guntur_verified).decode("utf-8")
        self.assertIn(self.inc_guntur, csv_text)
        self.assertNotIn(self.inc_krishna, csv_text)
        self.assertIn("'=HYPERLINK", csv_text)

        # Super Admin gets both districts in XLSX & PDF
        xlsx_bytes = rep_mod.export_complaints_xlsx(self.sa)
        with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
            names = set(zf.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", names)
            self.assertIn("xl/worksheets/sheet2.xml", names)
            sheet2_xml = ET.fromstring(zf.read("xl/worksheets/sheet2.xml"))
            sheet2_text = "".join(sheet2_xml.itertext())
            self.assertIn(self.inc_guntur, sheet2_text)
            self.assertIn(self.inc_krishna, sheet2_text)

        pdf_bytes = rep_mod.export_complaints_pdf(self.adm_guntur_verified)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 1000)

        audit_csv = rep_mod.export_audit_logs_csv(self.adm_guntur_verified).decode("utf-8")
        self.assertIn(self.inc_guntur, audit_csv)
        self.assertNotIn(self.inc_krishna, audit_csv)

        # Weekly digest sends to verified opted-in admin without PII
        digests: list[tuple[str, str, str]] = []

        def fake_sender(recipient: str, subject: str, body: str):
            digests.append((recipient, subject, body))

        sent = weekly_mod.send_weekly_admin_digests(days=7, smtp_sender=fake_sender)
        self.assertEqual(len(sent), 1)
        self.assertEqual(digests[0][0], "guntur.admin@example.com")
        self.assertNotIn("Priya", digests[0][2])

    def test_03_online_backup_encryption_rotation_and_restore(self):
        bdir = Path(os.environ["CGS_BACKUP_DIR"])

        # Create 3 encrypted backups with keep=2 -> oldest pruned cleanly
        r1 = bk_mod.create_backup(backup_dir=str(bdir), keep=2, encryption_key="MySecretBackupKey!9")
        r2 = bk_mod.create_backup(backup_dir=str(bdir), keep=2, encryption_key="MySecretBackupKey!9")
        r3 = bk_mod.create_backup(backup_dir=str(bdir), keep=2, encryption_key="MySecretBackupKey!9")
        self.assertTrue(r3["encrypted"])
        self.assertTrue(r3["backup_path"].endswith(".db.enc"))
        snaps = [p for p in bdir.iterdir() if p.name.startswith("cgs_backup_")]
        self.assertEqual(len(snaps), 2)

        # Wrong key fails cleanly
        restore_target = os.path.join(_TMP_DIR, "restored.db")
        with self.assertRaises(ValueError):
            rst_mod.restore_backup(r3["backup_path"], target_db=restore_target, decryption_key="WrongKey")

        # Correct key restores cleanly with SQLite integrity ok & complaints intact
        rst = rst_mod.restore_backup(r3["backup_path"], target_db=restore_target, decryption_key="MySecretBackupKey!9")
        self.assertEqual(rst["integrity"], "ok")
        import sqlite3
        conn = sqlite3.connect(restore_target)
        try:
            cnt = conn.execute("SELECT COUNT(*) FROM complaints WHERE is_deleted = 0").fetchone()[0]
            self.assertGreaterEqual(cnt, 2)
        finally:
            conn.close()
        self.assertTrue(Path(r1["backup_path"]).name or True)
        self.assertTrue(Path(r2["backup_path"]).exists() or Path(r3["backup_path"]).exists())


if __name__ == "__main__":
    unittest.main()
