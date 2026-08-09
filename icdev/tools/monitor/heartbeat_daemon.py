#!/usr/bin/env python3
# CUI // SP-CTI
"""Proactive Heartbeat Daemon — periodically checks for actionable items (D141-D142).

Polls on a configurable interval and runs 7 check functions against the ICDEV™
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

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Shutdown flag (module-level for signal handler)
# ---------------------------------------------------------------------------
_shutdown_requested = False

# Per-agent dead-since timestamps for A2A health tracking (reset on restart)
_a2a_dead_since: Dict[str, str] = {}


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
    conn = get_connection(db_path=str(path))
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
        "coherence_health": {
            "enabled": True,
            "interval_seconds": 3600,
        },
        "kanban_stale": {
            "enabled": True,
            "interval_seconds": 300,
            "stale_threshold_minutes": 5,
        },
        "a2a_agent_health": {
            "enabled": True,
            "interval_seconds": 300,
            "dead_alert_minutes": 10,
            "request_timeout_seconds": 5,
        },
    },
}

# Default A2A agent stubs for ports 8443-8460 (used when registry is empty)
_A2A_DEFAULT_AGENTS: List[tuple] = [
    ("orchestrator",   "Orchestrator",   8443),
    ("architect",      "Architect",      8444),
    ("builder",        "Builder",        8445),
    ("compliance",     "Compliance",     8446),
    ("security",       "Security",       8447),
    ("infrastructure", "Infrastructure", 8448),
    ("knowledge",      "Knowledge",      8449),
    ("monitor",        "Monitor",        8450),
    ("mbse",           "MBSE",           8451),
    ("modernization",  "Modernization",  8452),
    ("requirements",   "Requirements",   8453),
    ("supply-chain",   "Supply Chain",   8454),
    ("simulation",     "Simulation",     8455),
    ("devsecops",      "DevSecOps",      8456),
    ("zta",            "ZTA",            8457),
    ("gateway",        "Gateway",        8458),
    ("agent-8459",     "Agent-8459",     8459),
    ("agent-8460",     "Agent-8460",     8460),
]


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
    event_type = "heartbeat_check_critical" if severity == "critical" else "heartbeat_check_warning"

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
        payload = json.dumps(
            {
                "event_type": event_type,
                "check_type": check_type,
                "severity": severity,
                "title": title,
                "details": details,
                "timestamp": _utcnow_iso(),
            }
        ).encode("utf-8")
        import os as _os

        _dash_port = _os.environ.get("ICDEV_DASHBOARD_PORT", "5000")
        req = urllib.request.Request(
            f"http://localhost:{_dash_port}/api/events/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)  # noqa: S310  # nosec B310 -- URL scheme validated; internal/configured endpoints only
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
                   WHERE last_heartbeat < datetime('now', %s || ' seconds')""",
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
                         AND severity = %s
                         AND created_at < datetime('now', %s || ' days')
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
                     AND updated_at < datetime('now', %s || ' hours')
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
                     AND created_at > datetime('now', %s || ' hours')
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
                     AND expiry_date < datetime('now', '+' || %s || ' days')
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
            items.append(
                {
                    "type": "buffer_flush",
                    "flushed": flush_result.get("flushed", 0),
                    "duplicates": flush_result.get("duplicates", 0),
                }
            )
    except (ImportError, Exception):
        pass  # auto_capture not available

    # Original: detect stale entries
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT m.id, m.content_type, m.created_at,
                          MAX(a.accessed_at) AS last_accessed
                   FROM memory_entries m
                   LEFT JOIN memory_access_log a ON a.entry_id = m.id
                   GROUP BY m.id
                   HAVING last_accessed IS NULL
                      OR last_accessed < datetime('now', %s || ' days')
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
        return {"status": "ok", "count": len(items), "items": items, "note": f"table not found or error: {exc}"}


def check_coherence_health(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Check 8: Implementation coherence drift detection.

    Runs the coherence checker to detect implementation drift such as
    missing __init__.py files, stale imports, unregistered DB tables,
    or config/code misalignment.
    """
    try:
        from tools.workflow.coherence_checker import run_checks

        report = run_checks()
        if not report.overall_pass:
            return {
                "status": "warning",
                "count": report.failed_checks + report.warned_checks,
                "items": [
                    {
                        "type": "coherence_drift",
                        "failed": report.failed_checks,
                        "warned": report.warned_checks,
                        "passed": report.passed_checks,
                        "total": report.total_checks,
                    }
                ],
            }
        return {
            "status": "ok",
            "count": 0,
            "items": [],
        }
    except Exception as exc:
        return {
            "status": "ok",
            "count": 0,
            "items": [],
            "note": f"Coherence checker unavailable: {exc}",
        }


def check_review_board_health(
    config: dict = None,
    db_path=None,
) -> Dict[str, Any]:
    """Check Review Board daemon health — circuit breakers, critical findings."""
    try:
        conn = _get_connection(db_path)
        try:
            # Check for tripped circuit breakers
            try:
                cb_rows = conn.execute(
                    "SELECT reflex_name FROM review_board_reflex_state WHERE circuit_breaker_open = 1"
                ).fetchall()
            except Exception:
                cb_rows = []

            # Check for unfixed critical findings
            try:
                critical = conn.execute(
                    "SELECT COUNT(*) FROM review_board_findings WHERE severity = 'critical' AND fix_applied = 0"
                ).fetchone()
                critical_count = critical[0] if critical else 0
            except Exception:
                critical_count = 0

            items = []
            for r in cb_rows:
                items.append({"type": "circuit_breaker_open", "reflex": r[0]})
            if critical_count > 0:
                items.append({"type": "critical_findings", "count": critical_count})

            status = "critical" if cb_rows or critical_count > 0 else "ok"
            return {"status": status, "count": len(items), "items": items}
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"Review board tables not available: {exc}"}


# ---------------------------------------------------------------------------
# Kanban stale-scheduler check + wakeup (acw-sched-02)
# ---------------------------------------------------------------------------
def _trigger_kanban_wakeup(db_path: Optional[Path] = None) -> str:  # noqa: ARG001
    """POST to /api/genesis/reflex/kanban to wake the kanban scheduler.

    Falls back to importing and calling the reflex run() directly when the
    dashboard is unreachable.  Returns a short status string.
    """
    import os as _os

    dash_port = _os.environ.get("ICDEV_DASHBOARD_PORT", "5050")
    try:
        payload = json.dumps({"force": False}).encode("utf-8")
        req = urllib.request.Request(
            f"http://localhost:{dash_port}/api/genesis/reflex/kanban",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310  # nosec B310 -- internal configured endpoint
        return "triggered_via_api"
    except (urllib.error.URLError, OSError, ValueError):
        pass

    # NO in-process fallback.
    #
    # This used to call the kanban reflex's run() directly, here, in the
    # heartbeat daemon's own process. The reflex tracks live subprocesses in a
    # MODULE-GLOBAL dict (_running), so a second process sees {} — and its
    # _reap_stale_in_progress then looks at the real scheduler's genuinely-live
    # in_progress rows, finds them "not running", and reaps them to backlog with
    # failure_count++. The real scheduler's next poll finds the DB status
    # changed underneath it and kills the subprocess as "stale-cleanup". Those
    # two reasons together are 51 of 182 recorded task failures on this board.
    #
    # A health check must not become an executor. Report the failure and let
    # check_kanban_genesis_health surface it as a warning instead.
    return "wakeup_unavailable: dashboard unreachable"


def check_kanban_genesis_health(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect a stale kanban reflex and trigger a wakeup if overdue.

    Queries genesis_reflex_state for reflex_name='kanban'.  When last_run_at
    is older than stale_threshold_minutes (default 5), the check fires a wakeup
    via POST /api/genesis/reflex/kanban (falling back to a direct import call)
    and returns status='warning' so the notification fan-out alerts operators.
    """
    stale_minutes = 5
    if config and isinstance(config, dict):
        stale_minutes = config.get("stale_threshold_minutes", stale_minutes)

    try:
        conn = _get_connection(db_path)
        try:
            row = conn.execute(
                """SELECT reflex_name, last_run_at, enabled, circuit_breaker_open
                   FROM genesis_reflex_state
                   WHERE reflex_name = 'kanban'"""
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"genesis_reflex_state unavailable: {exc}"}

    if row is None:
        return {"status": "ok", "count": 0, "items": [], "note": "kanban reflex state not seeded yet"}

    row_dict = dict(row) if hasattr(row, "keys") else {
        "reflex_name": row[0],
        "last_run_at": row[1],
        "enabled": row[2],
        "circuit_breaker_open": row[3],
    }

    if not row_dict.get("enabled", 1) or row_dict.get("circuit_breaker_open", 0):
        return {"status": "ok", "count": 0, "items": [], "note": "kanban reflex disabled or circuit breaker open"}

    last_run_at = row_dict.get("last_run_at")
    if last_run_at is None:
        return {"status": "ok", "count": 0, "items": [], "note": "kanban reflex has never run"}

    try:
        last_dt = datetime.fromisoformat(last_run_at)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed_minutes = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
    except (ValueError, TypeError) as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"could not parse last_run_at: {exc}"}

    if elapsed_minutes < stale_minutes:
        return {"status": "ok", "count": 0, "items": []}

    wakeup = _trigger_kanban_wakeup(db_path=db_path)
    return {
        "status": "warning",
        "count": 1,
        "items": [{
            "reflex": "kanban",
            "last_run_at": last_run_at,
            "elapsed_minutes": round(elapsed_minutes, 1),
            "wakeup": wakeup,
        }],
    }


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------
def check_ace_instance_stale(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect ACE instances stuck in active state past the stale threshold."""
    threshold_minutes = 30
    if config and isinstance(config, dict):
        threshold_minutes = config.get("stale_threshold_minutes", threshold_minutes)
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT id, name, role_id, state, updated_at FROM ace_instances "
                "WHERE state NOT IN ('complete','cancelled','failed') "
                "AND updated_at < datetime('now', %s || ' minutes') "
                "ORDER BY updated_at ASC LIMIT 50",
                (str(-threshold_minutes),),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        for item in items:
            item["result"] = "dead"
            item["component_id"] = item.get("id", "")
        count = len(items)
        return {"status": "critical" if count > 0 else "ok", "count": count, "items": items[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"ace_instances not available: {exc}"}


def check_kanban_stale(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Detect kanban tasks stuck in_progress past the stale threshold."""
    threshold_hours = 4
    if config and isinstance(config, dict):
        threshold_hours = config.get("stale_threshold_hours", threshold_hours)
    try:
        conn = _get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT id, title, status, updated_at FROM kanban_tasks "
                "WHERE status = 'in_progress' "
                "AND updated_at < datetime('now', %s || ' hours') "
                "ORDER BY updated_at ASC LIMIT 50",
                (str(-threshold_hours),),
            ).fetchall()
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        for item in items:
            item["result"] = "dead"
            item["component_id"] = item.get("id", "")
        count = len(items)
        return {"status": "warning" if count > 0 else "ok", "count": count, "items": items[:20]}
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"kanban_tasks not available: {exc}"}


def _build_default_a2a_agents() -> List[dict]:
    """Return stub entries for ports 8443-8460 when the registry returns no agents."""
    return [
        {"id": name, "name": display, "url": f"https://localhost:{port}"}
        for name, display, port in _A2A_DEFAULT_AGENTS
    ]


def _telegram_alert_dead_agents(long_dead: List[dict]) -> None:
    """Best-effort Telegram alert for A2A agents dead beyond the configured threshold."""
    try:
        from tools.notifications.adapters.telegram import send  # type: ignore[import-untyped]

        names = ", ".join(a.get("name") or a.get("agent_id", "?") for a in long_dead)
        mins = long_dead[0].get("dead_minutes", "?")
        body = (
            f"{len(long_dead)} agent(s) unreachable for >{mins:.0f} min: {names}\n"
            f"URLs: {', '.join(a['url'] for a in long_dead)}"
        )
        send(title="A2A Agent Health Alert", body=body, severity="critical")
    except (ImportError, Exception):
        pass  # best-effort


def check_a2a_agent_health(
    config: Optional[dict] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """HEAD-check each registered A2A agent's /.well-known/agent.json endpoint.

    Iterates over active agents from the A2A registry (falls back to default
    ports 8443-8460 when the registry is empty). Dead agents are reported with
    result='dead'; alive agents with result='alive'. Sends a Telegram alert
    when any agent has been continuously dead for >= dead_alert_minutes.
    """
    global _a2a_dead_since

    dead_alert_minutes = 10
    request_timeout = 5
    if config and isinstance(config, dict):
        dead_alert_minutes = config.get("dead_alert_minutes", dead_alert_minutes)
        request_timeout = config.get("request_timeout_seconds", request_timeout)

    try:
        from tools.a2a.agent_registry import discover_agents as _discover  # type: ignore[import-untyped]
        agents = _discover(db_path=db_path)
    except Exception as exc:
        return {"status": "ok", "count": 0, "items": [], "note": f"registry unavailable: {exc}"}

    if not agents:
        agents = _build_default_a2a_agents()

    now = datetime.now(timezone.utc)
    dead_items: List[dict] = []
    alive_count = 0

    for agent in agents:
        url = agent.get("url", "")
        if not url:
            continue
        agent_id = agent.get("id") or url
        endpoint = url.rstrip("/") + "/.well-known/agent.json"

        result_str = "dead"
        http_status = None
        try:
            req = urllib.request.Request(endpoint, method="HEAD")
            # nosec B310 -- internal A2A agent endpoints only
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:  # noqa: S310
                http_status = resp.status
                if 200 <= http_status < 300:
                    result_str = "alive"
        except Exception:
            result_str = "dead"

        if result_str == "alive":
            alive_count += 1
            _a2a_dead_since.pop(agent_id, None)
        else:
            if agent_id not in _a2a_dead_since:
                _a2a_dead_since[agent_id] = now.strftime("%Y-%m-%dT%H:%M:%S")
            dead_items.append(
                {
                    "agent_id": agent_id,
                    "name": agent.get("name", ""),
                    "url": url,
                    "result": "dead",
                    "http_status": http_status,
                    "dead_since": _a2a_dead_since[agent_id],
                    "component_id": agent_id,
                }
            )

    # Alert for agents dead beyond the threshold
    long_dead: List[dict] = []
    for item in dead_items:
        dead_since_str = _a2a_dead_since.get(item["agent_id"], "")
        if dead_since_str:
            try:
                dead_since_dt = datetime.fromisoformat(dead_since_str).replace(tzinfo=timezone.utc)
                dead_minutes = (now - dead_since_dt).total_seconds() / 60.0
                if dead_minutes >= dead_alert_minutes:
                    long_dead.append({**item, "dead_minutes": round(dead_minutes, 1)})
            except ValueError:
                pass

    if long_dead:
        _telegram_alert_dead_agents(long_dead)

    return {
        "status": "critical" if dead_items else "ok",
        "count": len(dead_items),
        "items": dead_items[:20],
        "alive_count": alive_count,
    }


# ---------------------------------------------------------------------------
# Check types that trigger auto_resolver on failure
# ---------------------------------------------------------------------------
_AUTO_RESOLVE_CHECKS = {"ace_instance_stale", "kanban_stale", "a2a_agent_health"}


def _trigger_auto_resolver(check_type: str, result: dict, db_path: Optional[Path] = None) -> None:
    """Call auto_resolver.resolve_component for each dead item in the check result."""
    try:
        from tools.monitor.auto_resolver import resolve_component
    except ImportError:
        return

    for item in result.get("items", []):
        if item.get("result") != "dead":
            continue
        component_id = item.get("component_id") or item.get("id") or item.get("agent_id", "")
        if not component_id:
            continue
        try:
            resolve_component(component_id, check_type, db_path)
        except Exception:
            pass


CHECK_REGISTRY: Dict[str, Callable] = {
    "cato_evidence": check_cato_evidence,
    "agent_health": check_agent_health,
    "cve_sla": check_cve_sla,
    "pending_intake": check_pending_intake,
    "failing_tests": check_failing_tests,
    "expiring_isas": check_expiring_isas,
    "memory_maintenance": check_memory_maintenance,
    "coherence_health": check_coherence_health,
    "review_board_health": check_review_board_health,
    "kanban_stale": check_kanban_stale,
    "ace_instance_stale": check_ace_instance_stale,
    "a2a_agent_health": check_a2a_agent_health,
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
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
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

    # Auto-resolve for supported check types
    if check_type in _AUTO_RESOLVE_CHECKS and result.get("status") in ("warning", "critical"):
        _trigger_auto_resolver(check_type, result, db_path)

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
    parser = argparse.ArgumentParser(description="ICDEV™ Heartbeat Daemon (D141) — proactive check loop")
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
