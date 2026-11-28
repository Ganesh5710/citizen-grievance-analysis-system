"""
scripts/evaluate_phase2.py
Train and benchmark Phase 2 models (4-week Volume Forecast backtest + Gradient Boosting Resolution-Time Predictor),
generate charts in docs/evaluation_charts/, and print exact observed metrics.
"""

from __future__ import annotations

from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analytics.forecast import forecast_volume_series, load_weekly_series  # noqa: E402
from analytics.resolution_predictor import train_resolution_time_model  # noqa: E402


def main() -> None:
    charts_dir = PROJECT_ROOT / "docs" / "evaluation_charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Train & benchmark Resolution-Time Regressor
    res_meta = train_resolution_time_model()
    print("=== Resolution Time Predictor ===")
    print(res_meta)

    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    methods = ["Mean Baseline", "HistGradientBoosting"]
    maes = [res_meta["baseline_metrics"]["mae_hours"], res_meta["model_metrics"]["mae_hours"]]
    rmses = [res_meta["baseline_metrics"]["rmse_hours"], res_meta["model_metrics"]["rmse_hours"]]
    x = np.arange(len(methods))
    w = 0.34
    b1 = ax.bar(x - w / 2, maes, w, label="MAE (hours)", color="#2563eb")
    b2 = ax.bar(x + w / 2, rmses, w, label="RMSE (hours)", color="#f59e0b")
    ax.set_xticks(x)
    ax.set_xticklabels(methods)
    ax.set_ylabel("Error (hours)")
    ax.set_title("Resolution-Time Prediction vs Mean Baseline (Held-Out Test)")
    ax.legend()
    for bars in (b1, b2):
        for bar in bars:
            h = bar.get_height()
            ax.annotate(f"{h:.2f}h", (bar.get_x() + bar.get_width() / 2, h), ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(charts_dir / "resolution_time_eval.png", dpi=150)
    plt.close(fig)

    # 2. Evaluate Weekly Volume Forecasting across departments
    print("\n=== Volume Forecast Rolling-Origin Backtest ===")
    depts = [None, "Water", "Roads", "Electricity", "Sanitation", "Traffic", "General / Other"]
    fc_rows = []
    overall_fc = None
    for d in depts:
        series = load_weekly_series(department_name=d, district_id=None, include_synthetic_history=True, db_only=False)
        fc = forecast_volume_series(series, horizon=4)
        if d is None:
            overall_fc = fc
        bt = fc["backtest_metrics"]
        fc_rows.append({
            "Department": d or "All Combined",
            "Weeks": fc["history_weeks"],
            "Chosen": fc["chosen_method"],
            "Naive_MAE": bt["Seasonal-Naive Baseline"]["mae"],
            "Naive_MAPE": bt["Seasonal-Naive Baseline"]["mape"],
            "SARIMA_MAE": bt["SARIMA(1,0,1)(1,0,0,4)"]["mae"],
            "SARIMA_MAPE": bt["SARIMA(1,0,1)(1,0,0,4)"]["mape"],
            "W1_Pred": fc["forecast"][0]["predicted_volume"],
            "W1_Handlers": fc["forecast"][0]["suggested_handlers"],
        })
    print(pd.DataFrame(fc_rows).to_string(index=False))

    # Plot overall history + 4-week forecast
    if overall_fc:
        hist = overall_fc["history"][-20:]
        fpts = overall_fc["forecast"]
        fig, ax = plt.subplots(figsize=(8.2, 4.4))
        ax.plot([h["week_start"] for h in hist], [h["volume"] for h in hist], marker="o", label="Observed Weekly")
        fx = [p["week_start"] for p in fpts]
        fy = [p["predicted_volume"] for p in fpts]
        fl = [p["lower_ci"] for p in fpts]
        fh = [p["upper_ci"] for p in fpts]
        ax.plot(fx, fy, marker="s", color="#059669", label=f"Forecast ({overall_fc['chosen_method']})")
        ax.fill_between(fx, fl, fh, color="#10b981", alpha=0.22, label="80% Confidence Band")
        ax.set_title("4-Week Weekly Grievance Volume Forecast & Backtest Winner")
        ax.set_ylabel("Weekly Complaints")
        plt.xticks(rotation=35, ha="right", fontsize=8)
        ax.legend()
        fig.tight_layout()
        fig.savefig(charts_dir / "forecast_backtest.png", dpi=150)
        plt.close(fig)


if __name__ == "__main__":
    main()
