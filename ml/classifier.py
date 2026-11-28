"""
ml/classifier.py
Swappable ML inference & explainability service for complaint department and priority.
Falls back cleanly to rule-based core.classifier when no trained .joblib model is present.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from core.classifier import HIGH_KEYWORDS, MEDIUM_KEYWORDS
from core.classifier import classify as rule_classify
from core.config import PROJECT_ROOT, get_env_float, get_env_str
from core.logging_setup import get_logger
from core.priority_rules import compute_final_priority
from nlp.duration import analyze_duration_and_recurrence

logger = get_logger("ml.classifier")

EMERGENCY_KEYWORDS = list(HIGH_KEYWORDS) + [
    "live wire", "gas leak", "short circuit", "collapsed building",
    "electric shock", "flooding", "submerged", "critical condition",
    # Telugu & Hindi emergency hazard cues
    "అత్యవసరం", "షాక్", "గ్యాస్ లీక్", "ముంచేసి", "ప్రమాదం",
    "आग", "चिंगारी", "हादसा", "बाढ़", "गैस लीक", "करंट लग",
]


def find_emergency_keyword(text: str) -> str | None:
    if not text:
        return None
    lower = text.lower()
    for kw in EMERGENCY_KEYWORDS:
        if not kw.isascii():
            if " " in kw:
                if kw in lower:
                    return kw
            elif re.search(rf"(?:^|(?<=[\s,.;:!?])){re.escape(kw)}(?:$|(?=[\s,.;:!?]))", lower):
                return kw
        elif " " in kw:
            if kw in lower:
                return kw
        elif re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return kw
    return None


def has_emergency_keyword(text: str) -> bool:
    return find_emergency_keyword(text) is not None


class BaseComplaintClassifier(ABC):
    """Minimal swappable interface for complaint text classifiers."""

    @abstractmethod
    def predict(self, text: str, department_hint: str | None = None) -> dict[str, Any]:
        """Return department, priority, confidence, probabilities, top_terms, and model_version."""

    @abstractmethod
    def explain(self, text: str, num_features: int = 8) -> list[dict[str, Any]]:
        """Return LIME/attribution word weights for the department prediction."""


class HybridMLClassifier(BaseComplaintClassifier):
    """Loads versioned TF-IDF + linear/ensemble .joblib bundle with rule fallback."""

    def __init__(self, models_dir: Path | None = None) -> None:
        self.models_dir = models_dir or Path(
            get_env_str("ML_MODELS_DIR", str(PROJECT_ROOT / "ml" / "models"))
        )
        self._bundle: dict[str, Any] | None = None
        self._loaded_version: str | None = None
        self._load_active_bundle()

    def _resolve_active_artifact(self) -> tuple[str | None, Path | None]:
        pointer = self.models_dir / "active_model.json"
        if pointer.exists():
            try:
                data = json.loads(pointer.read_text(encoding="utf-8"))
                art = Path(data.get("artifact_path", ""))
                if not art.is_absolute():
                    art = PROJECT_ROOT / art
                if art.exists():
                    return str(data.get("version")), art
            except Exception as exc:
                logger.warning("Failed reading active_model.json: %s", exc)
        return None, None

    def _load_active_bundle(self) -> None:
        version, art_path = self._resolve_active_artifact()
        if art_path is None:
            self._bundle = None
            self._loaded_version = None
            return
        if version == self._loaded_version and self._bundle is not None:
            return
        try:
            self._bundle = joblib.load(art_path)
            self._loaded_version = version or self._bundle.get("version")
        except Exception as exc:
            logger.error("Failed loading ML artifact %s: %s", art_path, exc)
            self._bundle = None
            self._loaded_version = None

    def reload(self) -> None:
        self._bundle = None
        self._loaded_version = None
        self._load_active_bundle()

    def _extract_top_terms(
        self,
        text: str,
        pipeline: Any,
        explain_meta: dict[str, Any] | None,
        predicted_label: str,
        top_k: int = 6,
    ) -> list[dict[str, Any]]:
        if not text or not text.strip():
            return []
        try:
            vec = pipeline.named_steps["features"].transform([text])
            nz_indices = vec.nonzero()[1]
            if len(nz_indices) == 0:
                return []

            if explain_meta and predicted_label in explain_meta.get("classes", []):
                cls_idx = explain_meta["classes"].index(predicted_label)
                coef_row = explain_meta["coef"][cls_idx]
                feat_names = explain_meta["features"]
                scored = []
                for idx in nz_indices:
                    fname = str(feat_names[idx])
                    # Prefer readable word n-grams in staff display; include char fallback if needed
                    if fname.startswith("word__"):
                        clean = fname[len("word__"):]
                        weight = float(vec[0, idx] * coef_row[idx])
                        scored.append((clean, weight))
                if not scored:
                    for idx in nz_indices:
                        fname = str(feat_names[idx]).split("__", 1)[-1]
                        scored.append((fname, float(vec[0, idx] * coef_row[idx])))
                scored.sort(key=lambda item: item[1], reverse=True)
                positives = [
                    {"term": t, "weight": round(w, 4)}
                    for t, w in scored[:top_k]
                    if w > 0
                ]
                if positives:
                    return positives

            # Fallback: top TF-IDF terms in the input
            feat_names = pipeline.named_steps["features"].get_feature_names_out()
            pairs = []
            for idx in nz_indices:
                fname = str(feat_names[idx])
                if fname.startswith("word__"):
                    pairs.append((fname[len("word__"):], float(vec[0, idx])))
            pairs.sort(key=lambda x: x[1], reverse=True)
            return [{"term": t, "weight": round(w, 4)} for t, w in pairs[:top_k]]
        except Exception as exc:
            logger.debug("Top term extraction fallback: %s", exc)
            return []

    def predict(self, text: str, department_hint: str | None = None) -> dict[str, Any]:
        self._load_active_bundle()
        threshold = get_env_float("ML_CONFIDENCE_THRESHOLD", 0.60)
        emg_kw = find_emergency_keyword(text)
        emergency = emg_kw is not None
        dur_info = analyze_duration_and_recurrence(text)
        rule_dept, rule_prio = rule_classify(text)

        if self._bundle is None or not text or not text.strip():
            eff_dept = department_hint or rule_dept
            combined = compute_final_priority(
                department=eff_dept,
                emergency_override=emergency,
                emergency_keyword=emg_kw,
                duration_hours=dur_info["hours"],
                duration_matched_text=dur_info["matched_text"],
                duration_confidence=dur_info["confidence"],
                ml_priority=rule_prio,
                keyword_priority=rule_prio,
                is_recurring=bool(dur_info["recurring"]),
            )
            return {
                "department": rule_dept,
                "department_confidence": 0.45 if text and text.strip() else 0.0,
                "department_probs": {rule_dept: 0.45},
                "priority": combined["final_priority"],
                "ml_priority": rule_prio,
                "duration_priority": combined["duration_priority"],
                "priority_confidence": 0.99 if emergency else 0.55,
                "emergency_override": emergency,
                "emergency_keyword": emg_kw,
                "duration_hours": dur_info["hours"],
                "duration_matched_text": dur_info["matched_text"],
                "duration_confidence": dur_info["confidence"],
                "is_recurring": bool(dur_info["recurring"]),
                "recurring_phrase": dur_info["recurring_phrase"],
                "low_duration_confidence": combined["low_duration_confidence"],
                "priority_reason": combined["reason"],
                "confidence": 0.45 if text and text.strip() else 0.0,
                "low_confidence": True,
                "top_terms": [],
                "model_version": "rule-fallback",
            }

        dept_pipe = self._bundle["dept_pipeline"]
        prio_pipe = self._bundle["priority_pipeline"]

        dept_probs_arr = dept_pipe.predict_proba([text])[0]
        dept_classes = list(dept_pipe.classes_)
        dept_idx = int(np.argmax(dept_probs_arr))
        dept_label = str(dept_classes[dept_idx])
        dept_conf = float(dept_probs_arr[dept_idx])
        dept_probs = {
            str(cls): round(float(p), 4)
            for cls, p in sorted(zip(dept_classes, dept_probs_arr), key=lambda x: x[1], reverse=True)
        }

        prio_probs_arr = prio_pipe.predict_proba([text])[0]
        prio_classes = list(prio_pipe.classes_)
        prio_idx = int(np.argmax(prio_probs_arr))
        ml_prio_label = str(prio_classes[prio_idx])
        prio_conf = float(prio_probs_arr[prio_idx])

        eff_dept = department_hint or dept_label
        combined = compute_final_priority(
            department=eff_dept,
            emergency_override=emergency,
            emergency_keyword=emg_kw,
            duration_hours=dur_info["hours"],
            duration_matched_text=dur_info["matched_text"],
            duration_confidence=dur_info["confidence"],
            ml_priority=ml_prio_label,
            keyword_priority=rule_prio,
            is_recurring=bool(dur_info["recurring"]),
        )
        final_prio = combined["final_priority"]
        if emergency:
            prio_conf = max(prio_conf, 0.99)

        top_terms = self._extract_top_terms(
            text,
            dept_pipe,
            self._bundle.get("dept_explain"),
            dept_label,
        )

        return {
            "department": dept_label,
            "department_confidence": round(dept_conf, 4),
            "department_probs": dept_probs,
            "priority": final_prio,
            "ml_priority": ml_prio_label,
            "duration_priority": combined["duration_priority"],
            "priority_confidence": round(prio_conf, 4),
            "emergency_override": emergency,
            "emergency_keyword": emg_kw,
            "duration_hours": dur_info["hours"],
            "duration_matched_text": dur_info["matched_text"],
            "duration_confidence": dur_info["confidence"],
            "is_recurring": bool(dur_info["recurring"]),
            "recurring_phrase": dur_info["recurring_phrase"],
            "low_duration_confidence": combined["low_duration_confidence"],
            "priority_reason": combined["reason"],
            "confidence": round(dept_conf, 4),
            "low_confidence": dept_conf < threshold,
            "top_terms": top_terms,
            "model_version": str(self._bundle.get("version", self._loaded_version or "unknown")),
        }

    def explain(self, text: str, num_features: int = 8) -> list[dict[str, Any]]:
        self._load_active_bundle()
        if not text or not text.strip():
            return []
        if self._bundle is None:
            return []
        try:
            from lime.lime_text import LimeTextExplainer

            dept_pipe = self._bundle["dept_pipeline"]
            class_names = [str(c) for c in dept_pipe.classes_]
            explainer = LimeTextExplainer(class_names=class_names)
            probs = dept_pipe.predict_proba([text])[0]
            top_label = int(np.argmax(probs))
            exp = explainer.explain_instance(
                text,
                dept_pipe.predict_proba,
                num_features=num_features,
                labels=(top_label,),
                num_samples=250,
            )
            return [
                {"term": str(word), "weight": round(float(weight), 4)}
                for word, weight in exp.as_list(label=top_label)
            ]
        except Exception as exc:
            logger.warning("LIME explanation failed, falling back to linear coefficients: %s", exc)
            pred = self.predict(text)
            return pred.get("top_terms", [])


_CLASSIFIER_SINGLETON: HybridMLClassifier | None = None
_CLASSIFIER_MODELS_DIR: str | None = None


def get_classifier() -> HybridMLClassifier:
    global _CLASSIFIER_SINGLETON, _CLASSIFIER_MODELS_DIR
    current_dir = get_env_str("ML_MODELS_DIR", str(PROJECT_ROOT / "ml" / "models"))
    if _CLASSIFIER_SINGLETON is None or _CLASSIFIER_MODELS_DIR != current_dir:
        _CLASSIFIER_SINGLETON = HybridMLClassifier(Path(current_dir))
        _CLASSIFIER_MODELS_DIR = current_dir
    else:
        _CLASSIFIER_SINGLETON._load_active_bundle()
    return _CLASSIFIER_SINGLETON


def reload_classifier() -> None:
    global _CLASSIFIER_SINGLETON
    if _CLASSIFIER_SINGLETON is not None:
        _CLASSIFIER_SINGLETON.reload()
    else:
        get_classifier()


def predict_complaint(text: str, department_hint: str | None = None) -> dict[str, Any]:
    return get_classifier().predict(text, department_hint=department_hint)


def explain_with_lime(text: str, num_features: int = 8) -> list[dict[str, Any]]:
    return get_classifier().explain(text, num_features=num_features)
