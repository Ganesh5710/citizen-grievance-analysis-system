# Phase 0 Audit & Gap Check (`docs/gap_check.md`)

**Audit Date:** 2026-11-28  
**Audited Model Version:** `20261128_084902` (`LinearSVC_Calibrated`)

---

## 1. Workflow & Access-Control Bypass Fixes

| Item | Initial Audit Status | Resolution Implemented |
|---|---|---|
| Legacy `Pending` + `_pick_least_loaded_employee()` path in `submit_complaint()` | **PRESENT BYPASS** (district-less calls auto-assigned & set `Pending`) | **FIXED:** `submit_complaint()` now strictly requires a valid `district_id` / location (raising `ValueError` otherwise), removes `_pick_least_loaded_employee()` auto-assignment during submission and department transfer, and always enters `Awaiting Assignment` while surfacing only a suggested employee to Admins via `get_suggested_employee()`. |
| Unconditional `claim_complaint()` allowing employees outside Admin assignment | **PRESENT BYPASS** | **FIXED:** `districts.allow_self_claim` added via non-destructive migration (default `0` / OFF). `claim_complaint()` is blocked with `PermissionError` unless an Admin/Super Admin explicitly enables self-claim for that district and the employee matches department + jurisdiction. |

---

## 2. ML Audit Matrix

| Requirement | Initial Audit Status | Resolution / Evidence |
|---|---|---|
| `train_and_evaluate()` compares Naive Bayes, Logistic Regression, calibrated Linear SVM, Random Forest with stratified CV & template-grouped held-out test set | **PRESENT** | Uses `StratifiedGroupKFold(n_splits=5)` grouped by `template_group` on 8,700 rows (`6,960` train / `1,740` held-out unseen-template test, `528` unique `template_group` IDs). Compares `MultinomialNB`, `LogisticRegression`, `LinearSVC_Calibrated`, and `RandomForest`. |
| Accuracy, precision, recall, macro F1, confusion-matrix PNGs, training and inference time | **PARTIALLY MISSING** (Accuracy, Macro-F1, Weighted-F1 & PNGs were present; **macro precision, macro recall, fit time & inference latency** were missing in `model_comparison` & `evaluation_report.md`) | **FIXED:** Added `test_macro_precision`, `test_macro_recall`, `train_time_sec`, `inference_time_ms`, and `inference_ms_per_sample` to `ml/trainer.py` and re-ran `scripts/train.py` (`version 20261128_084902`). |
| Ablation study (text cleaning, stop words, char n-grams, TF-IDF settings) | **PARTIALLY MISSING** (`ml/trainer.py` previously ablated only `word`/`char`/`tfidf_only`/`both`, missing `clean_text`, `stop_words`, unigrams vs bigrams, and `sublinear_tf`) | **FIXED:** Expanded `_evaluate_task()` to ablate 8 variants: `word_unigram_1_1`, `word_bigram_1_2`, `char_wb_3_5`, `stop_words_english`, `clean_text_enabled`, `tfidf_raw_tf`, `tfidf_only`, and `both`. Also fixed `model/preprocessing.py` to remove blocking online `nltk.download()` calls. |
| Per-language metrics (English, Telugu, Hindi, mixed) with honest bias discussion | **PRESENT** (stale 5,500-row numbers in report) | Updated `docs/evaluation_report.md` with `en` (`983`), `hi` (`350`), `te` (`259`), `mixed` (`148`) metrics from the 8,700-row run and bilingual bias/transliteration notes. |
| Synthetic dataset of 5,000+ complaints, clearly labeled synthetic | **PRESENT** | `data/synthetic_complaints.csv` contains **8,700** rows, all marked `is_synthetic=True`. |
| `tests/gold_priority_cases.csv` with 300+ hand-written cases, never used for training or tuning | **PRESENT** | `339` hand-written multilingual cases; verified `0` case overlap with `data/synthetic_complaints.csv`. |
| `docs/evaluation_report.md` and `docs/priority_evaluation.md` | **PRESENT** | Updated `docs/evaluation_report.md` to match latest `20261128_084902` metadata. `docs/priority_evaluation.md` remains current. |

---

## 3. Data Leakage Investigation (> / = 100% Check)

- **ML Classifier Metrics (Synthetic Held-Out & Gold Set):**
  - Held-out Department (`LinearSVC_Calibrated`): Accuracy = **0.9224**, Macro-F1 = **0.9174** (< 100%).
  - Held-out Priority (`LinearSVC_Calibrated`): Accuracy = **0.8402**, Macro-F1 = **0.8367** (< 100%).
  - Gold Set ML-Only Priority: Accuracy = **0.8466** (< 100%).
  - **Result:** No ML metric equals 100%; template-grouped splits (`0` shared `template_group` IDs between train and test) prevented leakage.
- **Note on Deterministic Rules (`nlp/duration.py`):** The 100.00% (`231/231`) reported in `docs/priority_evaluation.md` Section 1 applies strictly to deterministic regex/unit parser verification against clear specification cases (`High` recall on the hybrid engine also hit `1.0000` only because all gold `High` cases match emergency keywords or `>= 7d` duration rules, at a tradeoff of `0.8954` precision). No ML weights were fit on gold rows.
