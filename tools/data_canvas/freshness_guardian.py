#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Data Design Canvas — Freshness Guardian.

Compares the last-modified timestamp of each profiled table against
configurable freshness thresholds and produces a staleness report.

Thresholds (configurable via args/data_canvas_config.yaml):
  stale_hours: 24      # warn if last_modified > 24h ago
  critical_hours: 168  # critical if > 7 days ago

Usage:
  python -m tools.data_canvas.freshness_guardian --db data/mydb.sqlite --output-json
  python -m tools.data_canvas.freshness_guardian --profile-json out.json --output-json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "icdev.db"
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "data_canvas_config.yaml"

_DEFAULT_STALE_HOURS = 24
_DEFAULT_CRITICAL_HOURS = 168


def _load_thresholds() -> tuple[int, int]:
    try:
        import yaml
        if _CONFIG_PATH.exists():
            cfg = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            fg = cfg.get("freshness_guardian", {})
            return (
                int(fg.get("stale_hours", _DEFAULT_STALE_HOURS)),
                int(fg.get("critical_hours", _DEFAULT_CRITICAL_HOURS)),
            )
    except Exception:
        pass
    return _DEFAULT_STALE_HOURS, _DEFAULT_CRITICAL_HOURS


def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _sqlite_last_modified(db_path: str, table: str) -> datetime | None:
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        # SQLite has no native last-modified per table; use file mtime as proxy
        conn.close()
        mtime = Path(db_path).stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    except Exception:
        return None


def check_table_freshness(
    table_name: str,
    last_modified: str | None,
    row_count: int = 0,
    stale_hours: int = _DEFAULT_STALE_HOURS,
    critical_hours: int = _DEFAULT_CRITICAL_HOURS,
) -> dict[str, Any]:
    """Evaluate freshness for a single table.

    Args:
        table_name: Name of the table.
        last_modified: ISO-8601 timestamp of last write (may be None).
        row_count: Number of rows in the table.
        stale_hours: Hours before flagging as stale.
        critical_hours: Hours before flagging as critical.

    Returns:
        {table, status, last_modified, age_hours, row_count, message}
    """
    now = datetime.now(timezone.utc)
    dt = _parse_ts(last_modified)

    if dt is None:
        return {
            "table": table_name,
            "status": "unknown",
            "last_modified": None,
            "age_hours": None,
            "row_count": row_count,
            "message": "No last_modified timestamp available.",
        }

    age_hours = (now - dt).total_seconds() / 3600
    if age_hours >= critical_hours:
        status = "critical"
        message = f"Table not updated in {age_hours:.1f}h (critical threshold: {critical_hours}h)."
    elif age_hours >= stale_hours:
        status = "stale"
        message = f"Table not updated in {age_hours:.1f}h (stale threshold: {stale_hours}h)."
    else:
        status = "fresh"
        message = f"Table updated {age_hours:.1f}h ago — within freshness threshold."

    return {
        "table": table_name,
        "status": status,
        "last_modified": dt.isoformat(),
        "age_hours": round(age_hours, 2),
        "row_count": row_count,
        "message": message,
    }


def check_profile_freshness(profile: dict, stale_hours: int | None = None, critical_hours: int | None = None) -> dict[str, Any]:
    """Run freshness checks across all tables in a profile_database result.

    Args:
        profile: Output of data_profiler.profile_database().
        stale_hours: Override stale threshold (defaults to config/24h).
        critical_hours: Override critical threshold (defaults to config/168h).

    Returns:
        {
            "db_name": str,
            "checked_at": ISO-8601,
            "stale_hours": int,
            "critical_hours": int,
            "tables": [check_table_freshness results...],
            "summary": {"fresh": N, "stale": N, "critical": N, "unknown": N},
            "overall_status": "fresh|stale|critical|unknown",
        }
    """
    cfg_stale, cfg_critical = _load_thresholds()
    sh = stale_hours if stale_hours is not None else cfg_stale
    ch = critical_hours if critical_hours is not None else cfg_critical

    tables = profile.get("tables", [])
    results = []
    for tbl in tables:
        tname = tbl.get("table_name", "?")
        last_mod = tbl.get("last_modified") or tbl.get("profiled_at")
        row_count = tbl.get("row_count", 0)
        results.append(check_table_freshness(tname, last_mod, row_count, sh, ch))

    summary: dict[str, int] = {"fresh": 0, "stale": 0, "critical": 0, "unknown": 0}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    if summary["critical"] > 0:
        overall = "critical"
    elif summary["stale"] > 0:
        overall = "stale"
    elif summary["unknown"] == len(results) and len(results) > 0:
        overall = "unknown"
    else:
        overall = "fresh"

    return {
        "db_name": profile.get("db_name", "unknown"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "stale_hours": sh,
        "critical_hours": ch,
        "tables": results,
        "summary": summary,
        "overall_status": overall,
    }


def save_freshness_run(result: dict, design_id: str | None = None) -> str:
    """Persist freshness check result to dd_freshness_runs."""
    import uuid
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(result)

    try:
        from tools.db.storage import get_connection
        conn = get_connection()
    except Exception:
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))

    try:
        try:
            conn.execute(
                "INSERT INTO dd_freshness_runs (run_id, design_id, overall_status, result_json, run_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, design_id, result.get("overall_status", "unknown"), payload, now),
            )
        except Exception:
            conn.execute(
                "INSERT INTO dd_freshness_runs (run_id, design_id, overall_status, result_json, run_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, design_id, result.get("overall_status", "unknown"), payload, now),
            )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return run_id


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="DDC Freshness Guardian — check table staleness")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", metavar="SQLITE_PATH", help="SQLite DB to check (all tables)")
    src.add_argument("--profile-json", metavar="FILE", help="data_profiler JSON output file")
    ap.add_argument("--stale-hours", type=int, default=None, help="Stale threshold in hours")
    ap.add_argument("--critical-hours", type=int, default=None, help="Critical threshold in hours")
    ap.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    args = ap.parse_args()

    if args.profile_json:
        try:
            with open(args.profile_json, encoding="utf-8") as fh:
                profile = json.load(fh)
        except Exception as exc:
            print(f"ERROR reading profile JSON: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        # Generate a minimal profile from the SQLite file
        from tools.data_canvas.data_profiler import profile_database
        conn_params = {"db_type": "sqlite", "path": args.db}
        profile = profile_database(conn_params)

    result = check_profile_freshness(profile, args.stale_hours, args.critical_hours)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        s = result["summary"]
        print(f"[freshness_guardian] {result['db_name']} — overall: {result['overall_status'].upper()}")
        print(f"  fresh={s['fresh']}  stale={s['stale']}  critical={s['critical']}  unknown={s['unknown']}")
        for tbl in result["tables"]:
            if tbl["status"] != "fresh":
                print(f"  [{tbl['status'].upper():8s}] {tbl['table']}: {tbl['message']}")


if __name__ == "__main__":
    main()
