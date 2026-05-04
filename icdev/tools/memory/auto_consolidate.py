#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-40 — Memory auto-consolidation daemon.

Outside Claude Code there is no pre_compact hook to trigger memory cleanup.
Long-running automation accumulates redundant entries with no bound.

This tool runs on a schedule (cron every 6h recommended) and invokes
`MemoryConsolidator.consolidate_all()` when `data/memory.db` exceeds a
configurable size threshold (default 50 MB). Otherwise it is a no-op.

Exit codes:
  0  — OK (consolidated or under threshold — no action needed)
  1  — consolidation ran but execution errored

Usage:
    python tools/memory/auto_consolidate.py --json
    python tools/memory/auto_consolidate.py --threshold-mb 100 --json
    python tools/memory/auto_consolidate.py --force --batch-size 100
    python tools/memory/auto_consolidate.py --dry-run --json

Scheduling (Linux cron — every 6 hours):
    0 */6 * * * /usr/bin/python /opt/icdev/tools/memory/auto_consolidate.py --json \
        >> /var/log/icdev/memory_consolidate.log 2>&1

Scheduling (Windows Task Scheduler):
    schtasks /Create /TN ICDEVMemoryConsolidate /TR \
        "python.exe C:/ICDev/tools/memory/auto_consolidate.py --json" \
        /SC HOURLY /MO 6
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DEFAULT_THRESHOLD_MB = 50
DEFAULT_BATCH_SIZE = 50
# Row-count threshold used when the DB backend is PostgreSQL (no file path to stat).
# 200k rows in memory_entries is roughly equivalent to ~50 MB in SQLite.
DEFAULT_THRESHOLD_ROWS = 200_000


def _memory_db_path() -> Path:
    """Resolve the memory DB path via tools.compat.db_utils (fallback: data/icdev.db).

    Memory tables are consolidated into the main icdev.db / PostgreSQL backend.
    """
    try:
        from tools.compat.db_utils import get_memory_db_path
        return Path(get_memory_db_path())
    except Exception:
        return BASE_DIR / "data" / "icdev.db"


def _db_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _memory_row_count() -> int:
    """Return the total row count across memory_entries for PostgreSQL threshold checks."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM memory_entries")
        row = c.fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _is_postgresql() -> bool:
    """Return True when the active storage backend is PostgreSQL."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        is_pg = getattr(conn, "_backend", "sqlite") == "postgresql"
        conn.close()
        return is_pg
    except Exception:
        return False


def _log_audit(result: dict) -> None:
    """Best-effort audit write. Never crashes the daemon."""
    try:
        from tools.audit.audit_logger import log_event
        log_event(
            event_type="compliance_check",
            actor="memory_auto_consolidate",
            action="memory.auto_consolidate",
            details={
                "threshold_bytes": result.get("threshold_bytes"),
                "db_bytes": result.get("db_bytes"),
                "triggered": result.get("triggered"),
                "summary": result.get("summary"),
                "duration_ms": result.get("duration_ms"),
            },
            classification="CUI",
        )
    except Exception:
        pass


def run(
    *,
    threshold_mb: int = DEFAULT_THRESHOLD_MB,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    force: bool = False,
    write_audit: bool = True,
) -> dict[str, Any]:
    """Check memory store size and consolidate if over threshold (or if --force).

    For PostgreSQL backends, uses row count (DEFAULT_THRESHOLD_ROWS) instead of
    file size because PostgreSQL has no on-disk path to stat.
    """
    started = time.time()
    use_pg = _is_postgresql()

    if use_pg:
        row_count = _memory_row_count()
        threshold_bytes = 0
        size_bytes = 0
        db_path_str = "postgresql://[icdev backend]"
    else:
        db_path = _memory_db_path()
        threshold_bytes = int(threshold_mb) * 1024 * 1024
        size_bytes = _db_size_bytes(db_path)
        row_count = 0
        db_path_str = str(db_path)

    result: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "db_path": db_path_str,
        "db_bytes": size_bytes,
        "db_mb": round(size_bytes / 1024 / 1024, 3),
        "threshold_bytes": threshold_bytes,
        "threshold_mb": threshold_mb,
        "backend": "postgresql" if use_pg else "sqlite",
        "dry_run": dry_run,
        "force": force,
        "triggered": False,
        "summary": None,
    }

    if use_pg:
        result["row_count"] = row_count
        result["threshold_rows"] = DEFAULT_THRESHOLD_ROWS
        should_run = force or row_count >= DEFAULT_THRESHOLD_ROWS
        if not should_run:
            result["skipped"] = f"under row threshold ({row_count} < {DEFAULT_THRESHOLD_ROWS})"
            result["duration_ms"] = int((time.time() - started) * 1000)
            return result
    else:
        if not db_path.exists():  # type: ignore[possibly-undefined]
            result["skipped"] = "memory_db_missing"
            result["duration_ms"] = int((time.time() - started) * 1000)
            return result
        should_run = force or size_bytes >= threshold_bytes
        if not should_run:
            result["skipped"] = f"under threshold ({size_bytes} < {threshold_bytes})"
            result["duration_ms"] = int((time.time() - started) * 1000)
            return result

    result["triggered"] = True

    try:
        from tools.memory.memory_consolidation import MemoryConsolidator
        consolidator = MemoryConsolidator(dry_run=dry_run)
        summary = consolidator.consolidate_all(batch_size=batch_size)
        # summary shape: {"processed": N, "actions": {MERGE, REPLACE, ...}}
        result["summary"] = summary
    except Exception as exc:
        result["error"] = str(exc)[:400]
        result["duration_ms"] = int((time.time() - started) * 1000)
        if write_audit:
            _log_audit(result)
        return result

    result["duration_ms"] = int((time.time() - started) * 1000)
    if write_audit:
        _log_audit(result)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--threshold-mb", type=int, default=DEFAULT_THRESHOLD_MB,
                   help="Trigger consolidation at this DB size (MB)")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                   help="Rows per consolidation pass")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview consolidation without applying changes")
    p.add_argument("--force", action="store_true",
                   help="Consolidate regardless of DB size")
    p.add_argument("--no-audit", action="store_true",
                   help="Skip audit_trail row (useful for tests)")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run(
        threshold_mb=args.threshold_mb,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        force=args.force,
        write_audit=not args.no_audit,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"memory_db: {result['db_path']}  size={result['db_mb']} MB  "
              f"threshold={result['threshold_mb']} MB")
        if result.get("skipped"):
            print(f"  skipped: {result['skipped']}")
        elif result.get("triggered"):
            s = result.get("summary") or {}
            print(f"  triggered (dry_run={result['dry_run']})  "
                  f"processed={s.get('processed')}  actions={s.get('actions')}")
        if result.get("error"):
            print(f"  error: {result['error']}")
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
