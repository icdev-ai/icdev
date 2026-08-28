#!/usr/bin/env python3
# CUI // SP-CTI
"""Companion Sync Daemon — scheduled regeneration of AI platform config files.

Runs ``companion.py --sync --write`` on a configurable interval (default: 30 min)
so that headless deployments and air-gap environments automatically receive
updated companion configs as goals and skills evolve — without requiring a
dashboard skill promotion to trigger the sync.

Architecture:
    Single reflex daemon built on DaemonBase (tools/daemon/base.py).
    Config: args/scheduler_config.yaml
    Audit: companion_sync_audit table (append-only, NIST AU)
    State: companion_sync_reflex_state table

Usage:
    python tools/scheduler/companion_sync_daemon.py              # Daemon mode
    python tools/scheduler/companion_sync_daemon.py --once       # Single pass
    python tools/scheduler/companion_sync_daemon.py --status     # Show status
    python tools/scheduler/companion_sync_daemon.py --reflex sync  # Force one sync
    python tools/scheduler/companion_sync_daemon.py --json       # JSON output

Cron alternative (non-systemd / Windows Task Scheduler):
    # Linux crontab — every 30 minutes
    */30 * * * * cd /opt/icdev && python tools/scheduler/companion_sync_daemon.py --once >> /var/log/icdev/companion_sync.log 2>&1

    # Windows Task Scheduler (PowerShell) — every 30 minutes
    schtasks /create /tn "ICDEV-CompanionSync" /tr "python C:\\ICDev\\tools\\scheduler\\companion_sync_daemon.py --once --json" /sc minute /mo 30

    # Air-gap one-shot (no cron) — call directly from CI/CD pipeline stage end:
    python tools/scheduler/companion_sync_daemon.py --reflex sync --json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.daemon.base import (  # noqa: E402
    DaemonBase,
    ReflexStateBase,
    TrustKernelBase,
    generate_id,
    utcnow_iso,
)
from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_VERSION = "1.0.0"
CONFIG_PATH = BASE_DIR / "args" / "scheduler_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "scheduler" / "companion_sync.pid"

REFLEX_NAMES = ["sync"]

# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------
_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS companion_sync_audit (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    reflex_name     TEXT,
    risk_tier       TEXT,
    details         TEXT,
    success         INTEGER,
    duration_ms     INTEGER,
    metric_name     TEXT,
    metric_value    REAL,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companion_sync_reflex_state (
    reflex_name                 TEXT PRIMARY KEY,
    enabled                     INTEGER NOT NULL DEFAULT 1,
    last_run_at                 TEXT,
    next_run_at                 TEXT,
    consecutive_failures        INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_open        INTEGER NOT NULL DEFAULT 0,
    circuit_breaker_tripped_at  TEXT,
    total_runs                  INTEGER NOT NULL DEFAULT 0,
    total_successes             INTEGER NOT NULL DEFAULT 0,
    total_failures              INTEGER NOT NULL DEFAULT 0,
    last_metric_value           REAL,
    last_error                  TEXT,
    updated_at                  TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Subclasses
# ---------------------------------------------------------------------------
class CompanionSyncReflexState(ReflexStateBase):
    state_table = "companion_sync_reflex_state"


class CompanionSyncTrustKernel(TrustKernelBase):
    """All companion sync operations are GREEN — read-only introspection + file write."""

    def can_execute(self, risk_tier: str, action: str = "run") -> Tuple[bool, str]:
        return True, "approved"


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------
class CompanionSyncDaemon(DaemonBase):
    """Scheduled daemon that regenerates companion configs for all AI platforms.

    Runs ``companion.py --sync --write`` on a configurable interval so that
    headless/air-gap deployments stay current without relying on dashboard events.
    """

    daemon_name = "Companion Sync Daemon"
    daemon_version = DAEMON_VERSION
    config_path = CONFIG_PATH
    pid_file = PID_FILE
    env_enabled_var = "ICDEV_COMPANION_SYNC_ENABLED"
    env_reflex_prefix = "ICDEV_COMPANION_SYNC_REFLEX"
    event_prefix = "companion_sync"
    reflex_names = REFLEX_NAMES
    id_prefix = "csd"
    service_name = "companion-sync"       # coordination session-id prefix (autonomy-id-05)
    service_agent = "companion_sync"

    # ------------------------------------------------------------------
    # Table lifecycle
    # ------------------------------------------------------------------
    def ensure_tables(self) -> None:
        conn = get_connection()
        try:
            # executescript works for SQLite; fall back statement-by-statement for PostgreSQL
            try:
                conn.executescript(_CREATE_TABLES_SQL)
                conn.commit()
            except AttributeError:
                for stmt in _CREATE_TABLES_SQL.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
                conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Audit (append-only, NIST AU)
    # ------------------------------------------------------------------
    def log_audit(
        self,
        event_type: str,
        reflex_name: str = None,
        risk_tier: str = None,
        details: Dict = None,
        success: bool = None,
        duration_ms: int = None,
        metric_name: str = None,
        metric_value: float = None,
        **kwargs,
    ) -> str:
        audit_id = generate_id("csd-aud")
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO companion_sync_audit
                    (id, event_type, reflex_name, risk_tier, details, success,
                     duration_ms, metric_name, metric_value, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    audit_id,
                    event_type,
                    reflex_name,
                    risk_tier,
                    json.dumps(details) if details else None,
                    1 if success else (0 if success is False else None),
                    duration_ms,
                    metric_name,
                    metric_value,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return audit_id

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------
    def create_reflex_state(self, name: str, config: Dict[str, Any]) -> ReflexStateBase:
        return CompanionSyncReflexState(name, config)

    def create_trust_kernel(self, config: Dict[str, Any]) -> TrustKernelBase:
        return CompanionSyncTrustKernel(config)

    # ------------------------------------------------------------------
    # Core reflex: invoke companion.py subprocess
    # ------------------------------------------------------------------
    def run_reflex_impl(
        self, name: str, config: Dict[str, Any], trust: TrustKernelBase
    ) -> Tuple[bool, float, Dict]:
        """Run companion sync — the only reflex this daemon exposes."""
        if name != "sync":
            return False, 0.0, {"error": f"Unknown reflex: {name}"}

        companion_script = BASE_DIR / "tools" / "dx" / "companion.py"
        if not companion_script.exists():
            return False, 0.0, {"error": "companion.py not found", "path": str(companion_script)}

        # Build CLI: --sync --write --json (+ optional --platforms override)
        platforms = config.get("platforms", [])
        cmd = [sys.executable, str(companion_script), "--sync", "--write", "--json"]
        if platforms:
            cmd += ["--platforms", ",".join(platforms)]

        timeout = config.get("timeout_seconds", 120)

        try:
            proc = subprocess.run(  # nosec B603 — args are internal constants, not user input
                cmd,
                cwd=str(BASE_DIR),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return False, 0.0, {"error": f"companion.py timed out after {timeout}s"}
        except Exception as exc:
            return False, 0.0, {"error": f"subprocess error: {exc}"}

        if proc.returncode != 0:
            return (
                False,
                0.0,
                {
                    "error": "companion.py exited non-zero",
                    "returncode": proc.returncode,
                    "stderr": proc.stderr[:1000] if proc.stderr else "",
                },
            )

        # Parse JSON output to count files written
        files_written = 0
        summary = {}
        try:
            output = json.loads(proc.stdout)
            summary = output.get("summary", {})
            files_written = summary.get("files_written", 0)
            if not files_written:
                # Count instruction_files as a proxy
                files_written = len(output.get("instruction_files", []))
        except (json.JSONDecodeError, AttributeError):
            # Non-JSON output still counts as success if returncode == 0
            files_written = 1

        return (
            True,
            float(files_written),
            {
                "files_written": files_written,
                "summary": summary,
                "returncode": proc.returncode,
            },
        )

    # ------------------------------------------------------------------
    # Extra status
    # ------------------------------------------------------------------
    def get_extra_status(self) -> Dict[str, Any]:
        interval_sec = (
            self.config.get("reflexes", {})
            .get("sync", {})
            .get("interval_seconds", 1800)
        )
        return {"sync_interval_seconds": interval_sec}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    config = CompanionSyncDaemon.load_config()
    daemon = CompanionSyncDaemon(config)
    daemon.run_cli()


if __name__ == "__main__":
    main()
