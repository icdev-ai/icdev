#!/usr/bin/env python3
"""Migrate data from data/fathomdesk.db (SQLite) into the main ICDEV database.

Copies all ad_*, kg_*, and trading_daemon_* tables from the legacy fathomdesk.db
SQLite file into the main ICDEV database (SQLite or PostgreSQL).

- SQLite destination: uses ATTACH DATABASE for maximum throughput.
- PostgreSQL destination: reads rows from fathomdesk.db and inserts via
  get_conn() using INSERT OR IGNORE (ON CONFLICT DO NOTHING).

Idempotent: rows with existing primary keys are skipped.

Usage:
    python tools/db/migrate_fathomdesk.py [--json] [--gate]
    python tools/db/migrate_fathomdesk.py --source data/fathomdesk.db [--json] [--gate]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.trading.db import get_conn  # noqa: E402 — ensures all tables exist first

DEFAULT_SOURCE = ROOT / "data" / "fathomdesk.db"

# Default destination — matches storage.py logic (SQLite only)
DEFAULT_DEST = Path(os.environ.get("ICDEV_DB_PATH", str(ROOT / "data" / "icdev.db")))

# Tables to migrate in dependency order (parents before children)
_MIGRATE_TABLES = [
    "ad_portfolios",
    "ad_positions",
    "ad_orders",
    "ad_analysis_runs",
    "ad_analyst_reports",
    "ad_debate_records",
    "ad_risk_assessments",
    "ad_trade_decisions",
    "ad_pulse_articles",
    "ad_signals",
    "ad_signal_validation",
    "ad_dynamic_weights",
    "ad_macro_context",
    "ad_macro_indicators",
    "ad_macro_sector_impact",
    "ad_economic_events",
    "ad_portfolio_snapshots",
    "ad_position_history",
    "ad_expert_opinions",
    "ad_cis_recommendations",
    "ad_daily_briefs",
    "ad_risk_profiles",
    "ad_scenario_runs",
    "ad_scenario_impacts",
    "ad_scenario_signal_adjustments",
    "ad_cascade_watchlists",
    "ad_graph_cache",
    "ad_transaction_costs",
    "ad_strategy_runs",
    "ad_strategy_holdings",
    "ad_strategy_sector_allocation",
    "ad_strategy_signals",
    "ad_fundamental_metrics",
    "ad_quality_scores",
    "ad_factor_returns",
    "ad_factor_loadings",
    "ad_regime_premiums",
    "ad_alpha_decomposition",
    "kg_graphs",
    "kg_nodes",
    "kg_edges",
    "trading_daemon_audit",
    "trading_daemon_reflex_state",
]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names for a table via PRAGMA."""
    # pg-portability: sqlite-only path — reads columns from the SQLite migration
    # SOURCE (raw sqlite3.Connection); the PG destination uses _dest_columns_pg().
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _source_tables(conn: sqlite3.Connection) -> set[str]:
    # pg-portability: sqlite-only path — enumerates tables in the SQLite migration
    # SOURCE (raw sqlite3.Connection); never runs against a PG connection.
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('sqlite_sequence')"
    ).fetchall()
    return {r[0] for r in rows}


def _dest_columns_pg(conn, table: str) -> list[str]:
    """Return column names for a table from a PostgreSQL connection."""
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def _migrate_sqlite(source_path: Path, dest_path: Path) -> dict:
    """SQLite → SQLite migration via ATTACH DATABASE."""
    dst = sqlite3.connect(str(dest_path), timeout=60)
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")
    dst.execute("PRAGMA busy_timeout=30000")
    dst.execute(f"ATTACH DATABASE '{source_path}' AS src")

    src_check = sqlite3.connect(str(source_path))
    src_tables = _source_tables(src_check)

    results = {}
    errors = []

    for table in _MIGRATE_TABLES:
        if table not in src_tables:
            results[table] = {"status": "skipped", "rows": 0, "reason": "not in source"}
            continue

        src_cols = set(_table_columns(src_check, table))
        dst_cols = set(_table_columns(dst, table))

        if not src_cols or not dst_cols:
            results[table] = {"status": "skipped", "rows": 0, "reason": "no columns"}
            continue

        shared = [c for c in _table_columns(src_check, table) if c in dst_cols]
        if not shared:
            results[table] = {"status": "skipped", "rows": 0, "reason": "no shared columns"}
            continue

        col_list = ", ".join(shared)

        try:
            before = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            dst.execute(
                f"INSERT OR IGNORE INTO main.{table} ({col_list}) "
                f"SELECT {col_list} FROM src.{table}"
            )
            dst.commit()
            after = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            results[table] = {"status": "ok", "rows_inserted": after - before, "rows_after": after}
        except Exception as exc:
            errors.append(f"{table}: {exc}")
            results[table] = {"status": "error", "message": str(exc)}
            try:
                dst.rollback()
            except Exception:
                pass

    src_check.close()
    dst.execute("DETACH DATABASE src")
    dst.close()
    return results, errors


def _migrate_postgresql(source_path: Path) -> tuple[dict, list[str]]:
    """SQLite → PostgreSQL migration via get_conn() row-by-row insert."""
    src = sqlite3.connect(str(source_path))
    src.row_factory = sqlite3.Row
    src_tables = _source_tables(src)

    conn = get_conn()

    results = {}
    errors = []

    for table in _MIGRATE_TABLES:
        if table not in src_tables:
            results[table] = {"status": "skipped", "rows": 0, "reason": "not in source"}
            continue

        src_cols = _table_columns(src, table)
        if not src_cols:
            results[table] = {"status": "skipped", "rows": 0, "reason": "no columns in source"}
            continue

        # Discover destination columns via information_schema
        dst_col_rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        dst_cols = {r[0] for r in dst_col_rows}

        if not dst_cols:
            results[table] = {"status": "skipped", "rows": 0, "reason": "table missing in dest (no columns)"}
            continue

        shared = [c for c in src_cols if c in dst_cols]
        if not shared:
            results[table] = {"status": "skipped", "rows": 0, "reason": "no shared columns"}
            continue

        try:
            before_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            before = before_row[0] if before_row else 0

            rows = src.execute(
                f"SELECT {', '.join(shared)} FROM {table}"
            ).fetchall()

            if rows:
                placeholders = ", ".join(["?" for _ in shared])
                col_list = ", ".join(shared)
                insert_sql = (
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                    f"ON CONFLICT DO NOTHING"
                )
                for row in rows:
                    try:
                        conn.execute(insert_sql, list(row))
                    except Exception:
                        pass
                conn.commit()

            after_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            after = after_row[0] if after_row else 0
            results[table] = {"status": "ok", "rows_inserted": after - before, "rows_after": after}

        except Exception as exc:
            errors.append(f"{table}: {exc}")
            results[table] = {"status": "error", "message": str(exc)}
            try:
                conn.rollback()
            except Exception:
                pass

    src.close()
    conn.close()
    return results, errors


def migrate(source_path: Path, dest_path: Path) -> dict:
    if not source_path.exists():
        return {"status": "error", "message": f"Source not found: {source_path}"}

    # Step 1: ensure all FathomDesk tables exist in destination via get_conn()
    init_conn = get_conn()
    init_conn.commit()
    init_conn.close()

    # Step 2: detect backend
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()

    if backend == "postgresql":
        results, errors = _migrate_postgresql(source_path)
    else:
        results, errors = _migrate_sqlite(source_path, dest_path)

    total_inserted = sum(r.get("rows_inserted", 0) for r in results.values())
    ok_tables = sum(1 for r in results.values() if r["status"] == "ok")
    skipped = sum(1 for r in results.values() if r["status"] == "skipped")

    return {
        "status": "ok" if not errors else "partial",
        "backend": backend,
        "tables_migrated": ok_tables,
        "tables_skipped": skipped,
        "total_rows_inserted": total_inserted,
        "errors": errors,
        "detail": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate fathomdesk.db into main ICDEV database")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Source fathomdesk.db path")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="Destination icdev.db path (SQLite only)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if any errors")
    args = parser.parse_args()

    result = migrate(Path(args.source), Path(args.dest))

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        status = result["status"]
        print(f"Migration {status.upper()} (backend: {result.get('backend', 'sqlite')})")
        print(f"  Tables migrated : {result.get('tables_migrated', 0)}")
        print(f"  Tables skipped  : {result.get('tables_skipped', 0)}")
        print(f"  Rows inserted   : {result.get('total_rows_inserted', 0)}")
        if result.get("errors"):
            print("  Errors:")
            for e in result["errors"]:
                print(f"    - {e}")

    if args.gate and result.get("errors"):
        sys.exit(1)


if __name__ == "__main__":
    main()
