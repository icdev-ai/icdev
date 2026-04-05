#!/usr/bin/env python3
# CUI // SP-CTI
"""DataBridge PostgreSQL optimizations — indexes, parallel query, tuning.

Creates indexes optimized for DataBridge query patterns on PostgreSQL.
Safe to run multiple times (IF NOT EXISTS).

Usage:
    python tools/db/pg_optimize_databridge.py --apply --json
    python tools/db/pg_optimize_databridge.py --verify --json
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Indexes to create (table, index_name, columns)
DATABRIDGE_INDEXES = [
    ("db_sync_log", "idx_db_sync_log_synced", "synced_at DESC"),
    ("db_sync_log", "idx_db_sync_log_conn_time", "connection_id, synced_at DESC"),
    ("db_forge_connectors", "idx_db_forge_connectors_status", "status"),
    ("db_forge_validations", "idx_db_forge_val_conn", "connector_id"),
    ("cf_siem_events", "idx_cf_siem_events_sev_type", "severity, event_type"),
    ("cf_siem_events", "idx_cf_siem_events_ts", "timestamp DESC"),
    ("cf_cost_records", "idx_cf_cost_period", "period_start DESC"),
    ("cf_cost_records", "idx_cf_cost_service", "service_name"),
    ("cf_runbook_executions", "idx_cf_runbook_exec_rb", "runbook_id"),
    ("db_messages", "idx_db_messages_conn", "connection_id"),
]


def apply_indexes() -> dict:
    """Create DataBridge indexes on PostgreSQL."""
    import os
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("ICDEV_PG_HOST", "localhost"),
        port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
        user=os.environ.get("ICDEV_PG_USER", "icdev"),
        password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
        dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
    )
    conn.autocommit = True
    cur = conn.cursor()

    created = 0
    skipped = 0
    errors = []

    for table, idx_name, columns in DATABRIDGE_INDEXES:
        try:
            # Check table exists
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                (table,),
            )
            if not cur.fetchone():
                skipped += 1
                continue
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})"  # nosec B608
            )
            created += 1
        except Exception as e:
            errors.append({"table": table, "index": idx_name, "error": str(e)[:100]})

    # PG tuning
    try:
        cur.execute("ALTER DATABASE icdev SET max_parallel_workers_per_gather = 4")
    except Exception:
        pass

    cur.close()
    conn.close()

    return {
        "indexes_created": created,
        "skipped": skipped,
        "errors": errors,
    }


def verify() -> dict:
    """Verify DataBridge indexes exist."""
    import os
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("ICDEV_PG_HOST", "localhost"),
        port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
        user=os.environ.get("ICDEV_PG_USER", "icdev"),
        password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
        dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
    )
    cur = conn.cursor()
    cur.execute(
        "SELECT indexname, tablename FROM pg_indexes WHERE schemaname = 'public' "
        "AND indexname LIKE 'idx_db_%' OR indexname LIKE 'idx_cf_%' "
        "ORDER BY tablename, indexname"
    )
    indexes = [{"index": r[0], "table": r[1]} for r in cur.fetchall()]
    cur.close()
    conn.close()

    return {"databridge_indexes": len(indexes), "indexes": indexes}


def main():
    parser = argparse.ArgumentParser(description="DataBridge PG Optimizer")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.apply:
        result = apply_indexes()
    elif args.verify:
        result = verify()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            if k != "indexes" and k != "errors":
                print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
