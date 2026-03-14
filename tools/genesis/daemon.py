#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Daemon — always-on autonomous research engine (D-GEN-1).

Runs 12 Reflexes as managed threads within a single process.  Each Reflex
operates on its own schedule and risk tier, governed by the Trust Kernel.

Usage:
    python tools/genesis/daemon.py                    # Run as daemon
    python tools/genesis/daemon.py --once             # Single pass (run all due reflexes, then exit)
    python tools/genesis/daemon.py --status           # Show daemon & reflex status
    python tools/genesis/daemon.py --reflex research  # Run one reflex immediately
    python tools/genesis/daemon.py --enable research   # Enable a reflex
    python tools/genesis/daemon.py --disable research  # Disable a reflex
    python tools/genesis/daemon.py --reset research    # Reset circuit breaker
    python tools/genesis/daemon.py --json             # JSON output for all modes
"""

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_VERSION = "2.0.0-alpha"
CONFIG_PATH = BASE_DIR / "args" / "genesis_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "genesis" / "daemon.pid"
STATE_FILE = BASE_DIR / ".tmp" / "genesis" / "state.json"

# Risk tier enum
RISK_GREEN = "green"
RISK_YELLOW = "yellow"
RISK_ORANGE = "orange"

# Reflex names (ordered by schedule)
REFLEX_NAMES = [
    "research", "scout", "audit", "comply", "ingest", "market", "report",
    "publish", "test", "learn", "heal", "evolve",
]

# ---------------------------------------------------------------------------
# Shutdown coordination
# ---------------------------------------------------------------------------
_shutdown_event = threading.Event()


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle shutdown signals gracefully."""
    print(f"\nINFO: Genesis received signal {signum}, initiating graceful shutdown...")
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def _generate_id(prefix: str = "gen") -> str:
    """Return a short unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data: str) -> str:
    """Return SHA-256 hex digest of a string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------
def _load_config() -> Dict[str, Any]:
    """Load genesis configuration from YAML with env var overrides."""
    try:
        import yaml
    except ImportError:
        # Fallback: return minimal default config
        return _default_config()

    if not CONFIG_PATH.exists():
        return _default_config()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # Apply env var overrides
    if os.environ.get("ICDEV_GENESIS_ENABLED", "").lower() in ("true", "1"):
        config["enabled"] = True
    elif os.environ.get("ICDEV_GENESIS_ENABLED", "").lower() in ("false", "0"):
        config["enabled"] = False

    # Per-reflex env overrides
    for name in REFLEX_NAMES:
        env_key = f"ICDEV_GENESIS_REFLEX_{name.upper()}_ENABLED"
        env_val = os.environ.get(env_key, "").lower()
        if env_val in ("true", "1"):
            config.setdefault("reflexes", {}).setdefault(name, {})["enabled"] = True
        elif env_val in ("false", "0"):
            config.setdefault("reflexes", {}).setdefault(name, {})["enabled"] = False

    return config


def _default_config() -> Dict[str, Any]:
    """Return minimal default configuration."""
    return {
        "enabled": False,
        "llm": {"tier": "scanner", "model": "qwen3.5-local"},
        "trust_kernel": {
            "circuit_breaker": {
                "max_consecutive_failures": 3,
                "cooldown_minutes": 60,
                "auto_reenable": False,
            },
        },
        "reflexes": {name: {"enabled": False, "risk_tier": RISK_GREEN} for name in REFLEX_NAMES},
    }


# ---------------------------------------------------------------------------
# Database — genesis_audit table (D-GEN-6, D-GEN-10)
# ---------------------------------------------------------------------------
def _ensure_tables() -> None:
    """Create genesis tables if they do not exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS genesis_audit (
                id              TEXT PRIMARY KEY,
                event_type      TEXT NOT NULL,
                reflex_name     TEXT,
                risk_tier       TEXT,
                details         TEXT,
                success         INTEGER,
                duration_ms     INTEGER,
                metric_name     TEXT,
                metric_value    REAL,
                gkp_id          TEXT,
                created_at      TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS genesis_reflex_state (
                reflex_name         TEXT PRIMARY KEY,
                enabled             INTEGER NOT NULL DEFAULT 1,
                last_run_at         TEXT,
                next_run_at         TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                circuit_breaker_open INTEGER NOT NULL DEFAULT 0,
                circuit_breaker_tripped_at TEXT,
                total_runs          INTEGER NOT NULL DEFAULT 0,
                total_successes     INTEGER NOT NULL DEFAULT 0,
                total_failures      INTEGER NOT NULL DEFAULT 0,
                last_metric_value   REAL,
                last_error          TEXT,
                updated_at          TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS genesis_gkp (
                id              TEXT PRIMARY KEY,
                gkp_version     TEXT NOT NULL DEFAULT '1.0',
                artifact_type   TEXT NOT NULL,
                genesis_reflex  TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.0,
                evidence        TEXT,
                payload         TEXT NOT NULL,
                sha256          TEXT NOT NULL,
                promotion_status TEXT NOT NULL DEFAULT 'pending_review',
                promoted_at     TEXT,
                created_at      TEXT NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _log_audit(event_type: str, reflex_name: str = None,
               risk_tier: str = None, details: Dict = None,
               success: bool = None, duration_ms: int = None,
               metric_name: str = None, metric_value: float = None,
               gkp_id: str = None) -> str:
    """Append an audit event (D-GEN-10: append-only, NIST AU compliant)."""
    audit_id = _generate_id("aud")
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO genesis_audit
                (id, event_type, reflex_name, risk_tier, details, success,
                 duration_ms, metric_name, metric_value, gkp_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            audit_id, event_type, reflex_name, risk_tier,
            json.dumps(details) if details else None,
            1 if success else (0 if success is False else None),
            duration_ms, metric_name, metric_value, gkp_id,
            _utcnow_iso(),
        ))
        conn.commit()
    finally:
        conn.close()
    return audit_id


# ---------------------------------------------------------------------------
# Reflex State Management
# ---------------------------------------------------------------------------
class ReflexState:
    """Manages persistent state for a single reflex."""

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self._lock = threading.Lock()

    def load(self) -> Dict[str, Any]:
        """Load current state from DB."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM genesis_reflex_state WHERE reflex_name = ?",
                (self.name,)
            ).fetchone()
            if row:
                return dict(row)
            # Initialize if missing
            now = _utcnow_iso()
            conn.execute("""
                INSERT OR IGNORE INTO genesis_reflex_state
                    (reflex_name, enabled, consecutive_failures, circuit_breaker_open,
                     total_runs, total_successes, total_failures, updated_at)
                VALUES (?, ?, 0, 0, 0, 0, 0, ?)
            """, (self.name, 1 if self.config.get("enabled", True) else 0, now))
            conn.commit()
            return {
                "reflex_name": self.name,
                "enabled": 1 if self.config.get("enabled", True) else 0,
                "last_run_at": None,
                "next_run_at": None,
                "consecutive_failures": 0,
                "circuit_breaker_open": 0,
                "total_runs": 0,
                "total_successes": 0,
                "total_failures": 0,
            }
        finally:
            conn.close()

    def record_success(self, metric_value: float = None) -> None:
        """Record a successful run."""
        with self._lock:
            now = _utcnow_iso()
            conn = get_connection()
            try:
                conn.execute("""
                    UPDATE genesis_reflex_state SET
                        last_run_at = ?,
                        consecutive_failures = 0,
                        total_runs = total_runs + 1,
                        total_successes = total_successes + 1,
                        last_metric_value = ?,
                        last_error = NULL,
                        updated_at = ?
                    WHERE reflex_name = ?
                """, (now, metric_value, now, self.name))
                conn.commit()
            finally:
                conn.close()

    def record_failure(self, error: str, cb_config: Dict) -> bool:
        """Record a failed run.  Returns True if circuit breaker tripped."""
        with self._lock:
            now = _utcnow_iso()
            conn = get_connection()
            try:
                state = conn.execute(
                    "SELECT consecutive_failures FROM genesis_reflex_state WHERE reflex_name = ?",
                    (self.name,)
                ).fetchone()
                failures = (state["consecutive_failures"] if state else 0) + 1
                tripped = failures >= cb_config.get("max_consecutive_failures", 3)

                conn.execute("""
                    UPDATE genesis_reflex_state SET
                        last_run_at = ?,
                        consecutive_failures = ?,
                        circuit_breaker_open = ?,
                        circuit_breaker_tripped_at = ?,
                        total_runs = total_runs + 1,
                        total_failures = total_failures + 1,
                        last_error = ?,
                        updated_at = ?
                    WHERE reflex_name = ?
                """, (
                    now, failures,
                    1 if tripped else 0,
                    now if tripped else None,
                    error[:2000], now, self.name,
                ))
                conn.commit()
                return tripped
            finally:
                conn.close()

    def reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker (human-initiated)."""
        with self._lock:
            now = _utcnow_iso()
            conn = get_connection()
            try:
                conn.execute("""
                    UPDATE genesis_reflex_state SET
                        consecutive_failures = 0,
                        circuit_breaker_open = 0,
                        circuit_breaker_tripped_at = NULL,
                        updated_at = ?
                    WHERE reflex_name = ?
                """, (now, self.name))
                conn.commit()
            finally:
                conn.close()

    def is_circuit_open(self) -> bool:
        """Check if circuit breaker is open (reflex disabled)."""
        state = self.load()
        return bool(state.get("circuit_breaker_open", 0))

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable this reflex."""
        now = _utcnow_iso()
        conn = get_connection()
        try:
            conn.execute("""
                UPDATE genesis_reflex_state SET enabled = ?, updated_at = ?
                WHERE reflex_name = ?
            """, (1 if enabled else 0, now, self.name))
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Trust Kernel
# ---------------------------------------------------------------------------
class TrustKernel:
    """Enforces risk tiers, approval gates, and action whitelists (D-GEN-3)."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("trust_kernel", {})
        self.risk_tiers = self.config.get("risk_tiers", {})
        self.allowed_actions = self.config.get("allowed_actions", {})

    def can_execute(self, risk_tier: str, action: str) -> Tuple[bool, str]:
        """Check if an action is allowed for the given risk tier."""
        allowed = self.allowed_actions.get(risk_tier, [])
        if action not in allowed:
            return False, f"Action '{action}' not in whitelist for tier '{risk_tier}'"
        return True, "approved"

    def requires_sandbox(self, risk_tier: str) -> bool:
        """Check if this tier requires a git worktree sandbox."""
        tier_config = self.risk_tiers.get(risk_tier, {})
        return tier_config.get("sandbox", False)

    def requires_human_approval(self, risk_tier: str) -> bool:
        """Check if this tier requires human approval."""
        tier_config = self.risk_tiers.get(risk_tier, {})
        return tier_config.get("approval") == "human"

    def requires_tests(self, risk_tier: str) -> bool:
        """Check if this tier requires tests to pass."""
        tier_config = self.risk_tiers.get(risk_tier, {})
        return tier_config.get("require_tests_pass", False)


# ---------------------------------------------------------------------------
# Schedule Parser
# ---------------------------------------------------------------------------
def _parse_schedule(schedule_str: str) -> Optional[Dict[str, Any]]:
    """Parse schedule string into structured form.

    Supports:
        "every 6h"          → interval-based
        "every 4h"          → interval-based
        "daily 07:00"       → daily at time
        "nightly 02:00"     → daily at time (alias)
        "weekly Sun 20:00"  → weekly at day+time
        "continuous"        → interval-based (uses interval_seconds from config)
    """
    s = schedule_str.strip().lower()

    if s == "continuous":
        return {"type": "interval", "seconds": 300}

    # "every Nh"
    m = re.match(r"every\s+(\d+)h", s)
    if m:
        return {"type": "interval", "seconds": int(m.group(1)) * 3600}

    # "daily HH:MM" or "nightly HH:MM"
    m = re.match(r"(?:daily|nightly)\s+(\d{2}):(\d{2})", s)
    if m:
        return {"type": "daily", "hour": int(m.group(1)), "minute": int(m.group(2))}

    # "weekly DAY HH:MM"
    m = re.match(r"weekly\s+(\w+)\s+(\d{2}):(\d{2})", s)
    if m:
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        day = day_map.get(m.group(1)[:3], 6)
        return {"type": "weekly", "weekday": day, "hour": int(m.group(2)), "minute": int(m.group(3))}

    return None


def _is_due(schedule: Dict[str, Any], last_run: Optional[str]) -> bool:
    """Check if a reflex is due to run based on its schedule and last run time."""
    now = _utcnow()

    if schedule["type"] == "interval":
        if last_run is None:
            return True
        try:
            last = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return True
        return (now - last).total_seconds() >= schedule["seconds"]

    if schedule["type"] == "daily":
        target = now.replace(hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0)
        if now < target:
            return False
        if last_run is None:
            return True
        try:
            last = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return True
        return last < target

    if schedule["type"] == "weekly":
        target = now.replace(hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0)
        # Find the most recent occurrence of the target weekday
        days_since = (now.weekday() - schedule["weekday"]) % 7
        target = target - timedelta(days=days_since)
        if now < target:
            return False
        if last_run is None:
            return True
        try:
            last = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return True
        return last < target

    return False


# ---------------------------------------------------------------------------
# Metric Evaluator
# ---------------------------------------------------------------------------
def _evaluate_metric(metric_config: Dict[str, Any], value: float) -> bool:
    """Evaluate a success metric against its threshold."""
    if "composite" in metric_config:
        # Composite metrics not evaluable without all values — skip for now
        return True

    threshold = metric_config.get("threshold", 0)
    op = metric_config.get("operator", "gt")

    if op == "gt":
        return value > threshold
    if op == "gte":
        return value >= threshold
    if op == "lt":
        return value < threshold
    if op == "lte":
        return value <= threshold
    if op == "eq":
        return value == threshold
    return True


# ---------------------------------------------------------------------------
# Reflex Runner (stub — individual reflex implementations in reflexes/)
# ---------------------------------------------------------------------------
def _run_reflex(name: str, config: Dict[str, Any], trust: TrustKernel) -> Tuple[bool, float, Dict]:
    """Execute a single reflex.  Returns (success, metric_value, details).

    This is the dispatch layer.  Each reflex has its own implementation module
    in tools/genesis/reflexes/.  If the module doesn't exist yet, the reflex
    runs in stub mode (returns success with metric=0).
    """
    risk_tier = config.get("risk_tier", RISK_GREEN)

    # Check trust kernel approval
    if trust.requires_human_approval(risk_tier):
        # ORANGE tier — log that human review is needed, don't auto-execute
        return True, 0.0, {"status": "awaiting_human_approval", "risk_tier": risk_tier}

    # Try to import the reflex module
    try:
        import importlib
        module = importlib.import_module(f"tools.genesis.reflexes.{name}")
        if hasattr(module, "run"):
            result = module.run(config, trust)
            return (
                result.get("success", False),
                result.get("metric_value", 0.0),
                result.get("details", {}),
            )
    except ImportError:
        pass  # Module not yet implemented — run stub
    except Exception as e:
        return False, 0.0, {"error": str(e), "stage": "reflex_execution"}

    # Stub mode — reflex not yet implemented
    return True, 0.0, {
        "status": "stub",
        "message": f"Reflex '{name}' not yet implemented — stub mode (success)",
    }


# ---------------------------------------------------------------------------
# Genesis Daemon
# ---------------------------------------------------------------------------
class GenesisDaemon:
    """Main daemon process managing 12 Reflexes (D-GEN-1)."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.trust = TrustKernel(config)
        self.reflex_states: Dict[str, ReflexState] = {}
        self.schedules: Dict[str, Dict[str, Any]] = {}
        self._executor: Optional[ThreadPoolExecutor] = None

        # Initialize reflex states and schedules
        for name in REFLEX_NAMES:
            reflex_config = config.get("reflexes", {}).get(name, {})
            self.reflex_states[name] = ReflexState(name, reflex_config)

            schedule_str = reflex_config.get("schedule", "")
            if schedule_str == "continuous":
                interval = reflex_config.get("interval_seconds", 300)
                self.schedules[name] = {"type": "interval", "seconds": interval}
            else:
                parsed = _parse_schedule(schedule_str)
                if parsed:
                    self.schedules[name] = parsed

    def _init_states(self) -> None:
        """Ensure all reflex states exist in DB."""
        for state in self.reflex_states.values():
            state.load()

    def run_reflex(self, name: str) -> Dict[str, Any]:
        """Run a single reflex with full lifecycle management."""
        reflex_config = self.config.get("reflexes", {}).get(name, {})
        state = self.reflex_states.get(name)
        if not state:
            return {"error": f"Unknown reflex: {name}"}

        risk_tier = reflex_config.get("risk_tier", RISK_GREEN)
        cb_config = self.config.get("trust_kernel", {}).get("circuit_breaker", {})

        # Pre-flight checks
        if state.is_circuit_open():
            _log_audit("genesis.reflex.skipped", name, risk_tier,
                       {"reason": "circuit_breaker_open"})
            return {"status": "skipped", "reason": "circuit_breaker_open"}

        current_state = state.load()
        if not current_state.get("enabled", 1):
            return {"status": "skipped", "reason": "disabled"}

        # Execute
        _log_audit("genesis.reflex.started", name, risk_tier)
        start_time = time.monotonic()

        try:
            success, metric_value, details = _run_reflex(name, reflex_config, self.trust)
            duration_ms = int((time.monotonic() - start_time) * 1000)

            # Evaluate success metric
            metric_config = reflex_config.get("success_metric", {})
            metric_name = metric_config.get("name", "default")
            metric_passed = _evaluate_metric(metric_config, metric_value) if success else False

            if success and metric_passed:
                state.record_success(metric_value)
                _log_audit("genesis.reflex.completed", name, risk_tier,
                           details, success=True, duration_ms=duration_ms,
                           metric_name=metric_name, metric_value=metric_value)
                return {
                    "status": "success",
                    "reflex": name,
                    "metric": {metric_name: metric_value},
                    "duration_ms": duration_ms,
                    "details": details,
                }
            else:
                error_msg = details.get("error", "metric_threshold_not_met")
                tripped = state.record_failure(error_msg, cb_config)
                event = "genesis.circuit_breaker.tripped" if tripped else "genesis.reflex.failed"
                _log_audit(event, name, risk_tier,
                           {**details, "circuit_breaker_tripped": tripped},
                           success=False, duration_ms=duration_ms,
                           metric_name=metric_name, metric_value=metric_value)

                if tripped:
                    print(f"WARNING: Circuit breaker TRIPPED for reflex '{name}' "
                          f"after {cb_config.get('max_consecutive_failures', 3)} consecutive failures")

                return {
                    "status": "failed",
                    "reflex": name,
                    "circuit_breaker_tripped": tripped,
                    "error": error_msg,
                    "duration_ms": duration_ms,
                }

        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            error_msg = f"{type(e).__name__}: {e}"
            tripped = state.record_failure(error_msg, cb_config)
            _log_audit("genesis.reflex.failed", name, risk_tier,
                       {"error": error_msg, "exception": True},
                       success=False, duration_ms=duration_ms)
            return {
                "status": "error",
                "reflex": name,
                "error": error_msg,
                "circuit_breaker_tripped": tripped,
                "duration_ms": duration_ms,
            }

    def run_due_reflexes(self) -> List[Dict[str, Any]]:
        """Run all reflexes that are currently due."""
        results = []
        for name in REFLEX_NAMES:
            if _shutdown_event.is_set():
                break

            schedule = self.schedules.get(name)
            if not schedule:
                continue

            state = self.reflex_states[name].load()
            if not state.get("enabled", 1):
                continue
            if state.get("circuit_breaker_open", 0):
                continue

            if _is_due(schedule, state.get("last_run_at")):
                print(f"INFO: Running reflex '{name}' (due)")
                result = self.run_reflex(name)
                results.append(result)

        return results

    def run_forever(self) -> None:
        """Main daemon loop — check and run due reflexes."""
        print(f"INFO: Genesis Daemon v{DAEMON_VERSION} starting...")
        print(f"INFO: {sum(1 for n in REFLEX_NAMES if self.config.get('reflexes', {}).get(n, {}).get('enabled'))} reflexes enabled")

        _log_audit("genesis.daemon.started", details={
            "version": DAEMON_VERSION,
            "enabled_reflexes": [
                n for n in REFLEX_NAMES
                if self.config.get("reflexes", {}).get(n, {}).get("enabled")
            ],
        })

        # Write PID file
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))

        self._init_states()

        try:
            check_interval = self.config.get("trust_kernel", {}).get(
                "kill_switch", {}).get("check_interval_seconds", 10)

            while not _shutdown_event.is_set():
                # Check kill switch
                if not self.config.get("enabled", False):
                    env_val = os.environ.get("ICDEV_GENESIS_ENABLED", "").lower()
                    if env_val not in ("true", "1"):
                        _log_audit("genesis.kill_switch.activated",
                                   details={"reason": "config_disabled"})
                        print("INFO: Genesis disabled via config/env — shutting down")
                        break

                # Run due reflexes
                try:
                    self.run_due_reflexes()
                except Exception as e:
                    print(f"ERROR: Daemon loop error: {e}")
                    _log_audit("genesis.daemon.error", details={"error": str(e)})

                # Sleep in small increments for responsive shutdown
                for _ in range(check_interval):
                    if _shutdown_event.is_set():
                        break
                    time.sleep(1)

        finally:
            _log_audit("genesis.daemon.stopped", details={"reason": "shutdown"})
            print("INFO: Genesis Daemon stopped")
            if PID_FILE.exists():
                PID_FILE.unlink()

    def get_status(self) -> Dict[str, Any]:
        """Return current daemon and reflex status."""
        reflexes = {}
        for name in REFLEX_NAMES:
            state = self.reflex_states[name].load()
            reflex_config = self.config.get("reflexes", {}).get(name, {})
            reflexes[name] = {
                "enabled": bool(state.get("enabled", 0)),
                "risk_tier": reflex_config.get("risk_tier", "unknown"),
                "schedule": reflex_config.get("schedule", "unknown"),
                "last_run_at": state.get("last_run_at"),
                "consecutive_failures": state.get("consecutive_failures", 0),
                "circuit_breaker_open": bool(state.get("circuit_breaker_open", 0)),
                "total_runs": state.get("total_runs", 0),
                "total_successes": state.get("total_successes", 0),
                "total_failures": state.get("total_failures", 0),
                "last_metric_value": state.get("last_metric_value"),
                "last_error": state.get("last_error"),
            }

        # Count recent audit events
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM genesis_audit WHERE created_at > ?",
                ((_utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),)
            ).fetchone()
            events_24h = row["cnt"] if row else 0
        except Exception:
            events_24h = 0
        finally:
            conn.close()

        return {
            "daemon": {
                "version": DAEMON_VERSION,
                "enabled": self.config.get("enabled", False),
                "pid": os.getpid(),
                "pid_file": str(PID_FILE),
                "uptime": "running" if PID_FILE.exists() else "stopped",
            },
            "trust_kernel": {
                "circuit_breaker_max_failures": self.config.get(
                    "trust_kernel", {}).get("circuit_breaker", {}).get("max_consecutive_failures", 3),
            },
            "reflexes": reflexes,
            "audit": {
                "events_last_24h": events_24h,
            },
            "timestamp": _utcnow_iso(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Genesis Daemon — ICDEV v2.0 Autonomous Research Engine")
    parser.add_argument("--once", action="store_true",
                        help="Single pass: run all due reflexes then exit")
    parser.add_argument("--status", action="store_true",
                        help="Show daemon & reflex status")
    parser.add_argument("--reflex", type=str, metavar="NAME",
                        help="Run one reflex immediately")
    parser.add_argument("--enable", type=str, metavar="NAME",
                        help="Enable a reflex")
    parser.add_argument("--disable", type=str, metavar="NAME",
                        help="Disable a reflex")
    parser.add_argument("--reset", type=str, metavar="NAME",
                        help="Reset circuit breaker for a reflex")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")
    args = parser.parse_args()

    # Install signal handlers
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Load config and ensure tables
    config = _load_config()
    _ensure_tables()

    daemon = GenesisDaemon(config)

    # --- Status mode ---
    if args.status:
        status = daemon.get_status()
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            d = status["daemon"]
            print(f"Genesis Daemon v{d['version']}")
            print(f"  Enabled: {d['enabled']}")
            print(f"  PID: {d['pid']}")
            print(f"  Audit events (24h): {status['audit']['events_last_24h']}")
            print()
            print(f"{'Reflex':<12} {'Tier':<8} {'Enabled':<9} {'CB':<5} "
                  f"{'Runs':<6} {'OK':<6} {'Fail':<6} {'Last Run'}")
            print("-" * 90)
            for name, r in status["reflexes"].items():
                cb = "OPEN" if r["circuit_breaker_open"] else "ok"
                last = r["last_run_at"][:16] if r["last_run_at"] else "never"
                print(f"{name:<12} {r['risk_tier']:<8} {str(r['enabled']):<9} "
                      f"{cb:<5} {r['total_runs']:<6} {r['total_successes']:<6} "
                      f"{r['total_failures']:<6} {last}")
        return

    # --- Enable/Disable/Reset ---
    if args.enable:
        name = args.enable
        if name not in REFLEX_NAMES:
            print(f"ERROR: Unknown reflex '{name}'", file=sys.stderr)
            sys.exit(1)
        daemon.reflex_states[name].set_enabled(True)
        _log_audit("genesis.reflex.enabled", name)
        print(f"Reflex '{name}' enabled" if not args.json else
              json.dumps({"action": "enabled", "reflex": name}))
        return

    if args.disable:
        name = args.disable
        if name not in REFLEX_NAMES:
            print(f"ERROR: Unknown reflex '{name}'", file=sys.stderr)
            sys.exit(1)
        daemon.reflex_states[name].set_enabled(False)
        _log_audit("genesis.reflex.disabled", name)
        print(f"Reflex '{name}' disabled" if not args.json else
              json.dumps({"action": "disabled", "reflex": name}))
        return

    if args.reset:
        name = args.reset
        if name not in REFLEX_NAMES:
            print(f"ERROR: Unknown reflex '{name}'", file=sys.stderr)
            sys.exit(1)
        daemon.reflex_states[name].reset_circuit_breaker()
        _log_audit("genesis.circuit_breaker.reset", name)
        print(f"Circuit breaker reset for '{name}'" if not args.json else
              json.dumps({"action": "circuit_breaker_reset", "reflex": name}))
        return

    # --- Run one reflex ---
    if args.reflex:
        name = args.reflex
        if name not in REFLEX_NAMES:
            print(f"ERROR: Unknown reflex '{name}'", file=sys.stderr)
            sys.exit(1)
        daemon._init_states()
        result = daemon.run_reflex(name)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Reflex '{name}': {result.get('status', 'unknown')}")
            if result.get("error"):
                print(f"  Error: {result['error']}")
            if result.get("metric"):
                for k, v in result["metric"].items():
                    print(f"  {k}: {v}")
            if result.get("duration_ms"):
                print(f"  Duration: {result['duration_ms']}ms")
        return

    # --- Single pass ---
    if args.once:
        daemon._init_states()
        results = daemon.run_due_reflexes()
        if args.json:
            print(json.dumps({"mode": "once", "results": results}, indent=2))
        else:
            print(f"Ran {len(results)} reflexes")
            for r in results:
                status_str = r.get("status", "unknown")
                name = r.get("reflex", "unknown")
                print(f"  {name}: {status_str}")
        return

    # --- Daemon mode ---
    if not config.get("enabled", False):
        env_val = os.environ.get("ICDEV_GENESIS_ENABLED", "").lower()
        if env_val not in ("true", "1"):
            print("ERROR: Genesis is disabled. Set ICDEV_GENESIS_ENABLED=true or "
                  "enabled: true in args/genesis_config.yaml", file=sys.stderr)
            sys.exit(1)
        config["enabled"] = True

    daemon.run_forever()


if __name__ == "__main__":
    main()
