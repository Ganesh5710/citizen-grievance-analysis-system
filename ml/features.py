"""
ml/features.py
Custom scikit-learn transformer extracting duration & recurrence numeric features
from raw complaint text so TF-IDF word/char features combine seamlessly across
all four benchmarked estimators (including MultinomialNB, which requires >= 0).
"""

from __future__ import annotations

from functools import lru_cache
import math
from typing import Iterable

import numpy as np
from scipy import sparse
from sklearn.base import BaseEstimator, TransformerMixin

from nlp.duration import analyze_duration_and_recurrence


DURATION_FEATURE_NAMES = np.array([
    "duration_hours_norm",
    "duration_log_norm",
    "has_duration",
    "bucket_lt24h",
    "bucket_1to6d",
    "bucket_7d_plus",
    "is_recurring",
])


@lru_cache(maxsize=16384)
def _extract_feature_tuple(text: str) -> tuple[float, float, float, float, float, float, float]:
    info = analyze_duration_and_recurrence(text)
    hrs = info["hours"]
    rec = 1.0 if info["recurring"] else 0.0
    if hrs is None:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, rec)
    h = float(hrs)
    return (
        min(h / 720.0, 2.0),
        math.log1p(h) / 9.0,
        1.0,
        1.0 if h < 24.0 else 0.0,
        1.0 if 24.0 <= h < 168.0 else 0.0,
        1.0 if h >= 168.0 else 0.0,
        rec,
    )


class DurationNumericFeatures(BaseEstimator, TransformerMixin):
    """Extract non-negative duration + bucket + recurrence features from complaint strings."""

    def fit(self, X: Iterable[str], y=None):
        return self

    def transform(self, X: Iterable[str]) -> sparse.csr_matrix:
        rows = [_extract_feature_tuple(raw if isinstance(raw, str) else "") for raw in X]
        arr = np.asarray(rows, dtype=np.float64)
        return sparse.csr_matrix(arr)

    def get_feature_names_out(self, input_features=None) -> np.ndarray:
        return DURATION_FEATURE_NAMES
