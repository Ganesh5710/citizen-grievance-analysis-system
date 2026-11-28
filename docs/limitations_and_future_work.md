# System Limitations & Future Work

An honest technical appraisal of current constraints and planned next steps:

## 1. Synthetic Training Data vs Real Historical Corpus
- The offline classifier (`LinearSVC_Calibrated` for Department and `LinearSVC_Calibrated` + rules for Priority) is trained on `data/synthetic_complaints.csv` (8,700 synthetic records grouped by `template_group` under `StratifiedGroupKFold`) and validated against 339 hand-crafted `tests/gold_priority_cases.csv` cases.
- **Limitation:** Real municipal grievance text contains regional slang, abbreviations, and noisy voice-to-text phrasing outside synthetic templates.
- **Mitigation & Future Work:** The ML Review Queue continuously logs human Admin overrides so candidate models can be retrained exclusively on verified production tickets once sufficient real volume accumulates.

## 2. SQLite Concurrency vs Live PostgreSQL Testing
- **Limitation:** Local validation ran against SQLite in `WAL` mode (`busy_timeout=5000`), which serializes concurrent write transactions inside one OS node. While `core/database.py` now includes a unified `DATABASE_URL` abstraction (`PostgresConnectionAdapter` with SQL placeholder and DDL translation), no external PostgreSQL server process was spun up locally during automated testing.
- **Future Work:** Run full integration tests against live PostgreSQL 16 in CI (using the service defined in `docker-compose.yml`), add Alembic schema migrations, and use `pg_dump` backups for production multi-worker clusters.

## 3. Offline Embedding & Translation Fallbacks
- **Limitation:** When `sentence-transformers` is absent (offline environment without `paraphrase-multilingual-MiniLM-L12-v2` cached weights), duplicate detection falls back gracefully to character/word TF-IDF cosine similarity (`tfidf_fallback`), which handles English and transliterated overlap strongly but has lower cross-script semantic recall (e.g., Telugu script matched against English description).
- **Future Work:** Pre-bundle quantized ONNX multilingual embeddings inside the Docker image for air-gapped cross-lingual semantic matching, plus a dedicated UI localization switcher (`hi`/`te`) using pre-reviewed resource strings and an automated "Show Original" staff translation toggle (using `langdetect`/`deep_translator`).

## 4. India Post Pincode-to-Area Granularity
- **Limitation:** In `data/india_post_pincodes_ap.csv` (`data.gov.in`, 10,681 rows across 26 AP districts), one 6-digit pincode often spans multiple post offices/villages, and the public CSV omits a `mandal` column.
- **Mitigation:** Pincode lookup populates State and District automatically and asks the citizen to confirm the specific Area/Village, using optional map coordinates or area centroids when GPS pins are omitted. Future integration with official LGD (Local Government Directory) mandal-village shapefiles will fill the optional `mandals` tier cleanly.

## 5. Concurrent HTTP Load Execution & Additional Future Signals
- **Limitation:** While SQLite 10,500-row database benchmarks ran directly (`6.46 ms` indexed pagination), headless multi-user HTTP load testing via `scripts/load_test.py` (`locust`) was authored but not executed against a running multi-replica server in this session.
- **Future Work:** Execute Locust suites against staging containers, add lightweight computer-vision verification on uploaded civic photos (pothole/garbage/wire relevance check), and surface urgency/distress sentiment signals inside the Admin triage queue.
