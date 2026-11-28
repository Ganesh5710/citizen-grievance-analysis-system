"""
scripts/train.py
CLI entrypoint to train & evaluate ML models on data/synthetic_complaints.csv
plus any human-reviewed complaints from SQLite.
By default saves a candidate version in ml/models/ without auto-activating it;
pass --activate-initial only on initial setup when no active model exists yet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config import get_env_str  # noqa: E402
from core.database import get_db_path  # noqa: E402
from ml.trainer import train_and_evaluate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Train citizen grievance ML classifiers.")
    parser.add_argument("--csv", default=str(PROJECT_ROOT / "data" / "synthetic_complaints.csv"))
    parser.add_argument("--db", default=str(get_db_path()))
    parser.add_argument("--models-dir", default=get_env_str("ML_MODELS_DIR", str(PROJECT_ROOT / "ml" / "models")))
    parser.add_argument("--activate-initial", action="store_true", help="Write active_model.json if none exists yet.")
    args = parser.parse_args()

    meta = train_and_evaluate(
        csv_path=args.csv,
        db_path=args.db,
        models_dir=args.models_dir,
        activate_if_none=args.activate_initial,
    )
    print(f"Trained version: {meta['version']} (activated={meta['activated']})")
    print(f"Dataset rows: {meta['dataset_rows']} | Train: {meta['train_rows']} | Test: {meta['test_rows']}")
    print("\n--- Department Model Comparison ---")
    for row in meta["department"]["model_comparison"]:
        print(
            f"  {row['model']:22s} CV F1={row['cv_macro_f1_mean']:.4f} "
            f"Test Acc={row['test_accuracy']:.4f} P={row['test_macro_precision']:.4f} "
            f"R={row['test_macro_recall']:.4f} F1={row['test_macro_f1']:.4f} "
            f"fit={row['train_time_sec']:.2f}s inf={row['inference_time_ms']:.1f}ms"
        )
    print(f"Best Department Model: {meta['department']['best_model']}")
    print("\n--- Priority Model Comparison ---")
    for row in meta["priority"]["model_comparison"]:
        print(
            f"  {row['model']:22s} CV F1={row['cv_macro_f1_mean']:.4f} "
            f"Test Acc={row['test_accuracy']:.4f} P={row['test_macro_precision']:.4f} "
            f"R={row['test_macro_recall']:.4f} F1={row['test_macro_f1']:.4f} "
            f"fit={row['train_time_sec']:.2f}s inf={row['inference_time_ms']:.1f}ms"
        )
    print(f"Best Priority Model: {meta['priority']['best_model']}")
    print("\n--- Department Language Slices ---")
    for lang, stats in meta["department"]["language_slices"].items():
        print(f"  {lang:6s} support={stats['support']:4d} acc={stats['accuracy']:.4f} f1={stats['macro_f1']:.4f}")
    print("\n--- Department Ablation ---")
    for row in meta["department"]["ablation"]:
        print(f"  {row['feature_set']:22s} acc={row['test_accuracy']:.4f} f1={row['test_macro_f1']:.4f}")
    print("\n--- Priority Ablation ---")
    for row in meta["priority"]["ablation"]:
        print(f"  {row['feature_set']:22s} acc={row['test_accuracy']:.4f} f1={row['test_macro_f1']:.4f}")
    print(f"\nSaved artifact: {meta['artifact_path']}")


if __name__ == "__main__":
    main()
