#!/usr/bin/env python3
"""AppForge Daemon — autonomous vertical discovery + app builder + Pulse writer.

Runs 5 reflexes on a daily cycle:
  1. discover — Innovation/Creative/Research engines find high-value challenges
  2. evaluate — Score and select the top challenge to build
  3. architect — Generate app blueprint and specification
  4. build    — Create standalone child app (Flask + SQLite + professional UI)
  5. publish  — Write and publish Pulse article about the build

Usage:
    python tools/appforge/daemon.py                     # Run as daemon
    python tools/appforge/daemon.py --once              # Single pass
    python tools/appforge/daemon.py --status            # Show status
    python tools/appforge/daemon.py --reflex discover   # Run one reflex
    python tools/appforge/daemon.py --json              # JSON output
"""

import importlib
import json
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
    BASE_DIR,
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
CONFIG_PATH = BASE_DIR / "args" / "appforge_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "appforge" / "daemon.pid"

REFLEX_NAMES = [
    "discover",  # Scan innovation + creative + research for challenges
    "evaluate",  # Score and select best challenge
    "architect",  # Generate app blueprint
    "build",  # Create child app
    "publish",  # Write and publish Pulse article
]


# ---------------------------------------------------------------------------
# AppForge ReflexState
# ---------------------------------------------------------------------------
class AppForgeReflexState(ReflexStateBase):
    state_table = "appforge_reflex_state"


# ---------------------------------------------------------------------------
# AppForge Trust Kernel — GREEN tier (auto-build, auto-publish)
# ---------------------------------------------------------------------------
class AppForgeTrustKernel(TrustKernelBase):
    """All reflexes run at GREEN tier — fully autonomous."""

    def can_execute(self, risk_tier: str, action: str = "run") -> Tuple[bool, str]:
        return True, "approved"


# ---------------------------------------------------------------------------
# AppForge Daemon
# ---------------------------------------------------------------------------
class AppForgeDaemon(DaemonBase):
    """Autonomous vertical discovery + app builder + Pulse writer."""

    daemon_name = "AppForge Daemon"
    daemon_version = DAEMON_VERSION
    config_path = CONFIG_PATH
    pid_file = PID_FILE
    env_enabled_var = "ICDEV_APPFORGE_ENABLED"
    env_reflex_prefix = "ICDEV_APPFORGE_REFLEX"
    event_prefix = "appforge"
    reflex_names = REFLEX_NAMES
    id_prefix = "afg"
    service_name = "appforge-daemon"       # coordination session-id prefix (autonomy-id-05)
    service_agent = "appforge"

    def ensure_tables(self) -> None:
        """Create AppForge tables."""
        conn = get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS appforge_audit (
                    id              TEXT PRIMARY KEY,
                    event_type      TEXT NOT NULL,
                    reflex_name     TEXT,
                    risk_tier       TEXT,
                    details         TEXT,
                    success         INTEGER,
                    duration_ms     INTEGER,
                    metric_name     TEXT,
                    metric_value    REAL,
                    created_at      TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS appforge_reflex_state (
                    reflex_name              TEXT PRIMARY KEY,
                    enabled                  INTEGER NOT NULL DEFAULT 1,
                    last_run_at              TEXT,
                    next_run_at              TEXT,
                    consecutive_failures     INTEGER NOT NULL DEFAULT 0,
                    circuit_breaker_open     INTEGER NOT NULL DEFAULT 0,
                    circuit_breaker_tripped_at TEXT,
                    total_runs               INTEGER NOT NULL DEFAULT 0,
                    total_successes          INTEGER NOT NULL DEFAULT 0,
                    total_failures           INTEGER NOT NULL DEFAULT 0,
                    last_metric_value        REAL,
                    last_error               TEXT,
                    updated_at               TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS appforge_challenges (
                    challenge_id    TEXT PRIMARY KEY,
                    vertical        TEXT NOT NULL,
                    title           TEXT NOT NULL,
                    description     TEXT,
                    pain_points     TEXT,
                    source          TEXT,
                    score           REAL DEFAULT 0.0,
                    status          TEXT DEFAULT 'discovered',
                    app_path        TEXT,
                    app_port        INTEGER,
                    pulse_post_id   TEXT,
                    discovered_at   TEXT NOT NULL,
                    built_at        TEXT,
                    published_at    TEXT
                );
            """)
            conn.commit()
        finally:
            conn.close()

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
        """Append-only audit logging."""
        audit_id = generate_id("afg-aud")
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO appforge_audit
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

    def create_reflex_state(self, name: str, config: Dict[str, Any]) -> ReflexStateBase:
        return AppForgeReflexState(name, config)

    def create_trust_kernel(self, config: Dict[str, Any]) -> TrustKernelBase:
        return AppForgeTrustKernel(config)

    def run_reflex_impl(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Execute a single reflex via tools/appforge/reflexes/<name>.py."""
        try:
            module = importlib.import_module(f"tools.appforge.reflexes.{name}")
            if hasattr(module, "run"):
                result = module.run(config, trust)
                return (
                    result.get("success", False),
                    result.get("metric_value", 0.0),
                    result.get("details", {}),
                )
        except ImportError:
            pass
        except Exception as e:
            return False, 0.0, {"error": str(e), "stage": "reflex_execution"}

        return (
            True,
            0.0,
            {
                "status": "stub",
                "message": f"Reflex '{name}' not yet implemented — stub mode",
            },
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    config = AppForgeDaemon.load_config()
    daemon = AppForgeDaemon(config)
    daemon.run_cli()


if __name__ == "__main__":
    main()
