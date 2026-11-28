"""
ml/trainer.py
Offline model training, StratifiedGroupKFold cross-validation (no template leakage),
model comparison, feature ablation, per-language evaluation, chart generation, and
versioned artifact persistence under ml/models/.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import matplotlib
import numpy as np
import pandas as pd
import sklearn
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from core.config import PROJECT_ROOT, get_env_str
from core.logging_setup import get_logger
from ml.features import DurationNumericFeatures
from model.preprocessing import clean_text

logger = get_logger("ml.trainer")

DEFAULT_SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic_complaints.csv"
DEFAULT_EVAL_CHARTS_DIR = PROJECT_ROOT / "docs" / "evaluation_charts"


def get_models_dir() -> Path:
    return Path(get_env_str("ML_MODELS_DIR", str(PROJECT_ROOT / "ml" / "models")))


def get_active_model_pointer_path() -> Path:
    return get_models_dir() / "active_model.json"


def build_vectorizer(mode: str = "both") -> FeatureUnion | TfidfVectorizer:
    word_vec = TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=6000,
        sublinear_tf=True,
        min_df=2,
        strip_accents="unicode",
    )
    char_vec = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(3, 5),
        max_features=8000,
        sublinear_tf=True,
        min_df=3,
    )
    dur_vec = DurationNumericFeatures()
    if mode == "word":
        return word_vec
    if mode == "char":
        return char_vec
    if mode == "tfidf_only":
        return FeatureUnion([("word", word_vec), ("char", char_vec)])
    return FeatureUnion([("word", word_vec), ("char", char_vec), ("duration", dur_vec)])


def _candidate_estimators() -> dict[str, Any]:
    return {
        "MultinomialNB": lambda: MultinomialNB(alpha=0.3),
        "LogisticRegression": lambda: LogisticRegression(
            max_iter=1000, C=2.0, class_weight="balanced", random_state=42
        ),
        "LinearSVC_Calibrated": lambda: CalibratedClassifierCV(
            LinearSVC(C=1.0, class_weight="balanced", random_state=42),
            cv=3,
        ),
        "RandomForest": lambda: RandomForestClassifier(
            n_estimators=50,
            max_depth=24,
            class_weight="balanced_subsample",
            n_jobs=1,
            random_state=42,
        ),
    }


def _load_reviewed_db_rows(db_path: Path | None) -> pd.DataFrame:
    """Load non-synthetic human-reviewed complaints if available in SQLite."""
    if db_path is None or not db_path.exists():
        return pd.DataFrame(columns=["text", "department", "priority", "language", "template_group"])
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "complaints" not in tables or "departments" not in tables:
            conn.close()
            return pd.DataFrame()
        c_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(complaints)").fetchall()
        }
        lang_expr = "COALESCE(c.detected_language, 'en')" if "detected_language" in c_cols else "'en'"
        rows = conn.execute(
            f"""
            SELECT c.id, c.text, d.name AS department, c.priority,
                   {lang_expr} AS language
            FROM complaints c
            JOIN departments d ON d.id = c.department_id
            WHERE c.is_deleted = 0
              AND (c.status IN ('Resolved', 'Closed')
                   OR c.id IN (SELECT complaint_id FROM priority_feedback_log))
            """
        ).fetchall()
        if "priority_feedback_log" in tables:
            fb_rows = conn.execute(
                """
                SELECT f.complaint_id AS id, f.text, d.name AS department,
                       f.corrected_priority AS priority
                FROM priority_feedback_log f
                JOIN complaints c ON c.id = f.complaint_id
                JOIN departments d ON d.id = c.department_id
                WHERE c.is_deleted = 0
                """
            ).fetchall()
        else:
            fb_rows = []
        conn.close()
        records = [
            {
                "text": r["text"],
                "department": r["department"],
                "priority": r["priority"],
                "language": r["language"] if r["language"] in ("en", "te", "hi", "mixed") else "en",
                "template_group": f"live_db_{r['id']}",
            }
            for r in rows
            if r["text"] and len(str(r["text"]).strip()) >= 8
        ]
        for r in fb_rows:
            if r["text"] and len(str(r["text"]).strip()) >= 8:
                records.append({
                    "text": r["text"],
                    "department": r["department"],
                    "priority": r["priority"],
                    "language": "en",
                    "template_group": f"feedback_db_{r['id']}",
                })
        return pd.DataFrame(records)
    except Exception as exc:
        logger.warning("Skipping live DB rows during training: %s", exc)
        return pd.DataFrame()


def load_training_dataset(
    csv_path: Path | str | None = None,
    db_path: Path | str | None = None,
) -> pd.DataFrame:
    src = Path(csv_path) if csv_path else DEFAULT_SYNTHETIC_CSV
    if not src.exists():
        raise FileNotFoundError(f"Training dataset not found: {src}")
    df = pd.read_csv(src)
    required = {"text", "department", "priority", "template_group"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Dataset missing required columns: {sorted(missing)}")
    if "language" not in df.columns:
        df["language"] = "en"
    df = df[["text", "department", "priority", "language", "template_group"]].dropna().copy()

    if db_path:
        live_df = _load_reviewed_db_rows(Path(db_path))
        if not live_df.empty:
            df = pd.concat([df, live_df], ignore_index=True)

    df["text"] = df["text"].astype(str)
    df["department"] = df["department"].astype(str)
    df["priority"] = df["priority"].astype(str)
    df["language"] = df["language"].astype(str)
    df["template_group"] = df["template_group"].astype(str)
    return df


def _stratified_group_holdout(
    df: pd.DataFrame,
    target_col: str,
    n_splits: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use StratifiedGroupKFold first fold as held-out test (~20%) with zero template leakage."""
    unique_groups = df["template_group"].nunique()
    splits = min(n_splits, max(2, unique_groups))
    sgkf = StratifiedGroupKFold(n_splits=splits, shuffle=True, random_state=42)
    train_idx, test_idx = next(sgkf.split(df["text"], df[target_col], groups=df["template_group"]))
    return df.iloc[train_idx].reset_index(drop=True), df.iloc[test_idx].reset_index(drop=True)


def _evaluate_task(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target_col: str,
    cv_splits: int = 5,
) -> dict[str, Any]:
    X_train = train_df["text"].tolist()
    y_train = train_df[target_col].tolist()
    groups_train = train_df["template_group"].tolist()

    X_test = test_df["text"].tolist()
    y_test = test_df[target_col].tolist()

    n_splits = min(cv_splits, max(2, train_df["template_group"].nunique()))
    sgkf = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=42)

    # Pre-vectorize each CV fold and full train/test once to avoid repeating TF-IDF extraction 4x
    fold_cache = []
    for tr_i, val_i in sgkf.split(X_train, y_train, groups=groups_train):
        vec = build_vectorizer("both")
        X_tr_mat = vec.fit_transform([X_train[i] for i in tr_i])
        y_tr = [y_train[i] for i in tr_i]
        X_va_mat = vec.transform([X_train[i] for i in val_i])
        y_va = [y_train[i] for i in val_i]
        fold_cache.append((X_tr_mat, y_tr, X_va_mat, y_va))

    full_vec = build_vectorizer("both")
    X_train_mat = full_vec.fit_transform(X_train)
    X_test_mat = full_vec.transform(X_test)

    results: list[dict[str, Any]] = []
    fitted_pipelines: dict[str, Pipeline] = {}

    for model_name, est_fn in _candidate_estimators().items():
        fold_acc: list[float] = []
        fold_f1: list[float] = []
        for X_tr_mat, y_tr, X_va_mat, y_va in fold_cache:
            clf_fold = est_fn()
            clf_fold.fit(X_tr_mat, y_tr)
            preds = clf_fold.predict(X_va_mat)
            fold_acc.append(float(accuracy_score(y_va, preds)))
            fold_f1.append(float(f1_score(y_va, preds, average="macro", zero_division=0)))

        # Fit on full train_df using the shared fitted vectorizer and measure train/inference timings
        clf_full = est_fn()
        t_fit_start = time.perf_counter()
        clf_full.fit(X_train_mat, y_train)
        train_time_sec = round(time.perf_counter() - t_fit_start, 4)

        full_pipe = Pipeline([
            ("features", full_vec),
            ("clf", clf_full),
        ])
        t_inf_start = time.perf_counter()
        test_preds = clf_full.predict(X_test_mat)
        inference_time_ms = round((time.perf_counter() - t_inf_start) * 1000.0, 2)
        inf_per_sample_ms = round(inference_time_ms / max(1, len(X_test)), 4)

        test_acc = float(accuracy_score(y_test, test_preds))
        test_macro_prec = float(precision_score(y_test, test_preds, average="macro", zero_division=0))
        test_macro_rec = float(recall_score(y_test, test_preds, average="macro", zero_division=0))
        test_macro_f1 = float(f1_score(y_test, test_preds, average="macro", zero_division=0))
        test_weighted_f1 = float(f1_score(y_test, test_preds, average="weighted", zero_division=0))

        fitted_pipelines[model_name] = full_pipe
        results.append({
            "model": model_name,
            "cv_accuracy_mean": round(float(np.mean(fold_acc)), 4),
            "cv_accuracy_std": round(float(np.std(fold_acc)), 4),
            "cv_macro_f1_mean": round(float(np.mean(fold_f1)), 4),
            "cv_macro_f1_std": round(float(np.std(fold_f1)), 4),
            "test_accuracy": round(test_acc, 4),
            "test_macro_precision": round(test_macro_prec, 4),
            "test_macro_recall": round(test_macro_rec, 4),
            "test_macro_f1": round(test_macro_f1, 4),
            "test_weighted_f1": round(test_weighted_f1, 4),
            "train_time_sec": train_time_sec,
            "inference_time_ms": inference_time_ms,
            "inference_ms_per_sample": inf_per_sample_ms,
        })

    results.sort(key=lambda r: (r["cv_macro_f1_mean"], r["test_macro_f1"]), reverse=True)
    best_name = results[0]["model"]
    best_pipe = fitted_pipelines[best_name]
    best_preds = best_pipe.predict(X_test)
    labels = sorted(list(set(y_train) | set(y_test)))
    cm = confusion_matrix(y_test, best_preds, labels=labels)
    report = classification_report(
        y_test, best_preds, labels=labels, output_dict=True, zero_division=0
    )

    # Language slices on held-out test set
    lang_slices: dict[str, dict[str, float | int]] = {}
    for lang in sorted(test_df["language"].unique()):
        mask = (test_df["language"] == lang).to_numpy()
        y_l = np.array(y_test)[mask]
        p_l = np.array(best_preds)[mask]
        if len(y_l) == 0:
            continue
        lang_slices[lang] = {
            "support": int(len(y_l)),
            "accuracy": round(float(accuracy_score(y_l, p_l)), 4),
            "macro_f1": round(float(f1_score(y_l, p_l, average="macro", zero_division=0)), 4),
        }

    # Comprehensive ablation on the winning estimator family
    est_factory = _candidate_estimators()[best_name]
    ablation_specs: list[tuple[str, Any, list[str], list[str]]] = [
        ("word_unigram_1_1", TfidfVectorizer(ngram_range=(1, 1), max_features=6000, sublinear_tf=True, min_df=2), X_train, X_test),
        ("word_bigram_1_2", build_vectorizer("word"), X_train, X_test),
        ("char_wb_3_5", build_vectorizer("char"), X_train, X_test),
        ("stop_words_english", TfidfVectorizer(ngram_range=(1, 2), max_features=6000, sublinear_tf=True, min_df=2, stop_words="english"), X_train, X_test),
        ("clean_text_enabled", build_vectorizer("tfidf_only"), [clean_text(t) for t in X_train], [clean_text(t) for t in X_test]),
        ("tfidf_raw_tf", TfidfVectorizer(ngram_range=(1, 2), max_features=6000, sublinear_tf=False, min_df=2), X_train, X_test),
        ("tfidf_only", build_vectorizer("tfidf_only"), X_train, X_test),
        ("both", build_vectorizer("both"), X_train, X_test),
    ]
    ablation: list[dict[str, Any]] = []
    for mode_name, vec_obj, tr_texts, te_texts in ablation_specs:
        abl_pipe = Pipeline([
            ("features", vec_obj),
            ("clf", est_factory()),
        ])
        abl_pipe.fit(tr_texts, y_train)
        abl_preds = abl_pipe.predict(te_texts)
        ablation.append({
            "feature_set": mode_name,
            "test_accuracy": round(float(accuracy_score(y_test, abl_preds)), 4),
            "test_macro_precision": round(float(precision_score(y_test, abl_preds, average="macro", zero_division=0)), 4),
            "test_macro_recall": round(float(recall_score(y_test, abl_preds, average="macro", zero_division=0)), 4),
            "test_macro_f1": round(float(f1_score(y_test, abl_preds, average="macro", zero_division=0)), 4),
        })

    # Error analysis: top misclassified pairs + up to 8 sample errors
    errors: list[dict[str, str]] = []
    pair_counts: dict[tuple[str, str], int] = {}
    for text, true_lbl, pred_lbl, lang in zip(X_test, y_test, best_preds, test_df["language"].tolist()):
        if true_lbl != pred_lbl:
            pair = (str(true_lbl), str(pred_lbl))
            pair_counts[pair] = pair_counts.get(pair, 0) + 1
            if len(errors) < 8:
                errors.append({
                    "language": str(lang),
                    "true": str(true_lbl),
                    "predicted": str(pred_lbl),
                    "text": str(text)[:160],
                })
    top_confusions = [
        {"true": k[0], "predicted": k[1], "count": v}
        for k, v in sorted(pair_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    ]

    # Interpretability artifact: fit an auxiliary LogisticRegression if best isn't directly linear
    # or extract linear coefficients if best has coef_ (MultinomialNB / LogisticRegression / LinearSVC)
    explain_coef = None
    clf = best_pipe.named_steps["clf"]
    feat_names = best_pipe.named_steps["features"].get_feature_names_out()
    if hasattr(clf, "coef_"):
        explain_coef = {"classes": list(clf.classes_), "coef": clf.coef_, "features": feat_names}
    elif hasattr(clf, "feature_log_prob_"):
        explain_coef = {"classes": list(clf.classes_), "coef": clf.feature_log_prob_, "features": feat_names}
    elif hasattr(clf, "calibrated_classifiers_"):
        coefs = [c.estimator.coef_ for c in clf.calibrated_classifiers_ if hasattr(c.estimator, "coef_")]
        if coefs:
            explain_coef = {
                "classes": list(clf.classes_),
                "coef": np.mean(coefs, axis=0),
                "features": feat_names,
            }
    if explain_coef is None and "LogisticRegression" in fitted_pipelines:
        lr_pipe = fitted_pipelines["LogisticRegression"]
        lr_clf = lr_pipe.named_steps["clf"]
        explain_coef = {
            "classes": list(lr_clf.classes_),
            "coef": lr_clf.coef_,
            "features": lr_pipe.named_steps["features"].get_feature_names_out(),
        }

    return {
        "best_model_name": best_name,
        "best_pipeline": best_pipe,
        "explain_coef": explain_coef,
        "model_comparison": results,
        "labels": labels,
        "confusion_matrix": cm.tolist(),
        "classification_report": report,
        "language_slices": lang_slices,
        "ablation": ablation,
        "top_confusions": top_confusions,
        "sample_errors": errors,
        "train_size": len(train_df),
        "test_size": len(test_df),
    }


def _save_confusion_matrix_png(
    cm: list[list[int]],
    labels: list[str],
    title: str,
    out_path: Path,
) -> None:
    arr = np.array(cm, dtype=float)
    fig, ax = plt.subplots(figsize=(7.5, 6))
    im = ax.imshow(arr, interpolation="nearest", cmap="Blues")
    ax.figure.colorbar(im, ax=ax)
    ax.set(
        xticks=np.arange(len(labels)),
        yticks=np.arange(len(labels)),
        xticklabels=labels,
        yticklabels=labels,
        title=title,
        ylabel="True label",
        xlabel="Predicted label",
    )
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right", rotation_mode="anchor")
    thresh = arr.max() / 2.0 if arr.size else 0.0
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            ax.text(
                j, i, f"{int(arr[i, j])}",
                ha="center", va="center",
                color="white" if arr[i, j] > thresh else "black",
                fontsize=9,
            )
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _save_comparison_chart_png(
    dept_results: list[dict[str, Any]],
    prio_results: list[dict[str, Any]],
    out_path: Path,
) -> None:
    models = [r["model"] for r in dept_results]
    dept_f1 = [r["test_macro_f1"] for r in dept_results]
    prio_map = {r["model"]: r["test_macro_f1"] for r in prio_results}
    prio_f1 = [prio_map.get(m, 0.0) for m in models]

    x = np.arange(len(models))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    b1 = ax.bar(x - width / 2, dept_f1, width, label="Department Test Macro-F1", color="#2563eb")
    b2 = ax.bar(x + width / 2, prio_f1, width, label="Priority Test Macro-F1", color="#059669")
    ax.set_ylabel("Macro-F1 Score")
    ax.set_title("Classifier Model Comparison (Held-Out Unseen Templates)")
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=15, ha="right")
    ax.set_ylim(0, 1.08)
    ax.legend()
    for bars in (b1, b2):
        for bar in bars:
            h = bar.get_height()
            ax.annotate(f"{h:.3f}", (bar.get_x() + bar.get_width() / 2, h),
                        ha="center", va="bottom", fontsize=8, xytext=(0, 3), textcoords="offset points")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def train_and_evaluate(
    csv_path: Path | str | None = None,
    db_path: Path | str | None = None,
    models_dir: Path | str | None = None,
    charts_dir: Path | str | None = None,
    activate_if_none: bool = False,
    cv_splits: int = 5,
) -> dict[str, Any]:
    """
    Train department and priority classifiers, evaluate with StratifiedGroupKFold +
    held-out unseen templates, save versioned .joblib and metadata JSON.
    Never overwrites active_model.json if an active model already exists!
    """
    df = load_training_dataset(csv_path=csv_path, db_path=db_path)
    train_df, test_df = _stratified_group_holdout(df, target_col="department", n_splits=5)

    dept_eval = _evaluate_task(train_df, test_df, target_col="department", cv_splits=cv_splits)
    prio_eval = _evaluate_task(train_df, test_df, target_col="priority", cv_splits=cv_splits)

    ts = datetime.now(timezone.utc)
    version = ts.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(models_dir) if models_dir else get_models_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Guarantee unique version name if retrained twice within one second
    if (out_dir / f"grievance_ml_{version}.joblib").exists():
        version = ts.strftime("%Y%m%d_%H%M%S_%f")

    artifact_path = out_dir / f"grievance_ml_{version}.joblib"
    metadata_path = out_dir / f"grievance_ml_{version}.json"

    bundle = {
        "version": version,
        "trained_at": ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "dept_model_name": dept_eval["best_model_name"],
        "priority_model_name": prio_eval["best_model_name"],
        "dept_pipeline": dept_eval["best_pipeline"],
        "priority_pipeline": prio_eval["best_pipeline"],
        "dept_explain": dept_eval["explain_coef"],
        "priority_explain": prio_eval["explain_coef"],
    }
    joblib.dump(bundle, artifact_path, compress=3)

    charts_root = Path(charts_dir) if charts_dir else DEFAULT_EVAL_CHARTS_DIR
    dept_cm_path = charts_root / "dept_confusion_matrix.png"
    prio_cm_path = charts_root / "priority_confusion_matrix.png"
    comp_chart_path = charts_root / "model_comparison.png"
    _save_confusion_matrix_png(
        dept_eval["confusion_matrix"],
        dept_eval["labels"],
        f"Department Confusion Matrix ({dept_eval['best_model_name']})",
        dept_cm_path,
    )
    _save_confusion_matrix_png(
        prio_eval["confusion_matrix"],
        prio_eval["labels"],
        f"Priority Confusion Matrix ({prio_eval['best_model_name']})",
        prio_cm_path,
    )
    _save_comparison_chart_png(
        dept_eval["model_comparison"],
        prio_eval["model_comparison"],
        comp_chart_path,
    )

    dept_best_metrics = dept_eval["model_comparison"][0]
    prio_best_metrics = prio_eval["model_comparison"][0]

    metadata = {
        "version": version,
        "trained_at": ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "dataset_rows": int(len(df)),
        "train_rows": int(len(train_df)),
        "test_rows": int(len(test_df)),
        "unique_template_groups": int(df["template_group"].nunique()),
        "library_versions": {
            "python": sys.version.split()[0],
            "scikit_learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "joblib": joblib.__version__,
        },
        "department": {
            "best_model": dept_eval["best_model_name"],
            "best_metrics": dept_best_metrics,
            "model_comparison": dept_eval["model_comparison"],
            "labels": dept_eval["labels"],
            "confusion_matrix": dept_eval["confusion_matrix"],
            "language_slices": dept_eval["language_slices"],
            "ablation": dept_eval["ablation"],
            "top_confusions": dept_eval["top_confusions"],
            "sample_errors": dept_eval["sample_errors"],
        },
        "priority": {
            "best_model": prio_eval["best_model_name"],
            "best_metrics": prio_best_metrics,
            "model_comparison": prio_eval["model_comparison"],
            "labels": prio_eval["labels"],
            "confusion_matrix": prio_eval["confusion_matrix"],
            "language_slices": prio_eval["language_slices"],
            "ablation": prio_eval["ablation"],
            "top_confusions": prio_eval["top_confusions"],
            "sample_errors": prio_eval["sample_errors"],
        },
        "artifact_path": str(artifact_path),
        "metadata_path": str(metadata_path),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    pointer_path = out_dir / "active_model.json"
    activated = False
    if activate_if_none and not pointer_path.exists():
        pointer_path.write_text(
            json.dumps(
                {
                    "version": version,
                    "artifact_path": str(artifact_path),
                    "metadata_path": str(metadata_path),
                    "activated_at": ts.strftime("%Y-%m-%d %H:%M:%S UTC"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        activated = True

    metadata["activated"] = activated
    return metadata
