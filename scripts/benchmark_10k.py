"""
10,000+ Row Database Scaling & Latency Benchmark.
Creates an isolated temporary SQLite database, seeds 10,500 complaints across
multiple districts and departments, verifies index usage, and measures key
read/pagination/analytics latencies in milliseconds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_benchmark(n_rows: int = 10500) -> dict:
    tmp_dir = tempfile.mkdtemp(prefix="cgs_bench10k_")
    saved_db = os.environ.get("CGS_DB_PATH")
    saved_up = os.environ.get("CGS_UPLOADS_DIR")
    os.environ["CGS_DB_PATH"] = os.path.join(tmp_dir, "bench10k.db")
    os.environ["CGS_UPLOADS_DIR"] = os.path.join(tmp_dir, "uploads")
    os.environ["CGS_SEED_PASSWORD"] = "BenchSeed@2026"

    try:
        import core.analytics_geo as geo_mod  # noqa: E402
        import core.auth as auth_mod  # noqa: E402
        import core.complaints as comp_mod  # noqa: E402
        import core.database as db_mod  # noqa: E402
        import core.departments as dept_mod  # noqa: E402
        import core.performance as perf_mod  # noqa: E402

        db_mod.init_db()
        db_mod.seed_demo_accounts()
        sa = auth_mod.login("superadmin", "BenchSeed@2026")
        depts = [d["id"] for d in dept_mod.get_active_departments()]
        cit = auth_mod.register("bench_cit", "Benchmark Citizen", "Pass@1234", phone="9000011111")

        with db_mod.get_connection() as conn, conn:
            conn.execute("INSERT OR IGNORE INTO states (id, name, code, is_active) VALUES (1, 'Andhra Pradesh', 'AP', 1)")
            for d_id, d_name in ((1, "Guntur"), (2, "Krishna"), (3, "Visakhapatnam"), (4, "Chittoor")):
                conn.execute(
                    "INSERT OR IGNORE INTO districts (id, state_id, name, is_active) VALUES (?, 1, ?, 1)",
                    (d_id, d_name),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO areas (id, district_id, name, latitude, longitude, is_active) "
                    "VALUES (?, ?, ?, 16.5, 80.6, 1)",
                    (d_id, d_id, f"{d_name} Central"),
                )

            base_dt = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
            rng = random.Random(42)
            statuses = ("Awaiting Assignment", "Assigned", "In Progress", "Resolved", "Closed")
            priorities = ("High", "Medium", "Low")
            batch = []
            for i in range(n_rows):
                c_dt = base_dt + timedelta(hours=i // 2)
                st = statuses[i % len(statuses)]
                res_dt = (c_dt + timedelta(hours=rng.randint(2, 60))).strftime("%Y-%m-%d %H:%M:%S") if st in ("Resolved", "Closed") else None
                d_id = (i % 4) + 1
                dept_id = depts[i % len(depts)]
                prio = priorities[i % 3]
                batch.append((
                    f"CIV-B{i:07d}",
                    cit["id"],
                    f"Synthetic civic benchmark complaint #{i} for pipeline and road maintenance",
                    f"Area {d_id} Street {i % 50}",
                    dept_id,
                    1,
                    d_id,
                    d_id,
                    f"52200{d_id}",
                    16.5 + (i % 10) * 0.001,
                    80.6 + (i % 10) * 0.001,
                    prio,
                    st,
                    (c_dt + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S"),
                    res_dt,
                    c_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    c_dt.strftime("%Y-%m-%d %H:%M:%S"),
                ))

            t_ins0 = time.perf_counter()
            conn.executemany(
                """
                INSERT INTO complaints
                    (incident_id, user_id, text, location, department_id, state_id, district_id,
                     area_id, pincode, latitude, longitude, priority, status, due_at, resolved_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            insert_ms = round((time.perf_counter() - t_ins0) * 1000.0, 2)

        # Verify query plan uses composite index
        with db_mod.get_connection() as conn:
            plan_rows = conn.execute(
                "EXPLAIN QUERY PLAN SELECT id, incident_id, status FROM complaints "
                "WHERE is_deleted = 0 AND district_id = 1 ORDER BY created_at DESC LIMIT 25 OFFSET 0"
            ).fetchall()
            plan_txt = " | ".join(str(tuple(r)) for r in plan_rows)

            t0 = time.perf_counter()
            page_rows = conn.execute(
                "SELECT id, incident_id, status, priority, created_at FROM complaints "
                "WHERE is_deleted = 0 AND district_id = 1 ORDER BY created_at DESC LIMIT 25 OFFSET 250"
            ).fetchall()
            sql_page_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        t1 = time.perf_counter()
        all_rows = comp_mod.get_complaints(sa)
        service_list_ms = round((time.perf_counter() - t1) * 1000.0, 2)

        t2 = time.perf_counter()
        pub = geo_mod.get_public_transparency_by_district()
        public_agg_ms = round((time.perf_counter() - t2) * 1000.0, 2)

        t3 = time.perf_counter()
        perf = perf_mod.get_performance_metrics(sa)
        perf_agg_ms = round((time.perf_counter() - t3) * 1000.0, 2)

        return {
            "rows_seeded": len(all_rows),
            "bulk_insert_ms": insert_ms,
            "sql_indexed_page25_ms": sql_page_ms,
            "sql_page_count": len(page_rows),
            "service_get_complaints_10k_ms": service_list_ms,
            "public_transparency_agg_ms": public_agg_ms,
            "performance_metrics_agg_ms": perf_agg_ms,
            "districts_computed": len(pub["visible_districts"]),
            "depts_computed": len(perf["by_department"]),
            "query_plan": plan_txt,
        }
    finally:
        if saved_db is None:
            os.environ.pop("CGS_DB_PATH", None)
        else:
            os.environ["CGS_DB_PATH"] = saved_db
        if saved_up is None:
            os.environ.pop("CGS_UPLOADS_DIR", None)
        else:
            os.environ["CGS_UPLOADS_DIR"] = saved_up
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    res = run_benchmark(10500)
    for k, v in res.items():
        print(f"{k}: {v}")
