#!/usr/bin/env python3
# CUI // SP-CTI
"""Comprehensive PostgreSQL optimization — indexes, JSONB, partitioning prep.

Applies all PG-specific optimizations across the full ICDEV™ schema.
Safe to run multiple times (IF NOT EXISTS, idempotent).

Categories:
    1. Composite indexes (project_id + created_at DESC)
    2. Missing FK/filter indexes
    3. Partial indexes for hot-path filters
    4. JSONB conversion for JSON-as-TEXT columns
    5. Covering indexes for dashboard queries
    6. Parallel query tuning

Usage:
    python tools/db/pg_optimize_all.py --apply --json
    python tools/db/pg_optimize_all.py --verify --json
    python tools/db/pg_optimize_all.py --stats --json
"""

import argparse
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _get_pg():
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("ICDEV_PG_HOST", "localhost"),
        port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
        user=os.environ.get("ICDEV_PG_USER", "icdev"),
        password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
        dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
    )
    conn.autocommit = True
    return conn


# ---------------------------------------------------------------------------
# Index definitions: (table, index_name, columns, condition)
# condition=None for regular indexes, string for partial indexes
# ---------------------------------------------------------------------------
INDEXES = [
    # --- Category 1: Composite indexes (project_id + time) ---
    ("audit_trail", "idx_audit_project_created", "project_id, created_at DESC", None),
    ("hook_events", "idx_hook_project_created", "project_id, created_at DESC", None),
    ("agent_token_usage", "idx_token_project_created", "project_id, created_at DESC", None),
    ("innovation_signals", "idx_innosig_created", "created_at DESC", None),
    # --- Category 2: Missing FK/filter indexes ---
    ("research_regulatory_map", "idx_res_regmap_challenge", "challenge_id", None),
    ("research_capability_map", "idx_res_capmap_challenge", "challenge_id", None),
    ("research_build_buy", "idx_res_bb_challenge", "challenge_id", None),
    ("research_challenges", "idx_res_chal_severity", "severity", None),
    ("creative_pain_points", "idx_creat_pain_created", "created_at DESC", None),
    ("sync_jobs", "idx_sync_jobs_status", "status", None),
    ("ai_use_case_inventory", "idx_ai_inv_created", "created_at DESC", None),
    ("project_controls", "idx_projctrl_impl_status", "implementation_status", None),
    ("cpmp_clins", "idx_cpmp_clins_contract", "contract_id", None),
    ("cpmp_deliverables", "idx_cpmp_deliv_contract", "contract_id", None),
    # --- Category 3: Partial indexes (hot-path filters) ---
    ("ai_appeals", "idx_ai_appeals_open", "id", "status IN ('open', 'pending_review')"),
    ("ai_incident_log", "idx_ai_incidents_critical", "created_at DESC", "severity IN ('critical', 'high')"),
    ("review_board_findings", "idx_rbf_unfixed", "severity, created_at DESC", "fix_applied = 0"),
    # --- Category 5: Covering indexes (dashboard queries) ---
    # PG INCLUDE syntax for covering indexes
]

# Covering indexes use INCLUDE (PG 11+ only)
COVERING_INDEXES = [
    ("projects", "idx_projects_status_cover", "status", "name, created_at, classification"),
]


def apply_optimizations() -> dict:
    """Apply all PG optimizations."""
    conn = _get_pg()
    cur = conn.cursor()

    results = {"indexes_created": 0, "skipped": 0, "errors": [], "jsonb_converted": 0}

    # --- Regular + Partial indexes ---
    for table, idx_name, columns, condition in INDEXES:
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                (table,),
            )
            if not cur.fetchone():
                results["skipped"] += 1
                continue

            if condition:
                sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns}) WHERE {condition}"  # nosec B608
            else:
                sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})"  # nosec B608
            cur.execute(sql)
            results["indexes_created"] += 1
        except Exception as e:
            err = str(e).strip().split("\n")[0][:120]
            if "already exists" in err:
                results["indexes_created"] += 1
            else:
                results["errors"].append({"table": table, "index": idx_name, "error": err})

    # --- Covering indexes (PG 11+) ---
    for table, idx_name, key_cols, include_cols in COVERING_INDEXES:
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                (table,),
            )
            if not cur.fetchone():
                results["skipped"] += 1
                continue

            sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({key_cols}) INCLUDE ({include_cols})"  # nosec B608
            cur.execute(sql)
            results["indexes_created"] += 1
        except Exception as e:
            err = str(e).strip().split("\n")[0][:120]
            if "already exists" not in err:
                results["errors"].append({"table": table, "index": idx_name, "error": err})

    # --- JSONB conversion for high-value columns ---
    jsonb_conversions = [
        ("innovation_signals", "score_breakdown"),
        ("innovation_signals", "metadata"),
        ("innovation_trends", "keywords"),
        ("innovation_trends", "metadata"),
        ("research_signals", "keywords"),
        ("research_signals", "metadata"),
        ("research_challenges", "score_breakdown"),
        ("research_challenges", "keywords"),
        ("creative_feature_gaps", "score_breakdown"),
    ]

    for table, column in jsonb_conversions:
        try:
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                (table,),
            )
            if not cur.fetchone():
                continue
            # Check current type
            cur.execute(
                "SELECT data_type FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                (table, column),
            )
            row = cur.fetchone()
            if row and row[0] == "text":
                # Drop default first (TEXT defaults can't auto-cast to JSONB)
                try:
                    cur.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT")  # nosec B608
                except Exception:
                    pass
                cur.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} "  # nosec B608
                    f"TYPE jsonb USING COALESCE({column}::jsonb, '{{}}'::jsonb)"
                )
                # Re-add default as JSONB
                cur.execute(
                    f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT '{{}}'::jsonb"  # nosec B608
                )
                results["jsonb_converted"] += 1
        except Exception as e:
            err = str(e).strip().split("\n")[0][:120]
            results["errors"].append({"table": table, "column": column, "error": err})

    # --- PG tuning ---
    try:
        cur.execute("ALTER DATABASE icdev SET max_parallel_workers_per_gather = 4")
        cur.execute("ALTER DATABASE icdev SET effective_cache_size = '1GB'")
        cur.execute("ALTER DATABASE icdev SET random_page_cost = 1.1")
        cur.execute("ALTER DATABASE icdev SET work_mem = '64MB'")
    except Exception:
        pass

    cur.close()
    conn.close()
    return results


def verify() -> dict:
    """Verify optimization state."""
    conn = _get_pg()
    cur = conn.cursor()

    # Count all custom indexes
    cur.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname NOT LIKE '%_pkey'")
    total_indexes = cur.fetchone()[0]

    # Count JSONB columns
    cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = 'public' AND data_type = 'jsonb'")
    jsonb_cols = cur.fetchone()[0]

    # Check PG settings
    cur.execute("SHOW max_parallel_workers_per_gather")
    parallel = cur.fetchone()[0]

    cur.execute("SHOW work_mem")
    work_mem = cur.fetchone()[0]

    cur.close()
    conn.close()

    return {
        "total_custom_indexes": total_indexes,
        "jsonb_columns": jsonb_cols,
        "parallel_workers": parallel,
        "work_mem": work_mem,
    }


def get_stats() -> dict:
    """Get PG performance statistics."""
    conn = _get_pg()
    cur = conn.cursor()

    # Table sizes
    cur.execute("""
        SELECT relname, n_live_tup,
               pg_size_pretty(pg_total_relation_size(relid)) as total_size
        FROM pg_stat_user_tables
        WHERE n_live_tup > 0
        ORDER BY n_live_tup DESC
        LIMIT 20
    """)
    tables = [{"table": r[0], "rows": r[1], "size": r[2]} for r in cur.fetchall()]

    # Index usage
    cur.execute("""
        SELECT indexrelname, idx_scan, idx_tup_read
        FROM pg_stat_user_indexes
        WHERE idx_scan > 0
        ORDER BY idx_scan DESC
        LIMIT 15
    """)
    index_usage = [{"index": r[0], "scans": r[1], "tuples_read": r[2]} for r in cur.fetchall()]

    # Database size
    cur.execute("SELECT pg_size_pretty(pg_database_size('icdev'))")
    db_size = cur.fetchone()[0]

    cur.close()
    conn.close()

    return {
        "database_size": db_size,
        "top_tables": tables,
        "top_indexes_by_usage": index_usage,
    }


def main():
    parser = argparse.ArgumentParser(description="Comprehensive PG Optimizer")
    parser.add_argument("--apply", action="store_true", help="Apply all optimizations")
    parser.add_argument("--verify", action="store_true", help="Verify optimization state")
    parser.add_argument("--stats", action="store_true", help="PG performance stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.apply:
        result = apply_optimizations()
    elif args.verify:
        result = verify()
    elif args.stats:
        result = get_stats()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            if not isinstance(v, list):
                print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
