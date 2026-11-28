"""
tests/test_ml.py
Phase 1 tests: ML classifier inference, emergency keyword override, LIME/term
explainability, versioned artifacts & non-auto-activation, Review Queue permissions
and resolution audit trail, and offline fallback when no artifact exists.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("CGS_SEED_PASSWORD", "SeedTest@123")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestMLPhase1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_root = tempfile.mkdtemp(prefix="cgs_ml_test_")
        cls._models_dir = os.path.join(cls._tmp_root, "models")
        os.environ["CGS_DB_PATH"] = os.path.join(cls._tmp_root, "test_ml.db")
        os.environ["CGS_UPLOADS_DIR"] = os.path.join(cls._tmp_root, "uploads")
        os.environ["ML_MODELS_DIR"] = cls._models_dir
        os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"

        import core.auth as auth_mod
        import core.database as db_mod
        import core.departments as dept_mod
        from ml.classifier import reload_classifier
        from ml.trainer import train_and_evaluate

        db_mod.init_db()
        db_mod.seed_demo_accounts()
        with db_mod.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active, allow_self_claim) VALUES (1, 1, 'Guntur', 1, 0)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (1, 1, 'Arundelpet S.O', 16.3067, 80.4365, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.commit()
        cls.auth = auth_mod
        cls.dept_mod = dept_mod

        cls.super_admin = cls.auth.login("superadmin", "SeedTest@123")
        cls.auth.change_own_password(cls.super_admin, "SeedTest@123", "SuperPass@123")
        cls.super_admin = cls.auth.login("superadmin", "SuperPass@123")

        cls.admin = cls.auth.login("admin", "SeedTest@123")
        cls.auth.change_own_password(cls.admin, "SeedTest@123", "AdminPass@123")
        cls.admin = cls.auth.login("admin", "AdminPass@123")
        cls.auth.update_admin_jurisdiction(cls.super_admin, cls.admin["id"], [1])
        cls.admin = cls.auth.login("admin", "AdminPass@123")

        cls.employee = cls.auth.login("employee", "SeedTest@123")
        cls.auth.change_own_password(cls.employee, "SeedTest@123", "EmpPass@123")
        cls.employee = cls.auth.login("employee", "EmpPass@123")

        cls.citizen = cls.auth.register("ml_citizen", "ML Citizen", "Citizen@123")
        depts = cls.dept_mod.get_all_departments(cls.super_admin)
        cls.depts = {d["name"]: d for d in depts}

        import pandas as pd

        # Create a representative fast subset CSV in _tmp_root for rapid CI unit testing
        full_df = pd.read_csv(PROJECT_ROOT / "data" / "synthetic_complaints.csv")
        cls._test_csv = os.path.join(cls._tmp_root, "sample_complaints.csv")
        sample_df = (
            full_df.groupby("department", group_keys=False)
            .head(80)
            .reset_index(drop=True)
        )
        sample_df.to_csv(cls._test_csv, index=False)

        # Train initial model in isolated test ML_MODELS_DIR using CV=2 for test speed
        cls.initial_meta = train_and_evaluate(
            csv_path=cls._test_csv,
            models_dir=cls._models_dir,
            charts_dir=os.path.join(cls._tmp_root, "charts"),
            activate_if_none=True,
            cv_splits=2,
        )
        reload_classifier()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("ML_MODELS_DIR", None)
        from ml.classifier import reload_classifier
        reload_classifier()
        shutil.rmtree(cls._tmp_root, ignore_errors=True)

    def test_01_model_comparison_stratified_group_artifacts_and_no_auto_activate(self):
        self.assertIn("department", self.initial_meta)
        self.assertIn("priority", self.initial_meta)
        self.assertEqual(len(self.initial_meta["department"]["model_comparison"]), 4)
        first_row = self.initial_meta["department"]["model_comparison"][0]
        for k in ("test_accuracy", "test_macro_precision", "test_macro_recall", "test_macro_f1", "train_time_sec", "inference_time_ms"):
            self.assertIn(k, first_row)
        self.assertGreaterEqual(len(self.initial_meta["department"]["ablation"]), 8)
        self.assertTrue(Path(self.initial_meta["artifact_path"]).exists())
        self.assertTrue(Path(self.initial_meta["metadata_path"]).exists())
        self.assertTrue(self.initial_meta["activated"])

        # Retraining as super_admin saves a NEW version without auto-activating it
        import core.ml_service as ml_svc

        models_before = ml_svc.list_ml_models(self.super_admin)
        active_before = next(m for m in models_before if m["is_active"] == 1)

        with self.assertRaises(PermissionError):
            ml_svc.retrain_ml_models(self.admin)
        with self.assertRaises(PermissionError):
            ml_svc.activate_ml_model(self.employee, active_before["version"])

        from ml.trainer import train_and_evaluate

        candidate_meta = train_and_evaluate(
            csv_path=self._test_csv,
            models_dir=self._models_dir,
            charts_dir=os.path.join(self._tmp_root, "charts"),
            activate_if_none=False,
            cv_splits=2,
        )
        self.assertFalse(candidate_meta["activated"])
        ml_svc.sync_ml_models_registry()
        models_after = ml_svc.list_ml_models(self.super_admin)
        active_after = next(m for m in models_after if m["is_active"] == 1)
        self.assertEqual(active_after["version"], active_before["version"])

        # Manual activation switches the active pointer
        ml_svc.activate_ml_model(self.super_admin, candidate_meta["version"])
        models_switched = ml_svc.list_ml_models(self.super_admin)
        active_switched = next(m for m in models_switched if m["is_active"] == 1)
        self.assertEqual(active_switched["version"], candidate_meta["version"])

    def test_02_classifier_prediction_emergency_override_and_explainability(self):
        from ml.classifier import explain_with_lime, predict_complaint

        pred_water = predict_complaint(
            "Main drinking water pipeline leaking near Jubilee Hills bus stop since 4 days"
        )
        self.assertEqual(pred_water["department"], "Water")
        self.assertGreater(pred_water["confidence"], 0.4)
        self.assertIn("top_terms", pred_water)
        self.assertGreater(len(pred_water["top_terms"]), 0)

        # Emergency keyword must force High priority regardless of base text phrasing
        pred_emg = predict_complaint(
            "Minor spark and electrocution fire hazard near school gate wire hanging"
        )
        self.assertEqual(pred_emg["priority"], "High")
        self.assertTrue(pred_emg["emergency_override"])

        # LIME returns attribution entries
        lime_weights = explain_with_lime(
            "Broken pothole road near Madhapur junction damaging bikes",
            num_features=5,
        )
        self.assertIsInstance(lime_weights, list)
        self.assertGreater(len(lime_weights), 0)

    def test_03_citizen_choice_preserved_mismatch_queued_and_admin_resolves(self):
        import core.complaints as comp_mod
        import core.ml_service as ml_svc

        # Citizen intentionally chooses "Traffic" for a clear "Water" pipeline issue -> non-blocking!
        incident_id = comp_mod.submit_complaint(
            self.citizen,
            self.depts["Traffic"]["id"],
            "No drinking water supply and burst water pipeline flooding street near Banjara Hills",
            "Banjara Hills Road 12",
            pincode="522002",
            area_id=1,
        )
        complaints = comp_mod.get_complaints(self.citizen)
        saved = next(c for c in complaints if c["incident_id"] == incident_id)
        self.assertEqual(saved["department"], "Traffic")  # citizen choice stands!
        self.assertEqual(saved["ml_suggested_department"], "Water")

        # Review Queue permissions & resolution
        with self.assertRaises(PermissionError):
            ml_svc.get_review_queue(self.citizen)
        with self.assertRaises(PermissionError):
            ml_svc.get_review_queue(self.employee)

        pending = ml_svc.get_review_queue(self.admin, status_filter="Pending")
        item = next((r for r in pending if r["incident_id"] == incident_id), None)
        self.assertIsNotNone(item)
        self.assertIn("Citizen-ML department mismatch", item["reason"])

        with self.assertRaises(PermissionError):
            ml_svc.resolve_review_item(self.employee, item["id"], "Accept ML")

        ml_svc.resolve_review_item(self.admin, item["id"], "Accept ML", note="Verified water leak")
        updated = comp_mod.get_complaint_detail(self.admin, saved["id"])
        self.assertEqual(updated["department"], "Water")

        history = comp_mod.get_complaint_history(self.admin, saved["id"])
        self.assertTrue(
            any(h["field_name"] == "department" and h["new_value"] == "Water" for h in history)
        )

    def test_04_offline_rule_fallback_when_no_model_artifact(self):
        from ml.classifier import HybridMLClassifier

        empty_dir = Path(self._tmp_root) / "empty_models"
        empty_dir.mkdir(parents=True, exist_ok=True)
        fallback = HybridMLClassifier(models_dir=empty_dir)
        pred = fallback.predict("Fire near transformer spark emergency on main road")
        self.assertEqual(pred["model_version"], "rule-fallback")
        self.assertEqual(pred["priority"], "High")
        self.assertTrue(pred["low_confidence"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
