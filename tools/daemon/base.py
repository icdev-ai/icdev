#!/usr/bin/env python3
# CUI // SP-CTI
"""Daemon base classes — extracted shared infrastructure from Genesis and
Proposal Genesis daemons.

Provides:
    - DaemonBase: ABC for schedule-driven reflex daemons
    - ReflexStateBase: Thread-safe, DB-backed reflex state management
    - TrustKernelBase: Risk tier enforcement
    - Schedule parsing, metric evaluation, utility helpers

Architecture Decision:
    Genesis and Proposal Genesis daemons were ~90% identical code with different
    table names, event prefixes, and reflex lists.  This base extracts the shared
    infrastructure so each daemon only defines its domain-specific behavior.
"""

from __future__ import annotations

import abc
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Load .env so daemon env var overrides (ICDEV_GENESIS_ENABLED, etc.) work
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    _env_file = BASE_DIR / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Risk tier constants (shared across all daemons)
# ---------------------------------------------------------------------------
RISK_GREEN = "green"
RISK_YELLOW = "yellow"
RISK_ORANGE = "orange"


# ---------------------------------------------------------------------------
# Startup integrity guard — fail-closed against a shadowed `tools` tree
# ---------------------------------------------------------------------------
def verify_tools_tree_integrity(base_dir: Path = BASE_DIR) -> None:
    """Fail closed if the imported ``tools`` package is NOT this checkout.

    A user-site ``.pth`` file (e.g. fathomdesk-root.pth) can prepend a stale
    vendored copy of the repo onto sys.path. Script-style daemon launches
    (``python tools/genesis/daemon.py``) have only the script dir on sys.path[0],
    so user-site can win and bind ``sys.modules["tools"]`` to the stale tree —
    every later ``tools.*`` import, including the reflex dispatch
    ``importlib.import_module(f"tools.genesis.reflexes.{name}")``, then executes
    STALE code silently. This guard resolves the actually-imported ``tools``
    package and aborts if it does not live under this repo root.

    Set ``ICDEV_SKIP_TREE_GUARD=1`` to bypass — ONLY for exotic packaging
    scenarios (e.g. a legitimately relocated/vendored install where the check
    would false-positive). Never set it to paper over a real shadowing bug.
    """
    if os.environ.get("ICDEV_SKIP_TREE_GUARD") == "1":
        return
    try:
        import tools as _tools_pkg  # noqa: PLC0415 — must resolve the live binding

        tools_path = Path(_tools_pkg.__file__).resolve()
    except Exception as exc:  # pragma: no cover — import machinery failure
        print(
            f"CRITICAL: startup integrity guard could not resolve the 'tools' "
            f"package: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    expected_root = Path(base_dir).resolve()
    # tools_path is <root>/tools/__init__.py — its parent.parent is the repo root.
    resolved_root = tools_path.parent.parent
    try:
        tools_path.relative_to(expected_root)
        under_root = True
    except ValueError:
        under_root = False

    if not under_root:
        print(
            "CRITICAL: shadowed 'tools' package detected — refusing to start.\n"
            f"  expected repo root : {expected_root}\n"
            f"  resolved tools root: {resolved_root}\n"
            f"  imported tools path: {tools_path}\n"
            "  A stale vendored copy of the repo is ahead of this checkout on "
            "sys.path (likely a user-site .pth file). Fix PYTHONPATH / remove the "
            "shadow, or set ICDEV_SKIP_TREE_GUARD=1 if this is an intentional "
            "relocated install.",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------
def generate_id(prefix: str = "gen") -> str:
    """Return a short unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def utcnow() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(data: str) -> str:
    """Return SHA-256 hex digest of a string."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Active Hours Gate
# ---------------------------------------------------------------------------
def in_active_hours(config: Dict[str, Any]) -> bool:
    """Check if current local time is within the configured active_hours window.

    Returns True if active_hours is disabled or not configured (default: run anytime).
    Uses the timezone from config (e.g. 'America/New_York') to determine local time.
    """
    ah = config.get("active_hours", {})
    if not ah.get("enabled", False):
        return True

    start = ah.get("start_hour", 0)
    end = ah.get("end_hour", 24)
    tz_name = ah.get("timezone", "")

    # Resolve local hour in the configured timezone
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # Python < 3.9
        try:
            local_hour = datetime.now(ZoneInfo(tz_name)).hour
        except Exception:
            # Fallback: system local time if timezone lookup fails
            local_hour = datetime.now().hour
    else:
        local_hour = datetime.now().hour

    return start <= local_hour < end


# ---------------------------------------------------------------------------
# Schedule Parser
# ---------------------------------------------------------------------------
def parse_schedule(schedule_str: str) -> Optional[Dict[str, Any]]:
    """Parse schedule string into structured form.

    Supports:
        "every 30s"            -> interval-based (seconds)
        "every 2m"             -> interval-based (minutes)
        "every 6h"             -> interval-based (hours)
        "daily 07:00"          -> daily at time (UTC)
        "nightly 02:00"        -> daily at time (alias, UTC)
        "weekly Sun 20:00"     -> weekly at day+time (UTC)
        "monthly 15 10:00"     -> monthly on day-of-month at time (UTC)
        "continuous"           -> interval-based (default 300s)
        "on_demand"            -> None (triggered by pipeline chain)

    All times are UTC. For US/Eastern daytime coverage, pick UTC hours:
        EDT (Mar-Nov) 10:00 ET = 14:00 UTC
        EST (Nov-Mar) 10:00 ET = 15:00 UTC
    """
    s = schedule_str.strip().lower()

    if s == "on_demand":
        return None

    if s == "continuous":
        return {"type": "interval", "seconds": 300}

    # "every Ns" / "every Nm" / "every Nh"
    m = re.match(r"every\s+(\d+)\s*([smh])", s)
    if m:
        n = int(m.group(1))
        mult = {"s": 1, "m": 60, "h": 3600}[m.group(2)]
        return {"type": "interval", "seconds": n * mult}

    # "daily HH:MM" or "nightly HH:MM"
    m = re.match(r"(?:daily|nightly)\s+(\d{1,2}):(\d{2})", s)
    if m:
        return {"type": "daily", "hour": int(m.group(1)), "minute": int(m.group(2))}

    # "weekly DAY HH:MM"
    m = re.match(r"weekly\s+(\w+)\s+(\d{1,2}):(\d{2})", s)
    if m:
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        day = day_map.get(m.group(1)[:3], 6)
        return {"type": "weekly", "weekday": day, "hour": int(m.group(2)), "minute": int(m.group(3))}

    # "monthly DD HH:MM"
    m = re.match(r"monthly\s+(\d{1,2})\s+(\d{1,2}):(\d{2})", s)
    if m:
        return {"type": "monthly", "day": max(1, min(28, int(m.group(1)))),
                "hour": int(m.group(2)), "minute": int(m.group(3))}

    return None


def is_due(schedule: Dict[str, Any], last_run: Optional[str]) -> bool:
    """Check if a reflex is due based on its schedule and last run time."""
    now = utcnow()

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

    if schedule["type"] == "monthly":
        # Target this month at day/hour/minute (capped at 28 to avoid Feb edge cases)
        day = min(schedule["day"], 28)
        target = now.replace(day=day, hour=schedule["hour"], minute=schedule["minute"],
                             second=0, microsecond=0)
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


def evaluate_metric(metric_config: Dict[str, Any], value: float) -> bool:
    """Evaluate a success metric against its threshold."""
    if "composite" in metric_config:
        return True  # Composite metrics not evaluable without all values

    threshold = metric_config.get("threshold", 0)
    op = metric_config.get("operator", "gte")

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


def topological_reflex_order(
    due_reflexes: List[str], depends_on: Dict[str, List[str]]
) -> List[str]:
    """Order *due_reflexes* so a reflex runs after its dependencies (crx-gen-03).

    ``depends_on`` maps reflex_name → list of reflex names it should run after.
    Only edges to reflexes ALSO due this cycle are honored — a dependency that
    is not due is ignored (best-effort intra-cycle ordering, NOT a cross-cycle
    blocking DAG engine; YAGNI). The relative order of independent reflexes is
    preserved (stable). A dependency cycle is broken deterministically by
    falling back to the original position for the nodes still unresolved, so the
    scheduler can never deadlock on a mis-configured graph.
    """
    due_set = set(due_reflexes)
    # Restrict edges to nodes that are due; drop self-edges and unknowns.
    deps: Dict[str, set] = {
        name: {d for d in depends_on.get(name, []) if d in due_set and d != name}
        for name in due_reflexes
    }
    if not any(deps.values()):
        return list(due_reflexes)  # no applicable ordering constraints

    index = {name: i for i, name in enumerate(due_reflexes)}
    ordered: List[str] = []
    placed: set = set()

    # Kahn-style stable pass: repeatedly emit the earliest-indexed node whose
    # dependencies are all already placed.
    remaining = list(due_reflexes)
    while remaining:
        ready = [n for n in remaining if deps[n] <= placed]
        if not ready:
            # Cycle among the remaining nodes — break it by taking the
            # earliest-indexed remaining node so progress is guaranteed.
            ready = [min(remaining, key=lambda n: index[n])]
        ready.sort(key=lambda n: index[n])
        pick = ready[0]
        ordered.append(pick)
        placed.add(pick)
        remaining.remove(pick)
    return ordered


# ---------------------------------------------------------------------------
# Reflex State Base
# ---------------------------------------------------------------------------
class ReflexStateBase:
    """Thread-safe, DB-backed state management for a single reflex.

    Subclasses only need to set ``state_table`` to the appropriate DB table name.
    """

    state_table: str = "genesis_reflex_state"  # Override in subclass

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self._lock = threading.Lock()

    def load(self) -> Dict[str, Any]:
        """Load current state from DB, initializing if missing."""
        conn = get_connection()
        try:
            row = conn.execute(
                f"SELECT * FROM {self.state_table} WHERE reflex_name = %s",  # nosec B608 -- table/column names are internal constants, not user input
                (self.name,),
            ).fetchone()
            if row:
                return dict(row)
            now = utcnow_iso()
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {self.state_table}
                    (reflex_name, enabled, consecutive_failures, circuit_breaker_open,
                     total_runs, total_successes, total_failures, updated_at)
                VALUES (%s, %s, 0, 0, 0, 0, 0, %s)
            """,
                (self.name, 1 if self.config.get("enabled", True) else 0, now),
            )
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
            now = utcnow_iso()
            conn = get_connection()
            try:
                conn.execute(
                    f"""
                    UPDATE {self.state_table} SET
                        last_run_at = %s, consecutive_failures = 0,
                        total_runs = total_runs + 1,
                        total_successes = total_successes + 1,
                        last_metric_value = %s, last_error = NULL, updated_at = %s
                    WHERE reflex_name = %s
                """,  # nosec B608 -- table/column names are internal constants, not user input
                    (now, metric_value, now, self.name),
                )
                conn.commit()
            finally:
                conn.close()

    def record_failure(self, error: str, cb_config: Dict) -> bool:
        """Record a failed run.  Returns True if circuit breaker tripped."""
        with self._lock:
            now = utcnow_iso()
            conn = get_connection()
            try:
                state = conn.execute(
                    f"SELECT consecutive_failures FROM {self.state_table} "  # nosec B608 -- table/column names are internal constants, not user input
                    f"WHERE reflex_name = %s",
                    (self.name,),
                ).fetchone()
                failures = (state["consecutive_failures"] if state else 0) + 1
                tripped = failures >= cb_config.get("max_consecutive_failures", 3)

                conn.execute(
                    f"""
                    UPDATE {self.state_table} SET
                        last_run_at = %s, consecutive_failures = %s,
                        circuit_breaker_open = %s,
                        circuit_breaker_tripped_at = %s,
                        total_runs = total_runs + 1,
                        total_failures = total_failures + 1,
                        last_error = %s, updated_at = %s
                    WHERE reflex_name = %s
                """,  # nosec B608 -- table/column names are internal constants, not user input
                    (now, failures, 1 if tripped else 0, now if tripped else None, error[:2000], now, self.name),
                )
                conn.commit()
                return tripped
            finally:
                conn.close()

    def reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker (human-initiated)."""
        with self._lock:
            now = utcnow_iso()
            conn = get_connection()
            try:
                conn.execute(
                    f"""
                    UPDATE {self.state_table} SET
                        consecutive_failures = 0, circuit_breaker_open = 0,
                        circuit_breaker_tripped_at = NULL, updated_at = %s
                    WHERE reflex_name = %s
                """,  # nosec B608 -- table/column names are internal constants, not user input
                    (now, self.name),
                )
                conn.commit()
            finally:
                conn.close()

    def is_circuit_open(self) -> bool:
        """Check if circuit breaker is open."""
        state = self.load()
        return bool(state.get("circuit_breaker_open", 0))

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable this reflex."""
        now = utcnow_iso()
        conn = get_connection()
        try:
            conn.execute(
                f"""
                UPDATE {self.state_table} SET enabled = %s, updated_at = %s
                WHERE reflex_name = %s
            """,  # nosec B608 -- table/column names are internal constants, not user input
                (1 if enabled else 0, now, self.name),
            )
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Trust Kernel Base
# ---------------------------------------------------------------------------
class TrustKernelBase:
    """Risk tier enforcement.  Override ``can_execute`` for custom behavior."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config.get("trust_kernel", {})
        self.risk_tiers = self.config.get("risk_tiers", {})
        self.allowed_actions = self.config.get("allowed_actions", {})

    def can_execute(self, risk_tier: str, action: str = "run") -> Tuple[bool, str]:
        """Check if an action is allowed for the given risk tier."""
        if self.allowed_actions:
            allowed = self.allowed_actions.get(risk_tier, [])
            if action not in allowed:
                return False, f"Action '{action}' not in whitelist for tier '{risk_tier}'"
        return True, "approved"

    def requires_sandbox(self, risk_tier: str) -> bool:
        tier_config = self.risk_tiers.get(risk_tier, {})
        return tier_config.get("sandbox", False)

    def requires_human_approval(self, risk_tier: str) -> bool:
        tier_config = self.risk_tiers.get(risk_tier, {})
        return tier_config.get("approval") == "human"

    def requires_tests(self, risk_tier: str) -> bool:
        tier_config = self.risk_tiers.get(risk_tier, {})
        return tier_config.get("require_tests_pass", False)


# ---------------------------------------------------------------------------
# Daemon Base
# ---------------------------------------------------------------------------
class DaemonBase(abc.ABC):
    """Abstract base for schedule-driven reflex daemons.

    Subclasses must define:
        - daemon_name: str          — Display name (e.g. "Genesis Daemon")
        - daemon_version: str       — Version string
        - config_path: Path         — YAML config file path
        - pid_file: Path            — PID file location
        - env_enabled_var: str      — Master enable env var name
        - env_reflex_prefix: str    — Per-reflex env var prefix
        - event_prefix: str         — Audit event prefix (e.g. "genesis", "pg")
        - reflex_names: List[str]   — Ordered list of reflex names
        - id_prefix: str            — ID generation prefix

    Subclasses must implement:
        - ensure_tables()           — Create daemon-specific DB tables
        - log_audit(...)            — Write to daemon-specific audit table
        - create_reflex_state(...)  — Factory for ReflexStateBase subclass
        - create_trust_kernel(...)  — Factory for TrustKernelBase subclass
        - run_reflex_impl(...)      — Execute a single reflex
        - get_extra_status(...)     — Return extra status fields (optional)
        - add_cli_args(parser)      — Add daemon-specific CLI args (optional)
        - handle_cli_args(args, daemon) — Handle custom CLI args (optional)
    """

    # --- Subclass must define these ---
    daemon_name: str = "Daemon"
    daemon_version: str = "0.0.0"
    config_path: Path = BASE_DIR / "args" / "daemon_config.yaml"
    pid_file: Path = BASE_DIR / ".tmp" / "daemon" / "daemon.pid"
    env_enabled_var: str = "ICDEV_DAEMON_ENABLED"
    env_reflex_prefix: str = "ICDEV_DAEMON_REFLEX"
    event_prefix: str = "daemon"
    reflex_names: List[str] = []
    id_prefix: str = "dmn"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.trust = self.create_trust_kernel(config)
        self.reflex_states: Dict[str, ReflexStateBase] = {}
        self.schedules: Dict[str, Dict[str, Any]] = {}
        self._shutdown_event = threading.Event()

        for name in self.reflex_names:
            reflex_config = config.get("reflexes", {}).get(name, {})
            self.reflex_states[name] = self.create_reflex_state(name, reflex_config)

            schedule_str = reflex_config.get("schedule", "")
            if schedule_str == "continuous":
                interval = reflex_config.get("interval_seconds", 300)
                self.schedules[name] = {"type": "interval", "seconds": interval}
            else:
                parsed = parse_schedule(schedule_str)
                if parsed:
                    self.schedules[name] = parsed

    # --- Signal handling ---
    def install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers for graceful shutdown."""

        def handler(signum: int, frame: Any) -> None:
            print(f"\nINFO: {self.daemon_name} received signal {signum}, initiating graceful shutdown...")
            self._shutdown_event.set()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    # --- Abstract methods ---
    @abc.abstractmethod
    def ensure_tables(self) -> None:
        """Create daemon-specific DB tables."""

    @abc.abstractmethod
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
        """Write to daemon-specific audit table (append-only, NIST AU)."""

    @abc.abstractmethod
    def create_reflex_state(self, name: str, config: Dict[str, Any]) -> ReflexStateBase:
        """Factory: create a ReflexStateBase subclass instance."""

    @abc.abstractmethod
    def create_trust_kernel(self, config: Dict[str, Any]) -> TrustKernelBase:
        """Factory: create a TrustKernelBase subclass instance."""

    @abc.abstractmethod
    def run_reflex_impl(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Execute a single reflex.  Returns (success, metric_value, details)."""

    # --- Optional overrides ---
    def get_extra_status(self) -> Dict[str, Any]:
        """Return extra status fields for daemon-specific info."""
        return {}

    def add_cli_args(self, parser: argparse.ArgumentParser) -> None:
        """Add daemon-specific CLI arguments."""
        pass

    def handle_cli_args(self, args: argparse.Namespace) -> Optional[bool]:
        """Handle daemon-specific CLI args.  Return True if handled."""
        return None

    def on_reflex_completed(self, name: str, result: Dict[str, Any]) -> None:
        """Hook called after a reflex completes successfully in the due loop.
        Override for pipeline chain triggering, etc."""
        pass

    # --- Config loading ---
    @classmethod
    def load_config(cls) -> Dict[str, Any]:
        """Load daemon configuration from YAML with env var overrides."""
        try:
            import yaml
        except ImportError:
            return cls.default_config()

        if not cls.config_path.exists():
            return cls.default_config()

        with open(cls.config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        # Master switch env override
        env_val = os.environ.get(cls.env_enabled_var, "").lower()
        if env_val in ("true", "1"):
            config["enabled"] = True
        elif env_val in ("false", "0"):
            config["enabled"] = False

        # Per-reflex env overrides
        for name in cls.reflex_names:
            env_key = f"{cls.env_reflex_prefix}_{name.upper()}_ENABLED"
            env_v = os.environ.get(env_key, "").lower()
            if env_v in ("true", "1"):
                config.setdefault("reflexes", {}).setdefault(name, {})["enabled"] = True
            elif env_v in ("false", "0"):
                config.setdefault("reflexes", {}).setdefault(name, {})["enabled"] = False

        return config

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
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
            "reflexes": {name: {"enabled": False, "risk_tier": RISK_GREEN} for name in cls.reflex_names},
        }

    # --- Checkpoint / Resumability (adapted from Agent Harness pattern) ---
    def save_checkpoint(self, reflex_name: str, phase: str, partial_results: Dict[str, Any]) -> str:
        """Save a mid-reflex checkpoint for resumability.

        When a reflex has multi-step execution (e.g. scanning 50 items),
        it can save checkpoints so that interruption doesn't lose progress.
        On restart, ``load_checkpoint`` detects partial state and the reflex
        can resume from the last completed phase.

        Args:
            reflex_name: Which reflex is checkpointing.
            phase: Current phase/step identifier (e.g. "scan_item_23").
            partial_results: Accumulated results so far.

        Returns:
            Checkpoint ID.
        """
        checkpoint_id = generate_id("ckpt")
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO daemon_checkpoints
                    (id, daemon_name, reflex_name, phase, partial_results,
                     created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(daemon_name, reflex_name) DO UPDATE SET
                    id = excluded.id,
                    phase = excluded.phase,
                    partial_results = excluded.partial_results,
                    created_at = excluded.created_at
            """,
                (
                    checkpoint_id,
                    self.daemon_name,
                    reflex_name,
                    phase,
                    json.dumps(partial_results, default=str),
                    utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return checkpoint_id

    def load_checkpoint(self, reflex_name: str) -> Optional[Dict[str, Any]]:
        """Load the most recent checkpoint for a reflex, if any.

        Returns:
            Dict with ``phase`` and ``partial_results``, or None if no
            checkpoint exists.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM daemon_checkpoints WHERE daemon_name = %s AND reflex_name = %s",
                (self.daemon_name, reflex_name),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            if result.get("partial_results"):
                try:
                    result["partial_results"] = json.loads(result["partial_results"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return result
        finally:
            conn.close()

    def clear_checkpoint(self, reflex_name: str) -> None:
        """Remove checkpoint after a reflex completes successfully."""
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM daemon_checkpoints WHERE daemon_name = %s AND reflex_name = %s",
                (self.daemon_name, reflex_name),
            )
            conn.commit()
        finally:
            conn.close()

    def _ensure_checkpoint_table(self) -> None:
        """Create the daemon_checkpoints table if needed."""
        conn = get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daemon_checkpoints (
                    id              TEXT PRIMARY KEY,
                    daemon_name     TEXT NOT NULL,
                    reflex_name     TEXT NOT NULL,
                    phase           TEXT NOT NULL,
                    partial_results TEXT,
                    created_at      TEXT NOT NULL,
                    UNIQUE(daemon_name, reflex_name)
                );
            """)
            conn.commit()
        finally:
            conn.close()

    # --- Core lifecycle ---
    def _init_states(self) -> None:
        """Ensure all reflex states exist in DB."""
        self._ensure_checkpoint_table()
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
            self.log_audit(f"{self.event_prefix}.reflex.skipped", name, risk_tier, {"reason": "circuit_breaker_open"})
            return {"status": "skipped", "reason": "circuit_breaker_open"}

        current_state = state.load()
        if not current_state.get("enabled", 1):
            return {"status": "skipped", "reason": "disabled"}

        # Execute
        self.log_audit(f"{self.event_prefix}.reflex.started", name, risk_tier)
        start_time = time.monotonic()

        try:
            success, metric_value, details = self.run_reflex_impl(name, reflex_config, self.trust)
            duration_ms = int((time.monotonic() - start_time) * 1000)

            metric_config = reflex_config.get("success_metric", {})
            metric_name = metric_config.get("name", "default")
            metric_passed = evaluate_metric(metric_config, metric_value) if success else False

            if success and metric_passed:
                state.record_success(metric_value)
                self.clear_checkpoint(name)  # Resumability: clear on success
                # SILENT suppression: skip audit log when reflex has nothing to report
                # (metric_value == 0 and details indicate no-op)
                silent = details.get("status") in (
                    "no_due_tasks",
                    "air_gapped",
                    "no_changes",
                    "nothing_to_do",
                    "empty",
                ) or (metric_value == 0 and not details.get("tasks"))
                if not silent:
                    self.log_audit(
                        f"{self.event_prefix}.reflex.completed",
                        name,
                        risk_tier,
                        details,
                        success=True,
                        duration_ms=duration_ms,
                        metric_name=metric_name,
                        metric_value=metric_value,
                    )
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
                event = (
                    f"{self.event_prefix}.circuit_breaker.tripped" if tripped else f"{self.event_prefix}.reflex.failed"
                )
                self.log_audit(
                    event,
                    name,
                    risk_tier,
                    {**details, "circuit_breaker_tripped": tripped},
                    success=False,
                    duration_ms=duration_ms,
                    metric_name=metric_name,
                    metric_value=metric_value,
                )
                if tripped:
                    print(
                        f"WARNING: Circuit breaker TRIPPED for reflex '{name}' "
                        f"after {cb_config.get('max_consecutive_failures', 3)} "
                        f"consecutive failures"
                    )
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
            self.log_audit(
                f"{self.event_prefix}.reflex.failed",
                name,
                risk_tier,
                {"error": error_msg, "exception": True},
                success=False,
                duration_ms=duration_ms,
            )
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
        due_reflexes = []

        # Active hours gate — skip entire cycle if outside window
        if not in_active_hours(self.config):
            return results

        # First pass: determine which reflexes are due
        for name in self.reflex_names:
            schedule = self.schedules.get(name)
            if not schedule:
                continue
            state = self.reflex_states[name].load()
            if not state.get("enabled", 1):
                continue
            if state.get("circuit_breaker_open", 0):
                continue
            if is_due(schedule, state.get("last_run_at")):
                due_reflexes.append(name)

        # crx-gen-03: honor optional `reflexes.<name>.depends_on: [names]` so a
        # reflex runs after its dependencies WITHIN this cycle (best-effort intra-
        # cycle ordering; a dependency not due this cycle is simply ignored).
        reflexes_cfg = self.config.get("reflexes", {})
        depends_on = {
            name: list(reflexes_cfg.get(name, {}).get("depends_on", []) or [])
            for name in due_reflexes
        }
        if any(depends_on.values()):
            due_reflexes = topological_reflex_order(due_reflexes, depends_on)

        total_due = len(due_reflexes)

        for idx, name in enumerate(due_reflexes):
            if self._shutdown_event.is_set():
                break

            print(f"INFO: Running reflex '{name}' (due)")

            # SSE progress broadcast (best-effort)
            try:
                from tools.dashboard.sse_manager import emit_progress

                emit_progress(
                    f"{self.event_prefix}-cycle",
                    f"{self.event_prefix}_reflex",
                    name,
                    idx,
                    total_due,
                    detail=f"Running reflex: {name}",
                )
            except Exception:
                pass

            result = self.run_reflex(name)
            results.append(result)
            # Hook for pipeline chain triggering
            self.on_reflex_completed(name, result)

        # Final SSE progress: cycle complete
        if due_reflexes:
            try:
                from tools.dashboard.sse_manager import emit_progress

                emit_progress(
                    f"{self.event_prefix}-cycle",
                    f"{self.event_prefix}_reflex",
                    "cycle_complete",
                    total_due,
                    total_due,
                    status="completed",
                    detail=f"{len(results)} reflexes executed",
                )
            except Exception:
                pass

        return results

    def run_forever(self) -> None:
        """Main daemon loop."""
        enabled_count = sum(1 for n in self.reflex_names if self.config.get("reflexes", {}).get(n, {}).get("enabled"))
        print(f"INFO: {self.daemon_name} v{self.daemon_version} starting...")
        print(f"INFO: {enabled_count} reflexes enabled")

        self.log_audit(
            f"{self.event_prefix}.daemon.started",
            details={
                "version": self.daemon_version,
                "enabled_reflexes": [
                    n for n in self.reflex_names if self.config.get("reflexes", {}).get(n, {}).get("enabled")
                ],
            },
        )

        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(os.getpid()))
        self._init_states()

        try:
            check_interval = (
                self.config.get("trust_kernel", {}).get("kill_switch", {}).get("check_interval_seconds", 10)
            )

            while not self._shutdown_event.is_set():
                # Check kill switch
                if not self.config.get("enabled", False):
                    env_val = os.environ.get(self.env_enabled_var, "").lower()
                    if env_val not in ("true", "1"):
                        self.log_audit(
                            f"{self.event_prefix}.kill_switch.activated", details={"reason": "config_disabled"}
                        )
                        print(f"INFO: {self.daemon_name} disabled -- shutting down")
                        break

                # Note: active_hours time gating is handled inside run_due_reflexes()
                # via in_active_hours(config). The hardcoded 23:00-08:00 quiet-hours
                # block was removed so the config file (args/genesis_config.yaml)
                # controls scheduling windows.

                try:
                    self.run_due_reflexes()
                except Exception as e:
                    print(f"ERROR: Daemon loop error: {e}")
                    self.log_audit(f"{self.event_prefix}.daemon.error", details={"error": str(e)})

                # Sleep in small increments for responsive shutdown
                for _ in range(check_interval):
                    if self._shutdown_event.is_set():
                        break
                    time.sleep(1)

        finally:
            self.log_audit(f"{self.event_prefix}.daemon.stopped", details={"reason": "shutdown"})
            print(f"INFO: {self.daemon_name} stopped")
            if self.pid_file.exists():
                self.pid_file.unlink()

    def get_status(self) -> Dict[str, Any]:
        """Return current daemon and reflex status."""
        reflexes = {}
        for name in self.reflex_names:
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
        audit_table = self._get_audit_table()
        conn = get_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM {audit_table} WHERE created_at > %s",  # nosec B608 -- table/column names are internal constants, not user input
                ((utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
            ).fetchone()
            events_24h = row["cnt"] if row else 0
        except Exception:
            events_24h = 0
        finally:
            conn.close()

        result = {
            "daemon": {
                "version": self.daemon_version,
                "enabled": self.config.get("enabled", False),
                "pid": os.getpid(),
            },
            "reflexes": reflexes,
            "audit": {"events_last_24h": events_24h},
            "timestamp": utcnow_iso(),
        }

        # Merge extra status from subclass
        extra = self.get_extra_status()
        if extra:
            result.update(extra)

        return result

    def _get_audit_table(self) -> str:
        """Return the audit table name.  Override if non-standard."""
        return f"{self.event_prefix.replace('.', '_')}_audit"

    # --- CLI ---
    def run_cli(self) -> None:
        """Standard CLI entry point."""
        # Fail closed before doing any work if a stale 'tools' tree shadows this
        # checkout (see verify_tools_tree_integrity). shx-safe-05.
        verify_tools_tree_integrity(BASE_DIR)
        parser = argparse.ArgumentParser(description=f"{self.daemon_name} — ICDEV™ Autonomous Engine")
        parser.add_argument("--once", action="store_true", help="Single pass: run all due reflexes then exit")
        parser.add_argument("--status", action="store_true", help="Show daemon & reflex status")
        parser.add_argument("--reflex", type=str, metavar="NAME", help="Run one reflex immediately")
        parser.add_argument("--enable", type=str, metavar="NAME", help="Enable a reflex")
        parser.add_argument("--disable", type=str, metavar="NAME", help="Disable a reflex")
        parser.add_argument("--reset", type=str, metavar="NAME", help="Reset circuit breaker for a reflex")
        parser.add_argument("--json", action="store_true", help="JSON output")
        self.add_cli_args(parser)
        args = parser.parse_args()

        self.install_signal_handlers()
        self.ensure_tables()

        # Handle subclass-specific CLI args first
        handled = self.handle_cli_args(args)
        if handled:
            return

        # --- Status ---
        if args.status:
            status = self.get_status()
            if args.json:
                print(json.dumps(status, indent=2))
            else:
                self._print_status_human(status)
            return

        # --- Enable/Disable/Reset ---
        if args.enable:
            self._cli_enable_disable_reset(args.enable, "enable", args.json)
            return
        if args.disable:
            self._cli_enable_disable_reset(args.disable, "disable", args.json)
            return
        if args.reset:
            self._cli_enable_disable_reset(args.reset, "reset", args.json)
            return

        # --- Run one reflex ---
        if args.reflex:
            name = args.reflex
            if name not in self.reflex_names:
                print(f"ERROR: Unknown reflex '{name}'", file=sys.stderr)
                sys.exit(1)
            self._init_states()
            result = self.run_reflex(name)
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
            self._init_states()
            results = self.run_due_reflexes()
            if args.json:
                print(json.dumps({"mode": "once", "results": results}, indent=2))
            else:
                print(f"Ran {len(results)} reflex(es)")
                for r in results:
                    print(f"  {r.get('reflex', '?')}: {r.get('status', 'unknown')}")
            return

        # --- Daemon mode ---
        if not self.config.get("enabled", False):
            env_val = os.environ.get(self.env_enabled_var, "").lower()
            if env_val not in ("true", "1"):
                print(
                    f"ERROR: {self.daemon_name} is disabled. "
                    f"Set {self.env_enabled_var}=true or "
                    f"enabled: true in {self.config_path}",
                    file=sys.stderr,
                )
                sys.exit(1)
            self.config["enabled"] = True

        self.run_forever()

    def _cli_enable_disable_reset(self, name: str, action: str, json_output: bool) -> None:
        """Handle enable/disable/reset CLI actions."""
        if name not in self.reflex_names:
            print(f"ERROR: Unknown reflex '{name}'", file=sys.stderr)
            sys.exit(1)

        if action == "enable":
            self.reflex_states[name].set_enabled(True)
            self.log_audit(f"{self.event_prefix}.reflex.enabled", name)
            msg = f"Reflex '{name}' enabled"
            result = {"action": "enabled", "reflex": name}
        elif action == "disable":
            self.reflex_states[name].set_enabled(False)
            self.log_audit(f"{self.event_prefix}.reflex.disabled", name)
            msg = f"Reflex '{name}' disabled"
            result = {"action": "disabled", "reflex": name}
        else:  # reset
            self.reflex_states[name].reset_circuit_breaker()
            self.log_audit(f"{self.event_prefix}.circuit_breaker.reset", name)
            msg = f"Circuit breaker reset for '{name}'"
            result = {"action": "circuit_breaker_reset", "reflex": name}

        print(json.dumps(result) if json_output else msg)

    def _print_status_human(self, status: Dict[str, Any]) -> None:
        """Print status in human-readable format."""
        d = status["daemon"]
        print(f"{self.daemon_name} v{d['version']}")
        print(f"  Enabled: {d['enabled']}")
        print(f"  PID: {d['pid']}")
        print(f"  Audit events (24h): {status['audit']['events_last_24h']}")
        print()
        print(f"{'Reflex':<12} {'Tier':<8} {'Enabled':<9} {'CB':<5} {'Runs':<6} {'OK':<6} {'Fail':<6} {'Last Run'}")
        print("-" * 90)
        for name, r in status["reflexes"].items():
            cb = "OPEN" if r["circuit_breaker_open"] else "ok"
            last = r["last_run_at"][:16] if r["last_run_at"] else "never"
            print(
                f"{name:<12} {r['risk_tier']:<8} "
                f"{str(r['enabled']):<9} {cb:<5} "
                f"{r['total_runs']:<6} {r['total_successes']:<6} "
                f"{r['total_failures']:<6} {last}"
            )
