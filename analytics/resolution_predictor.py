"""
analytics/resolution_predictor.py
Phase 2: Gradient Boosting resolution-time regression (hours) on department,
priority, district/region, hour, weekday, and text_length, benchmarked against
Mean Baseline with MAE and RMSE, cached inference, and graceful fallback.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, root_mean_squared_error
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from core.config import PROJECT_ROOT, get_env_str
from core.logging_setup import get_logger

_logger = get_logger("analytics.resolution")

_LOCK = threading.Lock()
_CACHED_BUNDLE: dict[str, Any] | None = None

_FALLBACK_HOURS = {
    ("Water", "High"): 6.5, ("Water", "Medium"): 32.0, ("Water", "Low"): 72.0,
    ("Electricity", "High"): 5.5, ("Electricity", "Medium"): 28.0, ("Electricity", "Low"): 64.0,
    ("Roads", "High"): 12.0, ("Roads", "Medium"): 44.0, ("Roads", "Low"): 96.0,
    ("Sanitation", "High"): 8.0, ("Sanitation", "Medium"): 36.0, ("Sanitation", "Low"): 78.0,
    ("Traffic", "High"): 6.0, ("Traffic", "Medium"): 30.0, ("Traffic", "Low"): 68.0,
}


def get_res_model_dir() -> Path:
    return Path(get_env_str("CGS_ML_ACTIVE_MODEL_DIR", str(PROJECT_ROOT / "ml" / "models")))


def _build_features_df(df: pd.DataFrame) -> pd.DataFrame:
    created = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    district_col = df["district"] if "district" in df.columns else df.get("ward_name", pd.Series(["Guntur"] * len(df)))
    return pd.DataFrame({
        "department": df["department"].fillna("General / Other").astype(str),
        "priority": df["priority"].fillna("Medium").astype(str),
        "district": district_col.fillna("Guntur").astype(str),
        "hour": created.dt.hour.fillna(12).astype(int),
        "weekday": created.dt.weekday.fillna(2).astype(int),
        "text_length": df["text"].fillna("").astype(str).str.len().astype(int),
    })


def train_resolution_time_model(
    csv_path: Path | str | None = None,
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    Train HistGradientBoostingRegressor to predict resolution_hours and evaluate
    MAE & RMSE against Mean Baseline on a grouped held-out split.
    """
    src = Path(csv_path) if csv_path else (PROJECT_ROOT / "data" / "synthetic_complaints.csv")
    df = pd.read_csv(src)
    df = df.dropna(subset=["resolution_hours", "department", "priority", "created_at", "text"]).copy()
    df["resolution_hours"] = pd.to_numeric(df["resolution_hours"], errors="coerce")
    df = df[df["resolution_hours"] > 0].reset_index(drop=True)

    X = _build_features_df(df)
    y = df["resolution_hours"].to_numpy(dtype=float)

    if "template_group" in df.columns and df["template_group"].nunique() >= 5:
        gss = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
        tr_idx, te_idx = next(gss.split(X, y, groups=df["template_group"]))
    else:
        rng = np.random.default_rng(42)
        perm = rng.permutation(len(df))
        cut = int(len(df) * 0.8)
        tr_idx, te_idx = perm[:cut], perm[cut:]

    X_tr, X_te = X.iloc[tr_idx], X.iloc[te_idx]
    y_tr, y_te = y[tr_idx], y[te_idx]

    mean_pred = np.full(len(y_te), float(np.mean(y_tr)))
    base_mae = float(mean_absolute_error(y_te, mean_pred))
    base_rmse = float(root_mean_squared_error(y_te, mean_pred))

    cat_cols = ["department", "priority", "district"]
    num_cols = ["hour", "weekday", "text_length"]
    pre = ColumnTransformer([
        (
            "cat",
            OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1),
            cat_cols,
        ),
        ("num", "passthrough", num_cols),
    ])

    reg = Pipeline([
        ("pre", pre),
        (
            "gbr",
            HistGradientBoostingRegressor(
                max_iter=150,
                max_depth=6,
                learning_rate=0.06,
                random_state=42,
            ),
        ),
    ])
    reg.fit(X_tr, y_tr)
    gbr_pred = np.maximum(0.5, reg.predict(X_te))
    gbr_mae = float(mean_absolute_error(y_te, gbr_pred))
    gbr_rmse = float(root_mean_squared_error(y_te, gbr_pred))
    resid_mae = gbr_mae

    beats = gbr_mae < base_mae and gbr_rmse < base_rmse
    note = (
        f"HistGradientBoostingRegressor beat Mean Baseline (MAE {gbr_mae:.2f}h < {base_mae:.2f}h, "
        f"RMSE {gbr_rmse:.2f}h < {base_rmse:.2f}h)."
        if beats
        else f"Model did not beat Mean Baseline (MAE {gbr_mae:.2f}h vs {base_mae:.2f}h); baseline used."
    )

    save_dir = Path(out_dir) if out_dir else get_res_model_dir()
    save_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = save_dir / "res_time_model.joblib"
    meta_path = save_dir / "res_time_model.json"

    meta = {
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "dataset_rows": int(len(df)),
        "train_rows": int(len(X_tr)),
        "test_rows": int(len(X_te)),
        "features": cat_cols + num_cols,
        "mean_baseline_hours": round(float(np.mean(y_tr)), 2),
        "baseline_metrics": {"mae_hours": round(base_mae, 3), "rmse_hours": round(base_rmse, 3)},
        "model_metrics": {"mae_hours": round(gbr_mae, 3), "rmse_hours": round(gbr_rmse, 3)},
        "beats_baseline": beats,
        "note": note,
        "resid_mae_hours": round(resid_mae, 2),
    }

    bundle = {"pipeline": reg, "metadata": meta}
    joblib.dump(bundle, artifact_path, compress=3)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    global _CACHED_BUNDLE
    with _LOCK:
        _CACHED_BUNDLE = bundle
    return meta


def _get_bundle() -> dict[str, Any] | None:
    global _CACHED_BUNDLE
    with _LOCK:
        if _CACHED_BUNDLE is not None:
            return _CACHED_BUNDLE
        p = get_res_model_dir() / "res_time_model.joblib"
        if p.exists():
            try:
                _CACHED_BUNDLE = joblib.load(p)
                return _CACHED_BUNDLE
            except Exception as exc:
                _logger.warning("Failed loading res_time_model (%s); falling back.", exc)
        return None


def predict_resolution_hours(
    department: str,
    priority: str,
    *,
    district_name: str = "",
    created_at: str | datetime | None = None,
    text: str = "",
) -> dict[str, Any]:
    """
    Predict approximate resolution hours for a complaint.
    Explicitly marked as an estimate (`is_estimate=True`); never crashes offline.
    """
    if isinstance(created_at, datetime):
        dt = created_at
    elif isinstance(created_at, str) and created_at.strip():
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)

    bundle = _get_bundle()
    if bundle is not None and bundle.get("metadata", {}).get("beats_baseline", False):
        try:
            row_df = pd.DataFrame([{
                "department": department or "General / Other",
                "priority": priority or "Medium",
                "district": district_name or "Guntur",
                "hour": int(dt.hour),
                "weekday": int(dt.weekday()),
                "text_length": len(text or ""),
            }])
            est = float(np.maximum(1.0, bundle["pipeline"].predict(row_df)[0]))
            spread = float(bundle["metadata"].get("resid_mae_hours", 8.0))
            low = max(1.0, round(est - spread * 0.5, 1))
            high = round(est + spread * 0.5, 1)
            est_r = round(est, 1)
            return {
                "estimated_hours": est_r,
                "low_hours": low,
                "high_hours": high,
                "method": "HistGradientBoostingRegressor",
                "beats_baseline": True,
                "is_estimate": True,
                "label": f"~{est_r:.1f} hours ({low:.0f}-{high:.0f}h estimate)",
            }
        except Exception as exc:
            _logger.warning("Resolution prediction failed (%s); using heuristic baseline.", exc)

    fallback = _FALLBACK_HOURS.get((department, priority), {"High": 8.0, "Medium": 36.0, "Low": 84.0}.get(priority, 36.0))
    low = max(1.0, round(fallback * 0.7, 1))
    high = round(fallback * 1.3, 1)
    return {
        "estimated_hours": round(fallback, 1),
        "low_hours": low,
        "high_hours": high,
        "method": "Department-Priority Baseline",
        "beats_baseline": False,
        "is_estimate": True,
        "label": f"~{fallback:.1f} hours ({low:.0f}-{high:.0f}h baseline estimate)",
    }
