"""
tests/test_phase2_analytics.py
Phase 2 test suite:
  - 4-week volume forecast + rolling-origin backtest + baseline comparison + scope enforcement
  - Resolution-time HistGradientBoostingRegressor vs Mean Baseline (MAE & RMSE) + fallback
  - Rolling-window anomaly detector firing on injected spike (District Admin + Super Admin alerts) and silent on normal data
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import shutil
import tempfile
import unittest

import analytics.anomaly as anom_mod
import analytics.forecast as fc_mod
import analytics.resolution_predictor as res_mod
import core.auth as auth_mod
import core.complaints as comp_mod
import core.database as db_mod
import core.departments as dept_mod
import scripts.inject_spike as spike_mod

_SAVED_ENV: dict[str, str | None] = {}
_TMP_DIR = ""


def setUpModule():
    global _TMP_DIR
    for k in (
        "CGS_DB_PATH",
        "CGS_UPLOADS_DIR",
        "CGS_ML_ACTIVE_MODEL_DIR",
        "CGS_SEED_PASSWORD",
        "CGS_SUPERADMIN_PASSWORD",
        "CGS_ADMIN_PASSWORD",
        "CGS_EMPLOYEE_PASSWORD",
    ):
        _SAVED_ENV[k] = os.environ.get(k)

    _TMP_DIR = tempfile.mkdtemp(prefix="cgs_phase2_test_")
    os.environ["CGS_DB_PATH"] = os.path.join(_TMP_DIR, "test_p2.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(_TMP_DIR, "uploads")
    os.environ["CGS_ML_ACTIVE_MODEL_DIR"] = os.path.join(_TMP_DIR, "models")
    os.environ["CGS_SEED_PASSWORD"] = "SeedTest@123"
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
    res_mod._CACHED_BUNDLE = None
    if _TMP_DIR:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)


class TestPhase2ForecastPredictionAnomaly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db_mod.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (1, 1, 'Guntur', 1)")
            conn.execute("INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (2, 1, 'Visakhapatnam', 1)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (1, 1, 'Arundelpet', 16.3067, 80.4365, 1)")
            conn.execute("INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) VALUES (2, 2, 'MVP Colony', 17.7380, 83.3320, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('522002', 1, 1, 1)")
            conn.execute("INSERT OR IGNORE INTO pincodes (pincode, area_id, district_id, state_id) VALUES ('530001', 2, 2, 1)")
            conn.commit()

        cls.sa = auth_mod.login("superadmin", "SeedTest@123")
        assert cls.sa is not None
        depts = {d["name"]: d["id"] for d in dept_mod.get_active_departments()}
        cls.water_id = depts["Water"]

        cls.adm_gnt = auth_mod.create_admin_account(cls.sa, "p2_adm_gnt", "Gnt Admin", "Pass@1234", [1])
        cls.emp_w = auth_mod.create_user_account(
            cls.sa, "p2_emp_w", "Water Emp", "Pass@1234", "employee",
            department_id=cls.water_id, district_id=1,
        )
        cls.citizen = auth_mod.register("p2_cit", "P2 Citizen", "Pass@1234")

    def test_01_volume_forecast_shape_baseline_and_scope(self):
        # Super Admin statewide 4-week forecast
        res_sa = fc_mod.get_scoped_volume_forecast(self.sa, department_name="Water", horizon=4)
        self.assertEqual(len(res_sa["forecast"]), 4)
        self.assertIn("Seasonal-Naive Baseline", res_sa["backtest_metrics"])
        self.assertIn("SARIMA(1,0,1)(1,0,0,4)", res_sa["backtest_metrics"])
        for row in res_sa["forecast"]:
            self.assertLessEqual(row["lower_ci"], row["predicted_volume"])
            self.assertLessEqual(row["predicted_volume"], row["upper_ci"])
            self.assertGreaterEqual(row["suggested_handlers"], 1)

        # Employee scope ignores department override & stays strictly on Water
        res_emp = fc_mod.get_scoped_volume_forecast(self.emp_w, department_name="Traffic", horizon=4)
        self.assertEqual(res_emp["scope"]["department"], "Water")

        # Admin outside assigned district gets PermissionError
        with self.assertRaises(PermissionError):
            fc_mod.get_scoped_volume_forecast(self.adm_gnt, district_id=2)

        # Citizen lacks view_analytics
        with self.assertRaises(PermissionError):
            fc_mod.get_scoped_volume_forecast(self.citizen)

    def test_02_resolution_time_model_beats_baseline_and_fallback(self):
        # Offline fallback works even before training
        res_mod._CACHED_BUNDLE = None
        fb = res_mod.predict_resolution_hours("Water", "High", district_name="Guntur", text="Burst pipe")
        self.assertTrue(fb["is_estimate"])
        self.assertEqual(fb["method"], "Department-Priority Baseline")

        meta = res_mod.train_resolution_time_model(out_dir=os.environ["CGS_ML_ACTIVE_MODEL_DIR"])
        self.assertTrue(meta["beats_baseline"])
        self.assertLess(meta["model_metrics"]["mae_hours"], meta["baseline_metrics"]["mae_hours"])
        self.assertLess(meta["model_metrics"]["rmse_hours"], meta["baseline_metrics"]["rmse_hours"])

        pred = res_mod.predict_resolution_hours("Water", "High", district_name="Guntur", text="Burst water pipe repair")
        self.assertTrue(pred["is_estimate"])
        self.assertEqual(pred["method"], "HistGradientBoostingRegressor")
        self.assertGreater(pred["estimated_hours"], 0.0)

        # Submitting & reading detail surfaces the estimate cleanly
        inc = comp_mod.submit_complaint(
            self.citizen, self.water_id, "Pipeline leak near Arundelpet junction.", "Junction",
            pincode="522002", area_id=1,
        )
        c = next(x for x in comp_mod.get_complaints(self.citizen) if x["incident_id"] == inc)
        detail = comp_mod.get_complaint_detail(self.citizen, c["id"])
        self.assertIn("estimated_resolution", detail)
        self.assertTrue(detail["estimated_resolution"]["is_estimate"])

    def test_03_anomaly_normal_silent_then_fires_on_spike_and_cleans_up(self):
        # Single complaint above does NOT fire spike anomaly
        now_normal = datetime.now(timezone.utc) - timedelta(days=3)
        normal_alerts = anom_mod.detect_anomalies(now=now_normal, notify=False)
        self.assertEqual(normal_alerts, [])

        # Inject clearly labeled [DEMO SPIKE] rows -> triggers alert for District Admin + Super Admin
        res = spike_mod.inject_demo_spike(count=8, department_name="Water", district_name="Guntur")
        self.assertEqual(res["inserted"], 8)
        self.assertGreaterEqual(res["alerts_triggered"], 1)

        sa_alerts = anom_mod.get_anomaly_alerts(self.sa, unread_only=True)
        adm_alerts = anom_mod.get_anomaly_alerts(self.adm_gnt, unread_only=True)
        self.assertTrue(any("[DEMO SPIKE]" in a["title"] for a in sa_alerts))
        self.assertTrue(any("[DEMO SPIKE]" in a["title"] for a in adm_alerts))

        # Cleanup removes synthetic spike complaints & notifications cleanly
        removed = spike_mod.remove_demo_spikes()
        self.assertEqual(removed, 8)
        self.assertEqual(anom_mod.get_anomaly_alerts(self.sa, unread_only=True), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
