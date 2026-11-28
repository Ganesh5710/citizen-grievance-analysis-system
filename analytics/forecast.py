"""
analytics/forecast.py
Phase 2: 4-week complaint volume forecasting per department (and per district for admins),
comparing SARIMA (`statsmodels`) against a Seasonal-Naive baseline with rolling-origin
backtesting (MAE & MAPE), confidence intervals, and staffing hints.
"""

from __future__ import annotations

from contextlib import closing
import math
from pathlib import Path
from typing import Any
import warnings

import numpy as np
import pandas as pd

from core.config import PROJECT_ROOT
import core.database as _db
from core.permissions import require

_MIN_SARIMA_WEEKS = 16


def load_weekly_series(
    *,
    department_name: str | None = None,
    district_id: int | None = None,
    include_synthetic_history: bool = True,
    db_only: bool = False,
) -> pd.Series:
    """
    Build a weekly complaint count Series indexed by Monday dates.
    Reads non-deleted live complaints from SQLite; optionally supplements sparse DB
    history with historical rows from data/synthetic_complaints.csv when unfiltered
    or department-filtered (for demonstration/backtesting when DB has < 8 weeks).
    """
    frames: list[pd.DataFrame] = []
    with closing(_db.get_connection()) as conn:
        where = ["c.is_deleted = 0", "COALESCE(c.is_demo_spike, 0) = 0"]
        params: list[Any] = []
        if department_name:
            where.append("d.name = ?")
            params.append(department_name)
        if district_id is not None:
            where.append("c.district_id = ?")
            params.append(int(district_id))
        rows = conn.execute(
            f"""
            SELECT c.created_at, d.name AS department
            FROM complaints c
            JOIN departments d ON d.id = c.department_id
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchall()
    if rows:
        df_db = pd.DataFrame([dict(r) for r in rows])
        df_db["created_at"] = pd.to_datetime(df_db["created_at"], errors="coerce", utc=True)
        df_db = df_db.dropna(subset=["created_at"])
        if not df_db.empty:
            frames.append(df_db[["created_at"]])

    if include_synthetic_history and not db_only and district_id is None:
        syn_path = PROJECT_ROOT / "data" / "synthetic_complaints.csv"
        if syn_path.exists():
            syn = pd.read_csv(syn_path, usecols=["created_at", "department"])
            if department_name:
                syn = syn[syn["department"] == department_name]
            if not syn.empty:
                syn["created_at"] = pd.to_datetime(syn["created_at"], errors="coerce", utc=True)
                syn = syn.dropna(subset=["created_at"])
                frames.append(syn[["created_at"]])

    if not frames:
        return pd.Series(dtype=float)

    all_df = pd.concat(frames, ignore_index=True)
    ts_naive = all_df["created_at"].dt.tz_localize(None)
    week_monday = (ts_naive - pd.to_timedelta(ts_naive.dt.weekday, unit="D")).dt.normalize()
    weekly = (
        all_df.assign(week=week_monday)
        .groupby("week")
        .size()
        .sort_index()
        .astype(float)
    )
    if len(weekly) >= 2:
        full_idx = pd.date_range(weekly.index.min(), weekly.index.max(), freq="7D")
        weekly = weekly.reindex(full_idx, fill_value=0.0)
    return weekly


def _seasonal_naive_predict(history: np.ndarray, horizon: int = 4, season: int = 4) -> np.ndarray:
    """Predict future `horizon` weeks from seasonal lag (or trailing mean for short histories)."""
    n = len(history)
    if n == 0:
        return np.zeros(horizon, dtype=float)
    if n < season:
        return np.full(horizon, float(np.mean(history)), dtype=float)
    preds = []
    for h in range(horizon):
        lag_idx = n - season + (h % season)
        preds.append(float(history[lag_idx]))
    return np.maximum(0.0, np.array(preds, dtype=float))


def _sarima_predict(history: np.ndarray, horizon: int = 4) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Fit lightweight SARIMAX(1,0,1)(1,0,0,4); return (mean, low, high) or None on failure."""
    if len(history) < _MIN_SARIMA_WEEKS or float(np.std(history)) == 0.0:
        return None
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(
                history,
                order=(1, 0, 1),
                seasonal_order=(1, 0, 0, 4),
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            fit = model.fit(disp=False, maxiter=50)
            fc = fit.get_forecast(steps=horizon)
            mean = np.maximum(0.0, np.asarray(fc.predicted_mean, dtype=float))
            ci = np.asarray(fc.conf_int(alpha=0.20), dtype=float)  # 80% CI
            low = np.maximum(0.0, ci[:, 0])
            high = np.maximum(mean, ci[:, 1])
            return mean, low, high
    except Exception:
        return None


def _compute_mae_mape(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    mae = float(np.mean(np.abs(y_true - y_pred)))
    denom = np.where(np.abs(y_true) < 1e-6, 1.0, np.abs(y_true))
    mape = float(np.mean(np.abs(y_true - y_pred) / denom) * 100.0)
    return round(mae, 3), round(mape, 2)


def forecast_volume_series(
    weekly: pd.Series,
    horizon: int = 4,
    *,
    capacity_per_handler_week: int = 15,
) -> dict[str, Any]:
    """
    Run rolling-origin backtest comparing SARIMA vs Seasonal-Naive baseline, pick the winner
    honestly, forecast `horizon` future weeks with 80% confidence intervals & staffing hints.
    """
    vals = weekly.to_numpy(dtype=float) if len(weekly) else np.array([], dtype=float)
    n = len(vals)

    sarima_errors: list[tuple[float, float]] = []
    naive_errors: list[tuple[float, float]] = []

    if n >= 8:
        start_origin = max(4, n - 8)
        for origin in range(start_origin, n - 1):
            step_h = min(horizon, n - origin)
            hist_slice = vals[:origin]
            actual = vals[origin : origin + step_h]

            n_pred = _seasonal_naive_predict(hist_slice, horizon=step_h)
            naive_errors.append(_compute_mae_mape(actual, n_pred))

            s_res = _sarima_predict(hist_slice, horizon=step_h)
            if s_res is not None:
                sarima_errors.append(_compute_mae_mape(actual, s_res[0]))

    naive_mae = round(float(np.mean([e[0] for e in naive_errors])), 3) if naive_errors else None
    naive_mape = round(float(np.mean([e[1] for e in naive_errors])), 2) if naive_errors else None
    sarima_mae = round(float(np.mean([e[0] for e in sarima_errors])), 3) if sarima_errors else None
    sarima_mape = round(float(np.mean([e[1] for e in sarima_errors])), 2) if sarima_errors else None

    beats_baseline = (
        sarima_mae is not None
        and naive_mae is not None
        and sarima_mae < naive_mae
    )

    last_week = weekly.index[-1] if n > 0 else pd.Timestamp.now("UTC").tz_localize(None).normalize()
    future_dates = [(last_week + pd.Timedelta(days=7 * (i + 1))).strftime("%Y-%m-%d") for i in range(horizon)]

    s_full = _sarima_predict(vals, horizon=horizon) if beats_baseline else None
    if s_full is not None and beats_baseline:
        chosen_method = "SARIMA(1,0,1)(1,0,0,4)"
        note = "SARIMA beat Seasonal-Naive baseline on rolling-origin MAE."
        fc_mean, fc_low, fc_high = s_full
    else:
        chosen_method = "Seasonal-Naive Baseline"
        if sarima_mae is not None and naive_mae is not None:
            note = (
                f"SARIMA (MAE={sarima_mae}) did not beat Seasonal-Naive baseline "
                f"(MAE={naive_mae}); falling back to baseline."
            )
        else:
            note = "Insufficient history (< 16 weeks) for SARIMA; using Seasonal-Naive baseline."
        fc_mean = _seasonal_naive_predict(vals, horizon=horizon)
        resid_std = float(np.std(vals[-min(n, 8):])) if n >= 2 else max(1.0, float(np.mean(fc_mean)) * 0.25)
        fc_low = np.maximum(0.0, fc_mean - 1.28 * resid_std)
        fc_high = fc_mean + 1.28 * resid_std

    forecast_rows = []
    for dt_str, m_val, l_val, h_val in zip(future_dates, fc_mean, fc_low, fc_high):
        m_r = round(float(m_val), 1)
        staff_hint = max(1, int(math.ceil(m_val / max(1, capacity_per_handler_week)))) if m_val > 0 else 1
        forecast_rows.append({
            "week_start": dt_str,
            "predicted_volume": m_r,
            "lower_ci": round(float(l_val), 1),
            "upper_ci": round(float(h_val), 1),
            "suggested_handlers": staff_hint,
        })

    return {
        "history_weeks": int(n),
        "chosen_method": chosen_method,
        "beats_baseline": beats_baseline,
        "method_note": note,
        "backtest_metrics": {
            "Seasonal-Naive Baseline": {"mae": naive_mae, "mape": naive_mape},
            "SARIMA(1,0,1)(1,0,0,4)": {"mae": sarima_mae, "mape": sarima_mape},
        },
        "history": [
            {"week_start": idx.strftime("%Y-%m-%d"), "volume": round(float(v), 1)}
            for idx, v in weekly.items()
        ],
        "forecast": forecast_rows,
    }


def get_scoped_volume_forecast(
    actor: dict | None,
    *,
    department_name: str | None = None,
    district_id: int | None = None,
    horizon: int = 4,
    db_only: bool = False,
) -> dict[str, Any]:
    """
    Role-scoped weekly forecast:
      - employee: strictly locked to actor's own department (+ own district if scoped)
      - admin: locked to actor's assigned districts (error if querying outside jurisdiction)
      - super_admin: statewide or any selected district/department
    """
    require(actor, "view_analytics")
    role = actor.get("role")
    eff_dept = department_name
    eff_dist = district_id

    if role == "employee":
        eff_dept = actor.get("department") or department_name
        if not eff_dept:
            raise ValueError("Employee has no assigned department.")
        if actor.get("district_id") is not None:
            eff_dist = int(actor["district_id"])
    elif role == "admin":
        with closing(_db.get_connection()) as conn:
            j_rows = conn.execute(
                "SELECT district_id FROM admin_jurisdictions WHERE user_id = ?",
                (actor["id"],),
            ).fetchall()
        allowed_dids = [int(r["district_id"]) for r in j_rows]
        if eff_dist is not None and allowed_dids and int(eff_dist) not in allowed_dids:
            raise PermissionError("Admin cannot view forecast outside assigned district jurisdiction.")
        if eff_dist is None and allowed_dids:
            eff_dist = allowed_dids[0]

    series = load_weekly_series(
        department_name=eff_dept,
        district_id=eff_dist,
        include_synthetic_history=True,
        db_only=db_only,
    )
    res = forecast_volume_series(series, horizon=horizon)
    res["scope"] = {"department": eff_dept or "All Departments", "district_id": eff_dist}
    return res
