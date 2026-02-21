#!/usr/bin/env python3
# CUI // SP-CTI
"""Proactive Heartbeat Daemon — periodically checks for actionable items (D141-D142).

Polls on a configurable interval and runs 7 check functions against the ICDEV
database.  Each check detects a specific class of overdue / stale / failing
items and fans notifications to the audit trail, SSE dashboard, and (optionally)
the remote-command gateway.

Usage:
    python tools/monitor/heartbeat_daemon.py              # Run as daemon
    python tools/monitor/heartbeat_daemon.py --once       # Single pass then exit
    python tools/monitor/heartbeat_daemon.py --check agent_health --json
    python tools/monitor/heartbeat_daemon.py --status     # Latest results
"""

import argparse
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
MEMORY_DB_PATH = BASE_DIR / "data" / "memory.db"

sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Shutdown flag (module-level for signal handler)
# ---------------------------------------------------------------------------
_shutdown_requested = False


def _signal_handler(signum: int, frame: Any) -> None:  # noqa: ANN401
    """Handle shutdown signals gracefully."""
    global _shutdown_requested
    print(f"\nINFO: Received signal {signum}, initiating graceful shutdown...")
    _shutdown_requested = True


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def _generate_id() -> str:
    """Return a short unique ID."""
    return uuid.uuid4().hex[:12]


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    return conn


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Self-initialising DB table
# ---------------------------------------------------------------------------
def _ensure_table(db_path: Optional[Path] = None) -> None:
    """Create the ``heartbeat_checks`` table if it does not exist."""
    conn = _get_connection(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heartbeat_checks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                check_type  TEXT    NOT NULL,
                last_run    TEXT    NOT NULL,
                next_run    TEXT    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','ok','warning','critical','error')),
                result_summary TEXT,
                items_found INTEGER DEFAULT 0,
                duration_ms INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "base_interval_seconds": 60,
    "notification_sinks": {
        "audit_trail": True,
        "sse_broadcast": True,
        "gateway_channels": False,
    },
    "checks": {
        "cato_evidence": {"enabled": True, "interval_seconds": 3600},
        "agent_health": {
            "enabled": True,
            "interval_seconds": 300,
            "stale_threshold_seconds": 600,
        },
        "cve_sla": {"enabled": True, "interval_seconds": 1800},
        "pending_intake": {
            "enabled": True,
            "interval_seconds": 7200,
            "idle_threshold_hours": 48,
        },
        "failing_tests": {
            "enabled": True,
            "interval_seconds": 900,
            "lookback_hours": 24,
        },
        "expiring_isas": {
            "enabled": True,
            "interval_seconds": 86400,
            "expiry_warning_days": 90,
        },
        "memory_maintenance": {
            "enabled": True,
            "interval_seconds": 86400,
            "stale_days": 90,
        },
    },
}


def _load_config() -> dict:
    """Load heartbeat config from ``args/monitoring_config.yaml``.

    Falls back to ``DEFAULT_CONFIG`` when the file is missing or ``pyyaml``
    is unavailable.
    """
    config_path = BASE_DIR / "args" / "monitoring_config.yaml"
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return dict(DEFAULT_CONFIG)

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        heartbeat = raw.get("heartbeat")
        if isinstance(heartbeat, dict):
            # Merge defaults for any missing keys
            merged = dict(DEFAULT_CONFIG)
            merged.update(heartbeat)
            merged_checks = dict(DEFAULT_CONFIG["checks"])
            for key, val in heartbeat.get("checks", {}).items():
                if isinstance(val, dict):
                    base = dict(merged_checks.get(key, {}))
                    base.update(val)
                    merged_checks[key] = base
            merged["checks"] = merged_checks
            return merged
        return dict(DEFAULT_CONFIG)
    except Exception:
        return dict(DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Notification fan-out (D142)
# ---------------------------------------------------------------------------
def _notify(
    check_type: str,
    severity: str,
    title: str,
    details: dict,
    db_path: Optional[Path] = None,
) -> None:
    """Fan out notification to configured sinks.

    1. Audit trail  (always, best-effort)
    2. SSE broadcast (best-effort HTTP POST to dashboard)
    3. Gateway mailbox broadcast (if configured, best-effort)
    """
    event_type = (
        "heartbeat_check_critical" if severity == "critical"
        else "heartbeat_check_warning"
    )

    # --- 1. Audit trail ---------------------------------------------------
    try:
        from tools.audit.audit_logger import log_event  # type: ignore[import-untyped]

        log_event(
            event_type=event_type,
            actor="heartbeat-daemon",
            action=title,
            details=details,
            db_path=db_path,
        )
    except Exception:
        pass  # best-effort

    # --- 2. SSE broadcast --------------------------------------------------
    try:
        payload = json.dumps({
            "event_type": event_type,
            "check_type": check_type,
            "severity": severity,
            "title": title,
            "details": details,
            "timestamp": _utcnow_iso(),
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:5000/api/events/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)  # noqa: S310
    except (urllib.error.URLError, OSError, ValueError):
        pass  # dashboard may not be running

    # --- 3. Gateway mailbox broadcast --------------------------------------
    try:
        from tools.agent.mailbox import broadcast  # type: ignore[import-untyped]

        broadcast(
            sender_id="heartbeat-daemon",
            subject=f"[{severity.upper()}] {title}",
            body=json.dumps(details),
        )
    except (ImportError, Exception):
        pass  # gateway / mailbox not available


# ---------------------------------------------------------------------------
# Check functions (7)
# ---------------------------------------------------------------------------
def check_cato_evidence(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Check for overdue cATO evidence (older than 24 h by default)."""
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                """SELECT id, control_id, evidence_type, collected_at
                   FROM cato_evidence
                   WHERE collected_at < datetime('now', '-24 hours')
                   ORDER BY collected_at ASC"""
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        count = len(items)
        status = "critical" if count > 0 else "ok"
        return {"status": status, "count": count, "items": items[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"table not found or error: {exc}"}


def check_agent_health(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect agents whose ``last_heartbeat`` is stale."""
    threshold = 600
    if config and isinstance(config, dict):
        threshold = config.get("stale_threshold_seconds", threshold)
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                """SELECT agent_id, name, last_heartbeat
                   FROM agents
                   WHERE last_heartbeat < datetime('now', ? || ' seconds')""",
                (str(-threshold),),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        count = len(items)
        status = "critical" if count > 0 else "ok"
        return {"status": status, "count": count, "items": items[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"table not found or error: {exc}"}


def check_cve_sla(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect CVE triage entries that have breached their SLA window.

    SLA periods: critical=7d, high=30d, medium=90d, low=180d.
    """
    sla_days = {"critical": 7, "high": 30, "medium": 90, "low": 180}
    try:
        conn = _get_connection(db_path)
        try:
            overdue: list = []
            for severity, days in sla_days.items():
                rows = conn.execute(
                    """SELECT id, cve_id, component, severity, created_at
                       FROM cve_triage
                       WHERE status != 'resolved'
                         AND severity = ?
                         AND created_at < datetime('now', ? || ' days')
                       ORDER BY created_at ASC""",
                    (severity, str(-days)),
                ).fetchall()
                overdue.extend([dict(r) for r in rows])
        finally:
            conn.close()
        count = len(overdue)
        status = "critical" if count > 0 else "ok"
        return {"status": status, "count": count, "items": overdue[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"table not found or error: {exc}"}


def check_pending_intake(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect intake sessions idle beyond the configured threshold."""
    idle_hours = 48
    if config and isinstance(config, dict):
        idle_hours = config.get("idle_threshold_hours", idle_hours)
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                """SELECT session_id, customer_name, customer_org, updated_at
                   FROM intake_sessions
                   WHERE session_status = 'active'
                     AND updated_at < datetime('now', ? || ' hours')
                   ORDER BY updated_at ASC""",
                (str(-idle_hours),),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        count = len(items)
        status = "warning" if count > 0 else "ok"
        return {"status": status, "count": count, "items": items[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"table not found or error: {exc}"}


def check_failing_tests(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect unresolved failures within the lookback window."""
    lookback = 24
    if config and isinstance(config, dict):
        lookback = config.get("lookback_hours", lookback)
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                """SELECT id, failure_type, error_summary, created_at
                   FROM failure_log
                   WHERE resolved = 0
                     AND created_at > datetime('now', ? || ' hours')
                   ORDER BY created_at DESC""",
                (str(-lookback),),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        count = len(items)
        status = "warning" if count > 0 else "ok"
        return {"status": status, "count": count, "items": items[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"table not found or error: {exc}"}


def check_expiring_isas(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect active ISA agreements expiring within the warning window."""
    days = 90
    if config and isinstance(config, dict):
        days = config.get("expiry_warning_days", days)
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                """SELECT id, partner_org, expiry_date, status
                   FROM isa_agreements
                   WHERE status = 'active'
                     AND expiry_date < datetime('now', '+' || ? || ' days')
                   ORDER BY expiry_date ASC""",
                (str(days),),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        count = len(items)
        status = "warning" if count > 0 else "ok"
        return {"status": status, "count": count, "items": items[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"table not found or error: {exc}"}


def check_memory_maintenance(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect stale memory entries + flush auto-capture buffer (D181)."""
    stale_days = 90
    if config and isinstance(config, dict):
        stale_days = config.get("stale_days", stale_days)
    mem_path = MEMORY_DB_PATH
    if db_path and db_path != DB_PATH:
        # Allow overriding for tests; assume memory.db lives next to icdev.db
        mem_path = db_path.parent / "memory.db"

    items = []

    # D181: Flush auto-capture buffer as first step
    try:
        from tools.memory.auto_capture import flush_buffer, buffer_status
        buf = buffer_status(db_path=mem_path)
        if buf.get("total_buffered", 0) > 0:
            flush_result = flush_buffer(db_path=mem_path)
            items.append({
                "type": "buffer_flush",
                "flushed": flush_result.get("flushed", 0),
                "duplicates": flush_result.get("duplicates", 0),
            })
    except (ImportError, Exception):
        pass  # auto_capture not available

    # Original: detect stale entries
    try:
        conn = sqlite3.connect(str(mem_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT m.id, m.content_type, m.created_at,
                          MAX(a.accessed_at) AS last_accessed
                   FROM memory_entries m
                   LEFT JOIN memory_access_log a ON a.entry_id = m.id
                   GROUP BY m.id
                   HAVING last_accessed IS NULL
                      OR last_accessed < datetime('now', ? || ' days')
                   ORDER BY last_accessed ASC
                   LIMIT 50""",
                (str(-stale_days),),
            ).fetchall()
        finally:
            conn.close()
        stale_items = [dict(r) for r in rows]
        items.extend(stale_items[:20])
        count = len(items)
        status = "warning" if count > 0 else "ok"
        return {"status": status, "count": count, "items": items}
    except Exception as exc:
        return {"status": "ok", "count": len(items), "items": items,
                "note": f"table not found or error: {exc}"}


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------
CHECK_REGISTRY: Dict[str, Callable] = {
    "cato_evidence": check_cato_evidence,
    "agent_health": check_agent_health,
    "cve_sla": check_cve_sla,
    "pending_intake": check_pending_intake,
    "failing_tests": check_failing_tests,
    "expiring_isas": check_expiring_isas,
    "memory_maintenance": check_memory_maintenance,
}


# ---------------------------------------------------------------------------
# Result recording
# ---------------------------------------------------------------------------
def _record_check_result(
    check_type: str,
    result: dict,
    duration_ms: int,
    interval: int,
    db_path: Optional[Path] = None,
) -> None:
    """Persist a check result into ``heartbeat_checks``."""
    now = _utcnow_iso()
    next_run = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    # Approximate next_run by adding interval seconds
    from datetime import timedelta

    next_dt = datetime.now(timezone.utc) + timedelta(seconds=interval)
    next_run = next_dt.strftime("%Y-%m-%dT%H:%M:%S")

    conn = _get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO heartbeat_checks
               (check_type, last_run, next_run, status, result_summary, items_found, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                check_type,
                now,
                next_run,
                result.get("status", "error"),
                json.dumps(result.get("items", [])[:5]),
                result.get("count", 0),
                duration_ms,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
def run_single_check(
    check_type: str,
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Run a named check, record the result, and notify on warnings/criticals."""
    if check_type not in CHECK_REGISTRY:
        return {"error": f"Unknown check: {check_type}. Valid: {list(CHECK_REGISTRY.keys())}"}

    cfg = config or _load_config()
    check_cfg = cfg.get("checks", {}).get(check_type, {})
    interval = check_cfg.get("interval_seconds", cfg.get("base_interval_seconds", 60))

    fn = CHECK_REGISTRY[check_type]
    start = time.monotonic()
    result = fn(config=check_cfg, db_path=db_path)
    duration_ms = int((time.monotonic() - start) * 1000)

    result["check_type"] = check_type
    result["timestamp"] = _utcnow_iso()
    result["duration_ms"] = duration_ms

    # Persist
    _ensure_table(db_path)
    _record_check_result(check_type, result, duration_ms, interval, db_path)

    # Notify on non-ok
    if result.get("status") in ("warning", "critical"):
        title = f"{check_type}: {result['status'].upper()} ({result.get('count', 0)} items)"
        _notify(
            check_type=check_type,
            severity=result["status"],
            title=title,
            details=result,
            db_path=db_path,
        )

    return result


def run_all_checks(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Run all enabled checks that are due based on their interval."""
    cfg = config or _load_config()
    checks_config = cfg.get("checks", {})

    _ensure_table(db_path)

    # Fetch last-run timestamps
    last_runs: Dict[str, Optional[str]] = {}
    try:
        conn = _get_connection(db_path)
        try:
            for row in conn.execute(
                """SELECT check_type, MAX(last_run) AS lr
                   FROM heartbeat_checks
                   GROUP BY check_type"""
            ):
                last_runs[row["check_type"]] = row["lr"]
        finally:
            conn.close()
    except Exception:
        pass  # table may not exist yet

    now = datetime.now(timezone.utc)
    results: Dict[str, dict] = {}
    checks_run = 0
    warnings = 0
    criticals = 0

    for name, fn in CHECK_REGISTRY.items():
        check_cfg = checks_config.get(name, {})
        if not check_cfg.get("enabled", True):
            continue

        interval = check_cfg.get("interval_seconds", cfg.get("base_interval_seconds", 60))
        lr = last_runs.get(name)

        if lr is not None:
            try:
                last_dt = datetime.fromisoformat(lr)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                elapsed = (now - last_dt).total_seconds()
                if elapsed < interval:
                    continue
            except (ValueError, TypeError):
                pass  # run it if we cannot parse

        result = run_single_check(name, config=cfg, db_path=db_path)
        results[name] = result
        checks_run += 1

        status = result.get("status", "ok")
        if status == "warning":
            warnings += 1
        elif status == "critical":
            criticals += 1

    return {
        "timestamp": _utcnow_iso(),
        "checks_run": checks_run,
        "warnings": warnings,
        "criticals": criticals,
        "results": results,
    }


def get_check_status(db_path: Optional[Path] = None) -> List[dict]:
    """Return the latest result for each check type."""
    _ensure_table(db_path)
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                """SELECT hc.*
                   FROM heartbeat_checks hc
                   INNER JOIN (
                       SELECT check_type, MAX(id) AS max_id
                       FROM heartbeat_checks
                       GROUP BY check_type
                   ) latest ON hc.id = latest.max_id
                   ORDER BY hc.check_type"""
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------
def daemon_loop(config: dict, db_path: Optional[Path] = None) -> None:
    """Main polling loop — mirrors ``poll_trigger.py`` pattern."""
    global _shutdown_requested
    _shutdown_requested = False

    interval = config.get("base_interval_seconds", 60)

    # Register signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (OSError, AttributeError):
        pass  # SIGTERM unavailable on some Windows builds

    print(f"Heartbeat daemon started. Checking every {interval}s. Ctrl+C to stop.")

    # Initial run
    run_all_checks(config=config, db_path=db_path)

    while not _shutdown_requested:
        # Sleep in 1-second increments for responsive shutdown
        for _ in range(interval):
            if _shutdown_requested:
                break
            time.sleep(1)

        if not _shutdown_requested:
            run_all_checks(config=config, db_path=db_path)

    print("Heartbeat daemon stopped.")


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def _format_human(result: dict) -> str:
    """Format a single check result as a human-readable line."""
    ts = result.get("timestamp", _utcnow_iso())
    ct = result.get("check_type", "unknown")
    status = result.get("status", "ok").upper()
    count = result.get("count", 0)
    note = result.get("note", "")
    suffix = f" ({note})" if note else ""
    return f"[HEARTBEAT] {ts} | {ct}: {status} ({count} issues){suffix}"


def _format_status_human(statuses: List[dict]) -> str:
    """Format the status listing for human output."""
    if not statuses:
        return "[HEARTBEAT] No check results recorded yet."
    lines = ["[HEARTBEAT] Latest check statuses:", ""]
    for s in statuses:
        ct = s.get("check_type", "?")
        st = s.get("status", "?").upper()
        lr = s.get("last_run", "?")
        nr = s.get("next_run", "?")
        items = s.get("items_found", 0)
        dur = s.get("duration_ms", 0)
        lines.append(f"  {ct:25s} {st:10s} items={items}  dur={dur}ms  last={lr}  next={nr}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point for the heartbeat daemon."""
    parser = argparse.ArgumentParser(
        description="ICDEV Heartbeat Daemon (D141) — proactive check loop"
    )
    parser.add_argument("--once", action="store_true", help="Single pass, then exit")
    parser.add_argument("--check", type=str, help="Run a specific check only")
    parser.add_argument("--status", action="store_true", help="Show latest check statuses")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Override DB path")
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    config = _load_config()

    if args.status:
        statuses = get_check_status(db_path=db)
        if args.json_output:
            print(json.dumps(statuses, indent=2, default=str))
        else:
            print(_format_status_human(statuses))
        return

    if args.check:
        result = run_single_check(args.check, config=config, db_path=db)
        if args.json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(_format_human(result))
        return

    if args.once:
        summary = run_all_checks(config=config, db_path=db)
        if args.json_output:
            print(json.dumps(summary, indent=2, default=str))
        else:
            for name, res in summary.get("results", {}).items():
                print(_format_human(res))
            w = summary.get("warnings", 0)
            c = summary.get("criticals", 0)
            run = summary.get("checks_run", 0)
            print(f"\n[HEARTBEAT] {run} checks run: {w} warnings, {c} criticals")
        return

    # Default: daemon mode
    print("CUI // SP-CTI")
    daemon_loop(config=config, db_path=db)


if __name__ == "__main__":
    main()
