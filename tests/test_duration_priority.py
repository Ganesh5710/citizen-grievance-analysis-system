"""
tests/test_duration_priority.py
Comprehensive tests for the duration-aware priority engine (Sections 1-8):
  - Gold test set (tests/gold_priority_cases.csv) extraction, mapping, hybrid & dept verification
  - >= 3 cases per language per pattern family (numeric, number words, script digits,
    units/abbreviations, relative phrases, ranges, negative/context, recurring)
  - Precedence rules (emergency > duration > ML > keyword + recurring +1; ML never lowers 1/2)
  - YAML hot-reload config test without code changes & Super Admin audit logging
  - Open complaint priority aging & continuous improvement logs
"""

from __future__ import annotations

import csv
import os
import shutil
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

GOLD_CSV = PROJECT_ROOT / "tests" / "gold_priority_cases.csv"


def _load_gold() -> list[dict]:
    with open(GOLD_CSV, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class TestDurationExtractorAndMapping(unittest.TestCase):
    def test_01_gold_clear_duration_extraction_100_percent(self):
        from nlp.duration import extract_duration

        clear_rows = [r for r in _load_gold() if r["case_type"] == "clear"]
        self.assertGreaterEqual(len(clear_rows), 180)
        failures = []
        for idx, row in enumerate(clear_rows, start=1):
            exp_h = float(row["expected_duration_hours"])
            res = extract_duration(row["text"])
            if res is None or abs(res[0] - exp_h) > 0.15:
                failures.append((idx, row["language"], exp_h, res, row["text"]))
        self.assertEqual(failures, [], f"Clear gold duration failures: {failures}")

    def test_02_gold_pure_duration_to_priority_mapping_100_percent(self):
        from core.priority_rules import map_duration_to_priority

        clear_rows = [r for r in _load_gold() if r["case_type"] == "clear"]
        failures = []
        for row in clear_rows:
            exp_h = float(row["expected_duration_hours"])
            got = map_duration_to_priority(exp_h, department=row["expected_department"])
            if got != row["expected_priority"]:
                failures.append((row["text"], exp_h, row["expected_department"], row["expected_priority"], got))
        self.assertEqual(failures, [], f"Mapping mismatches: {failures}")

    def test_03_gold_negative_recurring_and_ambiguous_cases(self):
        from nlp.duration import analyze_duration_and_recurrence, extract_duration

        rows = _load_gold()
        neg_rows = [r for r in rows if r["case_type"] == "negative"]
        rec_rows = [r for r in rows if r["case_type"] == "recurring"]
        amb_rows = [r for r in rows if r["case_type"] == "ambiguous"]
        self.assertGreaterEqual(len(neg_rows), 30)
        self.assertGreaterEqual(len(rec_rows), 15)
        self.assertGreaterEqual(len(amb_rows), 15)

        for r in neg_rows:
            with self.subTest(neg=r["text"]):
                self.assertIsNone(extract_duration(r["text"]))

        for r in rec_rows:
            with self.subTest(rec=r["text"]):
                info = analyze_duration_and_recurrence(r["text"])
                self.assertTrue(info["recurring"], f"Missed recurring: {r['text']}")

        for r in amb_rows:
            with self.subTest(amb=r["text"]):
                info = analyze_duration_and_recurrence(r["text"])
                self.assertIsNotNone(info["hours"])
                self.assertLess(info["confidence"], 0.70)

    def test_04_patterns_per_language_at_least_three_each(self):
        from nlp.duration import analyze_duration_and_recurrence, extract_duration

        # Numeric & script digits (>= 3 per language: en, te, hi, mixed)
        numeric_cases = {
            "en": [
                ("water missing for last 3 days", 72.0),
                ("garbage uncollected past 2 weeks", 336.0),
                ("pipe leaking 45 minutes outside shop", 0.75),
                ("street dark since 2 months", 1440.0),
            ],
            "te": [
                ("3 రోజులుగా నీళ్లు రావడం లేదు", 72.0),
                ("౩ రోజులుగా కరెంట్ పోయింది", 72.0),
                ("4 గంటలుగా ట్రాఫిక్ ఆగిపోయింది", 4.0),
            ],
            "hi": [
                ("पिछले 3 दिनों से पानी नहीं आ रहा", 72.0),
                ("३ दिनों से सड़क टूटी है", 72.0),
                ("30 मिनट से सिग्नल अटका है", 0.5),
            ],
            "mixed": [
                ("3 rojula nundi water ravatledu", 72.0),
                ("3 din se road blocked hai", 72.0),
                ("10 hrs nundi power cut undi", 10.0),
            ],
        }
        # Number words up to 30 (>= 3 per language)
        word_cases = {
            "en": [
                ("no water for one week", 168.0),
                ("road dug for twenty-four days", 576.0),
                ("park closed thirty days now", 720.0),
                ("no power half a day", 12.0),
            ],
            "te": [
                ("మూడు రోజులుగా నీరు లేదు", 72.0),
                ("ఇరవై రోజులుగా చెత్త ఎత్తలేదు", 480.0),
                ("అర గంటగా వాల్వ్ లీక్", 0.5),
            ],
            "hi": [
                ("तीन दिन से नल सूखा है", 72.0),
                ("बीस दिनों से नाली बंद है", 480.0),
                ("आधा घंटा से बिजली नहीं है", 0.5),
            ],
            "mixed": [
                ("moodu rojulu ga tap water ledu", 72.0),
                ("teen din se signal off hai", 72.0),
                ("okati week se drain choked", 168.0),
            ],
        }
        # Relative phrases & ranges (>= 3 per language)
        phrase_range_cases = {
            "en": [
                ("no water since yesterday", 24.0),
                ("signal dead since morning", 8.0),
                ("broken pipe 2 to 3 days", 72.0),
                ("drain choked 3-4 wks", 672.0),
            ],
            "te": [
                ("నిన్నటి నుండి వీధి దీపం లేదు", 24.0),
                ("గత వారం నుండి సిగ్నల్ పనిచేయలేదు", 168.0),
                ("2 నుండి 3 రోజులు నీరు లేదు", 72.0),
            ],
            "hi": [
                ("कल से पार्क बंद है", 24.0),
                ("पिछले हफ्ते से कचरा पड़ा है", 168.0),
                ("2 से 3 दिन से पानी बंद है", 72.0),
            ],
            "mixed": [
                ("ninna nundi current ledu", 24.0),
                ("gatha vaaram nundi garbage ledu", 168.0),
                ("2 to 3 days se pipe leak", 72.0),
            ],
        }

        for group in (numeric_cases, word_cases, phrase_range_cases):
            for lang, items in group.items():
                self.assertGreaterEqual(len(items), 3)
                for text, exp_h in items:
                    with self.subTest(lang=lang, text=text):
                        got = extract_duration(text)
                        self.assertIsNotNone(got, f"Failed to extract from {text!r}")
                        self.assertAlmostEqual(got[0], exp_h, delta=0.15)

        # Specific context rules
        self.assertIsNone(extract_duration("I paid 3 days salary for water pump"))
        self.assertIsNone(extract_duration("ticket valid for 7 days at counter"))
        self.assertIsNone(extract_duration("please call me after 3 days regarding road"))
        self.assertIsNone(extract_duration("3 days festival near market road"))
        self.assertIsNone(extract_duration("It was fixed 3 days ago but broke again today"))

        called_res = extract_duration("Called the office 3 days ago, no response")
        self.assertIsNotNone(called_res)
        self.assertEqual(called_res[0], 72.0)
        self.assertLess(called_res[2], 0.80)

        multi_res = analyze_duration_and_recurrence("Water supply low 2 days on corner and 10 days in main lane")
        self.assertEqual(multi_res["hours"], 240.0)
        self.assertLess(multi_res["confidence"], 0.55)

    def test_05_priority_precedence_unit_cases_and_monotonicity(self):
        from core.priority_rules import compute_final_priority, map_duration_to_priority
        from ml.classifier import predict_complaint

        # Required explicit unit cases
        p1 = predict_complaint("water problem for last 3 days", department_hint="Water")
        self.assertEqual(p1["priority"], "Medium")

        p2 = predict_complaint("water problem for 1 week", department_hint="Water")
        self.assertEqual(p2["priority"], "High")

        p3 = predict_complaint("no water since yesterday", department_hint="Water")
        self.assertEqual(p3["priority"], "Medium")

        p4 = predict_complaint("fire near transformer for 1 hour", department_hint="Electricity")
        self.assertEqual(p4["priority"], "High")
        self.assertTrue(p4["emergency_override"])

        # Irrelevant duration is NOT treated as 7-day priority
        p5 = compute_final_priority(
            department="Traffic",
            emergency_override=False,
            duration_hours=None,
            ml_priority="Low",
            keyword_priority="Low",
            is_recurring=False,
        )
        self.assertEqual(p5["final_priority"], "Low")

        # Recurring raises one level (Low -> Medium, Medium -> High)
        p6 = compute_final_priority(
            department="Roads",
            emergency_override=False,
            duration_hours=None,
            ml_priority="Low",
            keyword_priority="Low",
            is_recurring=True,
        )
        self.assertEqual(p6["final_priority"], "Medium")

        # ML model can NEVER lower rules 1 (emergency) or 2 (duration)
        r1 = compute_final_priority(
            department="Sanitation",
            emergency_override=True,
            emergency_keyword="fire",
            duration_hours=1.0,
            ml_priority="Low",
            keyword_priority="Low",
        )
        self.assertEqual(r1["final_priority"], "High")

        r2 = compute_final_priority(
            department="Roads",
            emergency_override=False,
            duration_hours=168.0,
            ml_priority="Low",
            keyword_priority="Low",
        )
        self.assertEqual(r2["final_priority"], "High")
        self.assertIsNotNone(map_duration_to_priority(72.0, "Roads"))


class TestPriorityRulesConfigServiceAndAging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_root = tempfile.mkdtemp(prefix="cgs_dur_test_")
        cls._yaml_path = os.path.join(cls._tmp_root, "priority_rules.yaml")
        shutil.copyfile(PROJECT_ROOT / "config" / "priority_rules.yaml", cls._yaml_path)

        os.environ["CGS_DB_PATH"] = os.path.join(cls._tmp_root, "test_dur.db")
        os.environ["CGS_UPLOADS_DIR"] = os.path.join(cls._tmp_root, "uploads")
        os.environ["CGS_PRIORITY_RULES_PATH"] = cls._yaml_path
        os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"
        os.environ["CGS_DAILY_SUBMISSION_LIMIT"] = "50"
        os.environ["CGS_SUBMISSION_COOLDOWN_SECONDS"] = "0"

        import core.auth as auth_mod
        import core.complaints as comp_mod
        import core.database as db_mod
        import core.departments as dept_mod
        import core.ml_service as ml_svc
        import core.priority_rules as pr_mod
        import core.sla as sla_mod

        db_mod.init_db()
        db_mod.seed_demo_accounts()
        with db_mod.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active, allow_self_claim) VALUES (1, 1, 'Guntur', 1, 0)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (1, 1, 'Arundelpet S.O', 16.3067, 80.4365, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.commit()
        cls.auth = auth_mod
        cls.comp = comp_mod
        cls.dept_mod = dept_mod
        cls.ml_svc = ml_svc
        cls.pr = pr_mod
        cls.sla = sla_mod

        sa = cls.auth.login("superadmin", "SeedTest@123")
        cls.superadmin = cls.auth.change_own_password(sa, "SeedTest@123", "SuperPass@123")
        adm = cls.auth.login("admin", "SeedTest@123")
        cls.admin = cls.auth.change_own_password(adm, "SeedTest@123", "AdminPass@123")
        cls.auth.update_admin_jurisdiction(cls.superadmin, cls.admin["id"], [1])
        cls.admin = cls.auth.login("admin", "AdminPass@123")
        cls.citizen = cls.auth.register("dur_citizen", "Dur Citizen", "Pass@1234")
        cls.depts = {d["name"]: d for d in cls.dept_mod.get_active_departments()}

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("CGS_PRIORITY_RULES_PATH", None)
        shutil.rmtree(cls._tmp_root, ignore_errors=True)

    def test_01_yaml_config_change_takes_effect_immediately_and_audits(self):
        # Default: 24h -> Medium for Roads & Water
        self.assertEqual(self.pr.map_duration_to_priority(24.0, "Roads"), "Medium")
        self.assertEqual(self.pr.map_duration_to_priority(24.0, "Water"), "Medium")

        # Non-super_admin cannot modify rules
        with self.assertRaises(PermissionError):
            self.pr.save_priority_rules(self.admin, {"default": {"medium_min_hours": 48, "high_min_hours": 168}})

        # Super Admin sets default medium_min_hours=48 -> 24h ("since yesterday") is now Low by default,
        # while Water keeps its 24h override -> Medium!
        self.pr.save_priority_rules(
            self.superadmin,
            {
                "default": {"medium_min_hours": 48.0, "high_min_hours": 168.0},
                "department_overrides": {
                    "Water": {"medium_min_hours": 24.0, "high_min_hours": 120.0},
                },
                "aging": {"enabled": True, "low_to_medium_days": 3.0, "medium_to_high_days": 7.0},
                "review_confidence_threshold": 0.55,
            },
            note="Raise default medium cutoff to 48h while keeping Water fast",
        )
        self.assertEqual(self.pr.map_duration_to_priority(24.0, "Roads"), "Low")
        self.assertEqual(self.pr.map_duration_to_priority(24.0, "Water"), "Medium")

        audits = self.pr.get_priority_rule_audit_logs(self.superadmin)
        self.assertGreaterEqual(len(audits), 1)
        self.assertEqual(audits[0]["actor_username"], "superadmin")

        # Restore 24h default cleanly
        self.pr.save_priority_rules(
            self.superadmin,
            {
                "default": {"medium_min_hours": 24.0, "high_min_hours": 168.0},
                "department_overrides": {
                    "Water": {"medium_min_hours": 24.0, "high_min_hours": 120.0},
                    "Electricity": {"medium_min_hours": 24.0, "high_min_hours": 120.0},
                },
                "aging": {"enabled": True, "low_to_medium_days": 3.0, "medium_to_high_days": 7.0},
                "review_confidence_threshold": 0.55,
            },
            note="Restore standard thresholds",
        )

    def test_02_complaint_persists_duration_low_conf_review_and_continuous_feedback(self):
        # Ambiguous "for ages" -> low duration confidence -> queued for review & logged
        inc = self.comp.submit_complaint(
            self.citizen,
            self.depts["Sanitation"]["id"],
            "Sewage drain overflowing near market lane for ages causing foul smell",
            "Market Street",
            pincode="522002",
            area_id=1,
        )
        detail = next(c for c in self.comp.get_complaints(self.citizen) if c["incident_id"] == inc)
        self.assertEqual(detail["duration_hours"], 168.0)
        self.assertIn("ages", detail["duration_matched_text"])
        self.assertLess(detail["duration_confidence"], 0.55)
        self.assertIn("duration", detail["priority_reason"].lower())

        pending = self.ml_svc.get_review_queue(self.admin, status_filter="Pending")
        rq_item = next((r for r in pending if r["incident_id"] == inc), None)
        self.assertIsNotNone(rq_item)
        self.assertIn("Low duration confidence", rq_item["reason"])

        dur_logs = self.pr.get_duration_extraction_logs(self.admin)
        self.assertTrue(any(d["incident_id"] == inc and d["status"] == "Low Confidence" for d in dur_logs))

        # Admin resolves review item and corrects priority -> saved in priority_feedback_log
        self.ml_svc.resolve_review_item(
            self.admin, rq_item["id"], "Keep Citizen", note="Verified severe", corrected_priority="Medium"
        )
        fb_logs = self.pr.get_priority_feedback_logs(self.admin)
        self.assertTrue(any(f["incident_id"] == inc and f["corrected_priority"] == "Medium" for f in fb_logs))

        # Unparsed time cue logs as Failed
        inc_fail = self.comp.submit_complaint(
            self.citizen,
            self.depts["Roads"]["id"],
            "Road surface broken forever near old bridge crossing",
            "Old Bridge",
            pincode="522002",
            area_id=1,
        )
        dur_logs_2 = self.pr.get_duration_extraction_logs(self.admin)
        self.assertTrue(any(d["incident_id"] == inc_fail and d["status"] == "Failed" for d in dur_logs_2))

    def test_03_open_priority_aging_writes_system_audit(self):
        inc = self.comp.submit_complaint(
            self.citizen,
            self.depts["General / Other"]["id"],
            "Small paint peeling on public park bench near gate",
            "City Park",
            pincode="522002",
            area_id=1,
        )
        comp = next(c for c in self.comp.get_complaints(self.citizen) if c["incident_id"] == inc)
        # Ensure starts Low if ML/rule produced Medium
        if comp["priority"] != "Low":
            self.comp.update_complaint(self.admin, comp["id"], priority="Low", remark="Reset for aging test")
            comp = self.comp.get_complaint_detail(self.admin, comp["id"])

        created_dt = self.sla.parse_utc(comp["created_at"])

        # After 4 days open -> Low rises to Medium
        res1 = self.sla.run_priority_aging_check(now=created_dt + timedelta(days=4))
        self.assertGreaterEqual(res1["low_to_medium"], 1)
        after_med = self.comp.get_complaint_detail(self.admin, comp["id"])
        self.assertEqual(after_med["priority"], "Medium")

        # After 8 days open -> Medium rises to High
        res2 = self.sla.run_priority_aging_check(now=created_dt + timedelta(days=8))
        self.assertGreaterEqual(res2["medium_to_high"], 1)
        after_high = self.comp.get_complaint_detail(self.admin, comp["id"])
        self.assertEqual(after_high["priority"], "High")

        history = self.comp.get_complaint_history(self.admin, comp["id"])
        self.assertTrue(
            any(
                h["field_name"] == "priority"
                and h["actor_username"] == "system"
                and h["new_value"] == "High"
                for h in history
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
