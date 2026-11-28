# ML Evaluation Report — Citizen Grievance Governance Platform

> **Dataset Notice:** All benchmark numbers below are evaluated on `data/synthetic_complaints.csv` (5,500 **100% synthetic** multilingual Hyderabad civic complaints, `is_synthetic=True`, 192 distinct `template_group` phrasings across 6 departments and 4 language modes: English, Telugu, Hindi, and mixed transliterated text).

---

## 1. Split Strategy & Leakage Prevention

- **StratifiedGroupKFold (5 folds)** grouped by `template_group`: all paraphrases derived from the same phrasing template stay strictly in either train or validation/test.
- Held-out test split: **4,421** training rows vs **1,079** unseen-template test rows (`0` overlapping template groups).
- Library versions recorded in metadata (`grievance_ml_20261128_050455.json`): Python 3.14.3, scikit-learn 1.8.0, pandas 3.0.2, numpy 2.4.4, joblib 1.5.3.

---

## 2. Department Classification Benchmark (Unseen Templates)

Features: Combined word n-grams `(1, 2)` (`max_features=6,000`) + character n-grams `char_wb (3, 5)` (`max_features=8,000`).

| Model | CV Acc Mean | CV Macro-F1 (Std) | Held-Out Test Acc | Held-Out Macro-F1 | Weighted-F1 |
|---|---:|---:|---:|---:|---:|
| **RandomForest** | **0.8681** | **0.8649** (0.0409) | 0.8563 | 0.8432 | 0.8584 |
| LinearSVC (Calibrated) | 0.8613 | 0.8546 (0.0528) | **0.8879** | **0.8722** | **0.8880** |
| LogisticRegression | 0.8249 | 0.8165 (0.0610) | 0.8285 | 0.8128 | 0.8298 |
| MultinomialNB | 0.7985 | 0.7919 (0.0845) | 0.8109 | 0.7885 | 0.8058 |

![Model Comparison](evaluation_charts/model_comparison.png)

### Department Confusion Matrix
![Department Confusion Matrix](evaluation_charts/dept_confusion_matrix.png)

---

## 3. Priority Classification Benchmark & Safety Rule

| Model | CV Acc Mean | CV Macro-F1 (Std) | Held-Out Test Acc | Held-Out Macro-F1 | Weighted-F1 |
|---|---:|---:|---:|---:|---:|
| **RandomForest** | **0.7998** | **0.7978** (0.0061) | 0.7813 | 0.7759 | 0.7809 |
| LinearSVC (Calibrated) | 0.7967 | 0.7920 (0.0095) | 0.7794 | 0.7705 | 0.7764 |
| LogisticRegression | 0.7855 | 0.7837 (0.0156) | 0.7748 | 0.7712 | 0.7760 |
| MultinomialNB | 0.7796 | 0.7742 (0.0083) | **0.7831** | 0.7748 | **0.7821** |

- **Production Safety Override:** Emergency keywords (`urgent`, `critical`, `emergency`, `danger`, `fire`, `death`, `blast`, `accident`, `sparking`, `electrocution`, `flood`, `live wire`, etc.) always override predicted priority to **High**.

![Priority Confusion Matrix](evaluation_charts/priority_confusion_matrix.png)

---

## 4. Feature Ablation Study

| Task | Feature Set | Held-Out Test Acc | Held-Out Macro-F1 |
|---|---|---:|---:|
| Department | Word TF-IDF `(1, 2)` only | 0.8758 | 0.8655 |
| Department | Char TF-IDF `char_wb (3, 5)` only | 0.8387 | 0.8243 |
| Department | Combined Word + Char TF-IDF | 0.8563 | 0.8432 |
| Priority | Word TF-IDF `(1, 2)` only | 0.7581 | 0.7640 |
| Priority | Char TF-IDF `char_wb (3, 5)` only | **0.7896** | **0.7849** |
| Priority | Combined Word + Char TF-IDF | 0.7813 | 0.7759 |

- Character n-grams slightly outperformed word-only TF-IDF on noisy priority phrasing (typos such as `urgnt`, prefixes, Telugu/Hindi suffix cues), while clean word n-grams were sharpest on English department terminology. Keeping the union preserves robustness across English, Telugu, Hindi, and mixed transliteration.

---

## 5. Multilingual Bias & Subgroup Evaluation

| Language Slice | Support (n) | Dept Acc | Dept Macro-F1 | Priority Acc | Priority Macro-F1 |
|---|---:|---:|---:|---:|---:|
| `en` (English) | 585 | 0.8598 | 0.8398 | 0.7744 | 0.7704 |
| `te` (Telugu) | 212 | 0.8774 | 0.8627 | 0.7925 | 0.7871 |
| `hi` (Hindi) | 101 | 0.9010 | 0.5960 | 0.7723 | 0.7803 |
| `mixed` (Code-mixed) | 181 | 0.7956 | 0.7214 | 0.7956 | 0.7895 |

- **Fairness Observation:** Hindi (`hi`) achieved high raw accuracy (`0.9010`) on the held-out split, but lower macro-F1 (`0.5960`) because only 101 held-out Hindi samples appeared across fewer template groups, amplifying errors on minority classes (`General / Other`). Mixed transliteration (`mixed`, 79.56% acc) sits ~6 points below `en`/`te` due to spelling variance without strict transliteration normalization. Collecting more real multilingual citizen feedback in Review Queue directly improves future candidate versions.

---

## 6. Error Analysis

Top department confusion pairs on unseen test templates (`grievance_ml_20261128_050455.json`):
1. **Electricity -> General / Other (44):** Held-out substation/pole phrasing lacking frequent keywords (`substation`, `fuse`) landed near generic infrastructure.
2. **General / Other -> Roads (14):** Complaints mentioning `"walkway"` or `"ring road junction"` overlapped lexically with Roads complaints.
3. **Electricity -> Roads (12):** Pole/junction location tokens overlapped with street road templates.
4. **Sanitation / Water -> Roads (8 each):** Flooded lanes and dug-up drains share road-surface vocabulary.

Because citizen department choices are **never blocked** and disagreements / `< 0.60` confidence cases enter the Admin `Review Queue`, ambiguous edge cases receive human oversight and feed into future retraining cycles.
