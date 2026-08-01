# CUI // SP-CTI
"""ICDEV™ database observability — slow-query + connection-pool health (crx-db-02).

Read-only reporting over PostgreSQL system catalogs:

  1. Slow-query visibility (``pg_stat_statements``): top-N statements by total and
     mean execution time, so operators can see where the database spends its time.
  2. Sequential-scan-heavy tables (``pg_stat_user_tables``): tables read mostly via
     seq-scan rather than index, with a plain-language "review index coverage"
     suggestion — a cheap pointer at missing-index candidates.
  3. Connection-pool health (``pg_stat_activity``): active / idle / idle-in-transaction
     / waiting connection counts plus the age of the oldest idle-in-transaction
     session. The idle-in-transaction age directly guards against the historical
     ``kanban_tasks`` lock-storm failure mode (see memory
     ``kanban-tasks-lock-storm``): a leaked transaction holding ACCESS SHARE locks
     shows up here as a growing idle-in-txn age before it can accumulate into a storm.

Design constraints (per CLAUDE.md guardrails):
  * READ-ONLY. Never mutates state — only SELECTs against system views.
  * PG-primary, PG-native SQL. On SQLite this is a graceful no-op returning a clear
    JSON message rather than an error (SQLite has no shared server nor these views).
  * Never crashes if the ``pg_stat_statements`` extension is not installed — the
    section reports ``available: false`` with a reason instead.
  * Soft-couples to alert routing: an ``alerts`` list is emitted when the
    idle-in-transaction age exceeds ``IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS``. This is
    a data-only hook for crx-not-01 (notification routing) once it exists — this
    module deliberately imports no notification module and blocks on nothing.

CLI::

    python tools/db/query_health.py --json          # full report
    python tools/db/query_health.py --json --top-n 20
    python tools/db/query_health.py                 # human-readable summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Soft-couple alert threshold. crx-not-01 (notification routing) is NOT built
# yet — when it lands, route the emitted ``alerts`` entries through it. Do NOT
# import a notification module here; this constant + the alerts list are the hook.
# ---------------------------------------------------------------------------
IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS: float = float(
    os.environ.get("ICDEV_DB_IDLE_TXN_ALERT_SECONDS", "300")
)

# Default number of statements / tables to report.
DEFAULT_TOP_N: int = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _rows_to_dicts(cursor) -> List[Dict[str, Any]]:
    """Return cursor rows as a list of plain dicts.

    Works whether the underlying cursor uses ``RealDictCursor`` (rows already
    behave like dicts) or a positional cursor (fall back to ``cursor.description``).
    """
    rows = cursor.fetchall()
    if not rows:
        return []
    first = rows[0]
    # RealDictRow / Mapping-like rows
    if isinstance(first, dict) or hasattr(first, "keys"):
        return [dict(r) for r in rows]
    cols = [d[0] for d in (cursor.description or [])]
    return [dict(zip(cols, r)) for r in rows]


def _pg_stat_statements_available(conn) -> bool:
    """True when the ``pg_stat_statements`` view is queryable in this database."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'"
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _pgss_time_columns(conn) -> tuple[str, str] | None:
    """Return the (total_time_col, mean_time_col) names for pg_stat_statements.

    PostgreSQL 13+ renamed ``total_time``/``mean_time`` to
    ``total_exec_time``/``mean_exec_time``. Detect which the installed extension
    version exposes so the query works across major versions.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'pg_stat_statements'"
        )
        cols = {r["column_name"] if isinstance(r, dict) or hasattr(r, "keys")
                else r[0] for r in cur.fetchall()}
    except Exception:
        return None
    if "total_exec_time" in cols and "mean_exec_time" in cols:
        return ("total_exec_time", "mean_exec_time")
    if "total_time" in cols and "mean_time" in cols:
        return ("total_time", "mean_time")
    return None


# ---------------------------------------------------------------------------
# Report sections (each assumes a live PostgreSQL connection)
# ---------------------------------------------------------------------------
def get_slow_queries(conn, top_n: int = DEFAULT_TOP_N) -> Dict[str, Any]:
    """Top-N statements by total and mean execution time from pg_stat_statements.

    Returns ``{available: False, reason: ...}`` when the extension is absent so
    the caller never crashes on a database without pg_stat_statements installed.
    """
    if not _pg_stat_statements_available(conn):
        return {
            "available": False,
            "reason": "pg_stat_statements extension not installed. Enable with: "
                      "CREATE EXTENSION pg_stat_statements; and add it to "
                      "shared_preload_libraries in postgresql.conf (requires restart).",
            "by_total_time": [],
            "by_mean_time": [],
        }
    cols = _pgss_time_columns(conn)
    if cols is None:
        return {
            "available": False,
            "reason": "pg_stat_statements present but expected time columns not found.",
            "by_total_time": [],
            "by_mean_time": [],
        }
    total_col, mean_col = cols

    def _query(order_col: str) -> List[Dict[str, Any]]:
        cur = conn.cursor()
        # Column names are validated (from a fixed allow-list above), never user input.
        cur.execute(
            f"SELECT queryid, calls, "
            f"round({total_col}::numeric, 2) AS total_ms, "
            f"round({mean_col}::numeric, 2) AS mean_ms, "
            f"rows, left(query, 200) AS query "
            f"FROM pg_stat_statements "
            f"ORDER BY {order_col} DESC "
            f"LIMIT %s",
            (top_n,),
        )
        out = _rows_to_dicts(cur)
        # queryid can be a large int / None — normalise to str for stable JSON.
        for r in out:
            if r.get("queryid") is not None:
                r["queryid"] = str(r["queryid"])
        return out

    return {
        "available": True,
        "top_n": top_n,
        "by_total_time": _query(total_col),
        "by_mean_time": _query(mean_col),
    }


def get_seq_scan_tables(conn, top_n: int = DEFAULT_TOP_N) -> Dict[str, Any]:
    """Tables read mostly via sequential scan, with an index-review suggestion."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT relname AS table_name, seq_scan, "
            "COALESCE(idx_scan, 0) AS idx_scan, "
            "COALESCE(n_live_tup, 0) AS live_rows "
            "FROM pg_stat_user_tables "
            "WHERE seq_scan > 0 "
            "ORDER BY seq_scan DESC "
            "LIMIT %s",
            (top_n,),
        )
        rows = _rows_to_dicts(cur)
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "reason": f"pg_stat_user_tables query failed: {exc}",
                "tables": []}

    for r in rows:
        seq = r.get("seq_scan") or 0
        idx = r.get("idx_scan") or 0
        live = r.get("live_rows") or 0
        # Heuristic: large table read predominantly via seq-scan is an index candidate.
        if live >= 1000 and seq > idx:
            r["suggestion"] = (
                "High sequential-scan ratio on a sizeable table — review index "
                "coverage for its common WHERE / JOIN columns."
            )
        else:
            r["suggestion"] = "OK — sequential scans are cheap at this table size."
    return {"available": True, "top_n": top_n, "tables": rows}


def get_pool_health(conn) -> Dict[str, Any]:
    """Connection-pool health from pg_stat_activity + the process-local PG pool.

    Emits an ``alerts`` list (soft-couple hook for crx-not-01) when the oldest
    idle-in-transaction session exceeds IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS.
    """
    alerts: List[Dict[str, Any]] = []
    server: Dict[str, Any] = {}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT state, count(*) AS n "
            "FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "GROUP BY state"
        )
        by_state = {(r.get("state") or "unknown"): int(r.get("n") or 0)
                    for r in _rows_to_dicts(cur)}

        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) AS waiting "
            "FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND wait_event_type = 'Lock'"
        )
        waiting_rows = _rows_to_dicts(cur)
        waiting = int(waiting_rows[0]["waiting"]) if waiting_rows else 0

        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(EXTRACT(EPOCH FROM (now() - min(state_change))), 0) "
            "AS max_age_s "
            "FROM pg_stat_activity "
            "WHERE datname = current_database() "
            "AND state = 'idle in transaction'"
        )
        age_rows = _rows_to_dicts(cur)
        oldest_idle_txn_s = round(float(age_rows[0]["max_age_s"]), 1) if age_rows else 0.0

        server = {
            "active": by_state.get("active", 0),
            "idle": by_state.get("idle", 0),
            "idle_in_transaction": by_state.get("idle in transaction", 0),
            "waiting_on_lock": waiting,
            "oldest_idle_in_txn_seconds": oldest_idle_txn_s,
            "by_state": by_state,
        }

        if oldest_idle_txn_s >= IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS:
            alerts.append({
                "severity": "warning",
                "metric": "oldest_idle_in_txn_seconds",
                "value": oldest_idle_txn_s,
                "threshold": IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS,
                "message": (
                    "A session has been idle-in-transaction beyond the alert "
                    "threshold — a leaked transaction can escalate into a lock "
                    "storm. Investigate and terminate it."
                ),
            })
    except Exception as exc:  # pragma: no cover - defensive
        server = {"available": False, "reason": f"pg_stat_activity query failed: {exc}"}

    # Process-local pool snapshot (best-effort; the pool is per-process).
    pool: Dict[str, Any] = {"initialized": False}
    try:
        from tools.db import storage as _storage
        if getattr(_storage, "_pg_pool", None) is not None:
            p = _storage._pg_pool
            free = len(getattr(p, "_pool", []) or [])
            used = len(getattr(p, "_used", {}) or {})
            pool = {
                "initialized": True,
                "min": getattr(p, "minconn", None),
                "max": getattr(p, "maxconn", None),
                "in_use": used,
                "idle_in_pool": free,
            }
    except Exception:
        pool = {"initialized": False}

    return {"available": True, "server": server, "process_pool": pool, "alerts": alerts}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def collect(top_n: int = DEFAULT_TOP_N) -> Dict[str, Any]:
    """Collect the full DB-observability report.

    Graceful no-op on SQLite (or when PG is unreachable): returns a report with
    ``backend`` set and an ``available: False`` note rather than raising.
    """
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "idle_in_txn_alert_threshold_seconds": IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS,
    }
    try:
        from tools.db.storage import get_connection, is_pg
    except ImportError as exc:
        report.update({"available": False, "backend": "unknown",
                       "reason": f"tools.db.storage import failed: {exc}"})
        return report

    if not is_pg():
        report.update({
            "available": False,
            "backend": "sqlite",
            "reason": "DB observability requires PostgreSQL (pg_stat_statements / "
                      "pg_stat_activity). SQLite is a single-file, single-process "
                      "engine with no shared server, connection pool, or these "
                      "catalog views — nothing to report.",
        })
        return report

    conn = None
    try:
        conn = get_connection()
        # Confirm the live connection is actually PG (get_connection can fall back
        # to SQLite when PG is unreachable).
        if not is_pg(conn):
            report.update({
                "available": False,
                "backend": "sqlite",
                "reason": "PostgreSQL configured but unreachable; connection fell "
                          "back to SQLite. No DB observability available.",
            })
            return report
        report["backend"] = "postgresql"
        report["available"] = True
        report["slow_queries"] = get_slow_queries(conn, top_n)
        report["seq_scan_tables"] = get_seq_scan_tables(conn, top_n)
        report["pool_health"] = get_pool_health(conn)
        # Surface pool alerts at the top level for easy consumption.
        report["alerts"] = report["pool_health"].get("alerts", [])
    except Exception as exc:
        report.update({"available": False,
                       "backend": report.get("backend", "postgresql"),
                       "reason": f"DB observability collection failed: {exc}"})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_human(report: Dict[str, Any]) -> None:
    print(f"ICDEV DB Observability — {report.get('generated_at')}")
    print(f"Backend: {report.get('backend', 'unknown')}")
    if not report.get("available"):
        print(f"Unavailable: {report.get('reason')}")
        return

    sq = report.get("slow_queries", {})
    print("\nSlow queries (pg_stat_statements):")
    if not sq.get("available"):
        print(f"  unavailable — {sq.get('reason')}")
    else:
        print("  Top by total time:")
        for r in sq.get("by_total_time", [])[:5]:
            print(f"    {r.get('total_ms')}ms total / {r.get('calls')} calls: "
                  f"{(r.get('query') or '').strip()[:80]}")

    ss = report.get("seq_scan_tables", {})
    print("\nSequential-scan-heavy tables:")
    for r in ss.get("tables", [])[:5]:
        print(f"    {r.get('table_name')}: seq={r.get('seq_scan')} "
              f"idx={r.get('idx_scan')} rows={r.get('live_rows')} — {r.get('suggestion')}")

    ph = report.get("pool_health", {}).get("server", {})
    print("\nConnection pool / activity:")
    print(f"    active={ph.get('active')} idle={ph.get('idle')} "
          f"idle_in_txn={ph.get('idle_in_transaction')} "
          f"waiting={ph.get('waiting_on_lock')} "
          f"oldest_idle_txn={ph.get('oldest_idle_in_txn_seconds')}s")
    for a in report.get("alerts", []):
        print(f"    ALERT [{a.get('severity')}]: {a.get('message')}")


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ICDEV™ database observability (slow queries + pool health)")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                        help=f"Number of statements/tables to report (default {DEFAULT_TOP_N})")
    args = parser.parse_args(argv)

    report = collect(top_n=args.top_n)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)
    # Read-only reporting tool: exit 0 regardless of DB availability.
    return 0


if __name__ == "__main__":
    sys.exit(main())
