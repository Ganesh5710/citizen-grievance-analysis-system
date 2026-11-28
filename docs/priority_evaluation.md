# Priority & Duration Evaluation Report

**Evaluated Model Version:** `20261128_054732` (`LinearSVC_Calibrated` priority, `LinearSVC_Calibrated` department)  
**Gold Test Set:** `tests/gold_priority_cases.csv` (339 hand-written cases; **never used in training or threshold tuning**)  
**ML Leakage Check (100% ML Score Flag):** Clean (< 100% ML score, no leakage detected)

---

## 1. Deterministic Duration Extraction & Pure Rule Mapping

| Component | Passed | Total | Accuracy | Notes |
|---|---:|---:|---:|---|
| Clear Duration Extraction (`nlp/duration.py`) | 231 | 231 | 100.00% | Numeric, words (1-30), Devanagari/Telugu digits, ranges, idioms |
| Pure Duration -> Priority Mapping | 231 | 231 | 100.00% | `core/priority_rules.py` + `config/priority_rules.yaml` |
| Negative Context Rejection | 52 | 52 | 100.00% | Salary, tickets, festivals, future calls, repaired-then-broke |
| Recurring Pattern Flagging | 19 | 19 | 100.00% | Frequency expressions trigger +1 level without duration misparse |
| Ambiguous Duration Handling | 18 | 18 | 100.00% | 18/18 flagged with confidence < 0.70 for review |

---

## 2. Final Hybrid Priority vs ML-Only Priority (Full Gold Set)

| Model / Engine | Gold Accuracy | Gold Macro-F1 | High F1 | Medium F1 | Low F1 |
|---|---:|---:|---:|---:|---:|
| **Final Hybrid Priority Engine** | **0.8702** | **0.8582** | 0.9448 | 0.7817 | 0.8482 |
| **ML-Only Priority Model** | 0.8466 | 0.8470 | 0.8548 | 0.8089 | 0.8774 |

*(Gold Department Classification Accuracy = `0.8230`, Macro-F1 = `0.8141`)*

### Per-Class Precision, Recall & F1 (Final Hybrid Engine)

| Class | Precision | Recall | F1-Score | Support |
|---|---:|---:|---:|---:|
| **High** | 0.8954 | 1.0000 | 0.9448 | 137 |
| **Medium** | 0.7404 | 0.8280 | 0.7817 | 93 |
| **Low** | 0.9878 | 0.7431 | 0.8482 | 109 |

### Final Hybrid Confusion Matrix

| True \\ Pred | High | Medium | Low |
|---|---:|---:|---:|
| **High** | 137 | 0 | 0 |
| **Medium** | 15 | 77 | 1 |
| **Low** | 1 | 27 | 81 |

### ML-Only Confusion Matrix

| True \\ Pred | High | Medium | Low |
|---|---:|---:|---:|
| **High** | 103 | 25 | 9 |
| **Medium** | 1 | 91 | 1 |
| **Low** | 0 | 16 | 93 |

### Breakdown by Language

| Language | Support | Hybrid Acc | Hybrid Macro-F1 | ML-Only Acc | ML-Only Macro-F1 |
|---|---:|---:|---:|---:|---:|
| `en` | 150 | 0.8733 | 0.8569 | 0.8600 | 0.8620 |
| `hi` | 63 | 0.8889 | 0.8707 | 0.8254 | 0.8224 |
| `mixed` | 63 | 0.8413 | 0.8363 | 0.8730 | 0.8754 |
| `te` | 63 | 0.8730 | 0.8679 | 0.8095 | 0.8097 |

---

## 3. Model Version Comparison on Gold Set

| Version | Dept Model | Priority Model | Gold Hybrid Acc | Gold Hybrid F1 | Gold ML-Only Acc | Gold ML-Only F1 |
|---|---|---|---:|---:|---:|---:|
| `20261128_050455` | RandomForest | RandomForest | 0.7935 | 0.7648 | 0.3333 | 0.3150 |
| `20261128_054732` | LinearSVC_Calibrated | LinearSVC_Calibrated | 0.8702 | 0.8582 | 0.8466 | 0.8470 |

---

## 4. Open List of Failures & Root Causes

### Clear Duration Extractor Failures (0)
- None (100% of clear gold cases extracted accurately).

### Negative Context False Positives (0)
- None.

### Hybrid Priority Mismatches on Gold Set (44)
- Row #3 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "Traffic signal stuck on red for 4 hrs near central bridge" _(Reason: Medium: duration for 4 hrs -> Low (rule), ML said Low, keyword Medium.)_
- Row #5 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "Garbage bin spilled onto alley for 6 hrs near post office" _(Reason: Medium: duration for 6 hrs -> Low (rule), ML said Low, keyword Medium.)_
- Row #13 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "Sewer overflow began at corner since last night" _(Reason: Medium: duration since last night -> Low (rule), ML said Low, keyword Medium.)_
- Row #14 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "Pothole waterlogging started on service road since morning" _(Reason: Medium: duration since morning -> Low (rule), ML said Low, keyword Medium.)_
- Row #15 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "Traffic jam backing up at toll plaza since last night" _(Reason: Medium: duration since last night -> Low (rule), ML said Low, keyword Medium.)_
- Row #134 (`clear`, `mixed`): expected **Low**, predicted **Medium** (ML=Low) — "45 mins se traffic signal red pe stuck hai" _(Reason: Medium: duration 45 mins se -> Low (rule), ML said Low, keyword Medium.)_
- Row #135 (`clear`, `mixed`): expected **Low**, predicted **Medium** (ML=Low) — "subah se garbage bin overflow avutundi" _(Reason: Medium: duration subah se -> Low (rule), ML said Low, keyword Medium.)_
- Row #176 (`recurring`, `en`): expected **Medium**, predicted **High** (ML=Medium) — "Water supply cuts out every week in upper block" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #177 (`recurring`, `en`): expected **Medium**, predicted **High** (ML=Medium) — "Garbage truck skips our street twice a month" _(Reason: High: ML said Medium, keyword Medium, recurring pattern (+1 from Medium).)_
- Row #178 (`recurring`, `en`): expected **Medium**, predicted **High** (ML=Medium) — "Voltage drops every evening and daily around seven" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #179 (`recurring`, `en`): expected **Medium**, predicted **High** (ML=Medium) — "Traffic jams happen every morning at ferry junction" _(Reason: High: ML said Medium, keyword Medium, recurring pattern (+1 from Medium).)_
- Row #180 (`recurring`, `en`): expected **Medium**, predicted **High** (ML=Medium) — "Noise loudspeakers repeat weekly near temple grounds" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #181 (`recurring`, `te`): expected **Medium**, predicted **High** (ML=Medium) — "ప్రతి వారం మా వీధిలో మంచినీరు నిలిచిపోతోంది" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #182 (`recurring`, `te`): expected **Medium**, predicted **High** (ML=Medium) — "ప్రతి రోజు ఉదయం ట్రాఫిక్ సిగ్నల్ వద్ద జామ్ అవుతోంది" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #183 (`recurring`, `te`): expected **Medium**, predicted **High** (ML=Medium) — "ప్రతి నెల రెండుసార్లు డ్రైనేజీ పొంగుతోంది" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #184 (`recurring`, `hi`): expected **Medium**, predicted **High** (ML=Medium) — "हर हफ्ते बिजली ट्रांसफार्मर ट्रिप हो जाता है" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #185 (`recurring`, `hi`): expected **Medium**, predicted **High** (ML=Medium) — "हर दिन सुबह कचरा गाड़ी बिना उठाए निकल जाती है" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #186 (`recurring`, `hi`): expected **Medium**, predicted **High** (ML=Medium) — "हर महीने चौराहे पर पार्किंग की समस्या लौट आती है" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #187 (`recurring`, `mixed`): expected **Medium**, predicted **High** (ML=Medium) — "har hafte water pressure down avutundi colony lo" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #188 (`recurring`, `mixed`): expected **Medium**, predicted **High** (ML=Medium) — "prathi roju evening street lights off ho jati hain" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #189 (`recurring`, `mixed`): expected **Medium**, predicted **High** (ML=Medium) — "twice a month drainage block avutundi main lane lo" _(Reason: High: ML said Medium, recurring pattern (+1 from Medium).)_
- Row #194 (`negative`, `en`): expected **Low**, predicted **Medium** (ML=Medium) — "Please call me after 3 days regarding streetlight pole" _(Reason: Medium: ML said Medium.)_
- Row #199 (`negative`, `en`): expected **Low**, predicted **Medium** (ML=Medium) — "Warranty of 1 year expired on public solar street lamp" _(Reason: Medium: ML said Medium.)_
- Row #203 (`negative`, `te`): expected **Low**, predicted **Medium** (ML=Medium) — "3 రోజుల పండుగ తర్వాత చెత్త కుప్పలు శుభ్రం చేయలేదు" _(Reason: Medium: ML said Medium.)_
- Row #204 (`negative`, `te`): expected **Low**, predicted **Medium** (ML=Medium) — "3 రోజుల క్రితం బాగు చేసిన రోడ్డు మళ్లీ పాడైంది" _(Reason: Medium: ML said Medium.)_
- Row #205 (`negative`, `te`): expected **Low**, predicted **Medium** (ML=Medium) — "వచ్చే 2 రోజుల తర్వాత వచ్చి ట్రాన్స్‌ఫార్మర్ చూడండి" _(Reason: Medium: ML said Medium.)_
- Row #206 (`negative`, `hi`): expected **Low**, predicted **Medium** (ML=Medium) — "3 दिन की सैलरी कटने पर भी सफाई नहीं हुई" _(Reason: Medium: ML said Medium.)_
- Row #209 (`negative`, `hi`): expected **Low**, predicted **Medium** (ML=Medium) — "3 दिन पहले नल ठीक हुआ था पर आज फिर टूट गया" _(Reason: Medium: ML said Medium.)_
- Row #211 (`negative`, `mixed`): expected **Low**, predicted **Medium** (ML=Medium) — "paid 4 days wages for private water tanker issue" _(Reason: Medium: ML said Medium, keyword Medium.)_
- Row #236 (`negative`, `en`): expected **Low**, predicted **Medium** (ML=Medium) — "Broken streetlight pole makes walkway completely dark" _(Reason: Medium: ML said Medium.)_
- Row #239 (`negative`, `en`): expected **Low**, predicted **Medium** (ML=Medium) — "Abandoned scooter gathering rust outside public library" _(Reason: Medium: ML said Medium.)_
- Row #241 (`negative`, `te`): expected **Low**, predicted **Medium** (ML=Medium) — "రోడ్డుపై తారు పోయి కంకర రాళ్లు తేలాయి" _(Reason: Medium: ML said Medium.)_
- Row #242 (`negative`, `te`): expected **Low**, predicted **Medium** (ML=Medium) — "వీధి దీపం వెలగక చీకటిగా మారింది దారి" _(Reason: Medium: ML said Medium.)_
- Row #246 (`negative`, `hi`): expected **Low**, predicted **Medium** (ML=Medium) — "नल से पानी का दबाव कम आ रहा है मोहल्ले में" _(Reason: Medium: ML said Medium.)_
- Row #248 (`negative`, `hi`): expected **Low**, predicted **Medium** (ML=Medium) — "बिजली के खंभे की स्ट्रीट लाइट खराब है" _(Reason: Medium: ML said Medium.)_
- Row #252 (`negative`, `mixed`): expected **Low**, predicted **Medium** (ML=Medium) — "tap water muddy ga vastundi near post office" _(Reason: Medium: ML said Medium.)_
- Row #255 (`negative`, `mixed`): expected **Medium**, predicted **Low** (ML=Low) — "drainage block hone se bad smell aa rahi hai" _(Reason: Low: ML said Low.)_
- Row #256 (`negative`, `mixed`): expected **Low**, predicted **Medium** (ML=Medium) — "illegal auto parking blocking narrow turn" _(Reason: Medium: ML said Medium.)_
- Row #283 (`clear`, `mixed`): expected **Low**, predicted **Medium** (ML=Low) — "four hrs se traffic light dark hai station pe" _(Reason: Medium: duration four hrs se -> Low (rule), ML said Low, keyword Medium.)_
- Row #288 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "twenty-five minutes since traffic light failed at gate" _(Reason: Medium: duration twenty-five minutes -> Low (rule), ML said Low, keyword Medium.)_
- Row #302 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "twenty-two hours garbage van missing from route" _(Reason: Medium: duration twenty-two hours -> Low (rule), ML said Low, keyword Medium.)_
- Row #303 (`clear`, `en`): expected **Low**, predicted **Medium** (ML=Low) — "16 hours traffic police missing from school crossing" _(Reason: Medium: duration 16 hours -> Low (rule), ML said Low, keyword Medium.)_
- Row #321 (`clear`, `en`): expected **Low**, predicted **High** (ML=Low) — "half an hour water outage near hospital wing" _(Reason: High: emergency keyword ('hospital') override, duration half an hour -> Low (rule), ML said Low, keyword High.)_
- Row #322 (`clear`, `en`): expected **Medium**, predicted **High** (ML=High) — "half a week open drain stinking near block F" _(Reason: High: duration half a week -> Medium (rule), ML said High.)_

---

## 5. Known Limitations

- **Slang, emojis & sarcasm:** Sarcastic or idiomatic expressions (e.g., *"roads like the moon since forever"*) fall outside fixed regex/number-word lexicons and rely on the ML classifier or ambiguous-confidence fallback (`review_queue`).
- **Regional transliteration spellings:** Unseen Romanized phonetic spellings (e.g., `rojuu`, `dinnn`, `mahinee`) may miss unit dictionaries and log to `duration_extraction_logs` for rule expansion.
- **Negative cases without duration:** On complaints with no valid duration (`negative`), priority relies on ML + keyword signals, where ML probability differences between `Low` and `Medium` cause remaining confusion.
