#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Daemon — always-on autonomous research engine (D-GEN-1).

Runs 14 Reflexes as managed threads within a single process.  Each Reflex
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

import importlib
import json
import os
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
    RISK_GREEN,
    generate_id,
    utcnow,
    utcnow_iso,
    sha256_hex,
)
from tools.db.storage import get_connection  # noqa: E402
from tools.genesis.constants import TRUST_MODES  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_VERSION = "2.0.0-alpha"
CONFIG_PATH = BASE_DIR / "args" / "genesis_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "genesis" / "daemon.pid"
STATE_FILE = BASE_DIR / ".tmp" / "genesis" / "state.json"

REFLEX_NAMES = [
    "research",
    "scout",
    "audit",
    "comply",
    "ingest",
    "market",
    "report",
    "publish",
    "test",
    "learn",
    "heal",
    "evolve",
    "docs",
    "experiment",
    "synthesize",
    "kanban",
    "oracle",
    "goal_learner",
    "remediation_lens",
    "awareness",
    "canvas_indexer",
    "alphadesk_trap_scenarios",
    "migration_canvas",
    "academy_reflex",
    "e2e_runner",
    "log_triage",
]

# Backward-compat aliases for module-level access used by other code
_generate_id = generate_id
_utcnow = utcnow
_utcnow_iso = utcnow_iso
_sha256 = sha256_hex


# ---------------------------------------------------------------------------
# Genesis-specific ReflexState (uses genesis_reflex_state table)
# ---------------------------------------------------------------------------
class GenesisReflexState(ReflexStateBase):
    state_table = "genesis_reflex_state"


# ---------------------------------------------------------------------------
# Genesis Trust Kernel (supports ORANGE tier with human approval)
# ---------------------------------------------------------------------------
class GenesisTrustKernel(TrustKernelBase):
    """Enforces risk tiers, approval gates, and action whitelists (D-GEN-3)."""

    def can_execute(self, risk_tier: str, action: str = "run") -> Tuple[bool, str]:
        if self.allowed_actions:
            allowed = self.allowed_actions.get(risk_tier, [])
            if action not in allowed:
                return False, f"Action '{action}' not in whitelist for tier '{risk_tier}'"
        return True, "approved"


# ---------------------------------------------------------------------------
# Genesis Daemon
# ---------------------------------------------------------------------------
class GenesisDaemon(DaemonBase):
    """Main daemon process managing 14 Reflexes (D-GEN-1)."""

    daemon_name = "Genesis Daemon"
    daemon_version = DAEMON_VERSION
    config_path = CONFIG_PATH
    pid_file = PID_FILE
    env_enabled_var = "ICDEV_GENESIS_ENABLED"
    env_reflex_prefix = "ICDEV_GENESIS_REFLEX"
    event_prefix = "genesis"
    reflex_names = REFLEX_NAMES
    id_prefix = "gen"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        raw = config.get("trust_mode", "full")
        self.trust_mode: str = raw if raw in TRUST_MODES else "full"

    def ensure_tables(self) -> None:
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
        gkp_id: str = None,
        **kwargs,
    ) -> str:
        """Append an audit event (D-GEN-10: append-only, NIST AU)."""
        audit_id = generate_id("aud")
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO genesis_audit
                    (id, event_type, reflex_name, risk_tier, details, success,
                     duration_ms, metric_name, metric_value, gkp_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    gkp_id,
                    utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return audit_id

    def create_reflex_state(self, name: str, config: Dict[str, Any]) -> ReflexStateBase:
        return GenesisReflexState(name, config)

    def create_trust_kernel(self, config: Dict[str, Any]) -> TrustKernelBase:
        return GenesisTrustKernel(config)

    def run_reflex_impl(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Execute a single reflex via tools/genesis/reflexes/<name>.py."""
        risk_tier = config.get("risk_tier", RISK_GREEN)

        # ORANGE tier — log that human review is needed
        if trust.requires_human_approval(risk_tier):
            return True, 0.0, {"status": "awaiting_human_approval", "risk_tier": risk_tier}

        try:
            module = importlib.import_module(f"tools.genesis.reflexes.{name}")
            if hasattr(module, "run"):
                # [DISPATCH POINT] Centralized reflex invocation via importlib.
                # All 22 reflexes in REFLEX_NAMES are dispatched here.
                # observe() wrapper should wrap module.run(config, trust) here:
                #   result = observe(name, module.run, config, trust)
                result = module.run(config, trust)
                success = result.get("success", False)
                if success:
                    try:
                        from tools.aisg.roi_tracker import emit_roi_event
                        emit_roi_event(
                            "genesis_reflex",
                            f"Reflex '{name}' executed successfully",
                            triggered_by="genesis_daemon",
                        )
                    except Exception:
                        pass
                return (
                    success,
                    result.get("metric_value", 0.0),
                    result.get("details", {}),
                )
        except ImportError:
            pass
        except Exception as e:
            return False, 0.0, {"error": str(e), "stage": "reflex_execution"}

        # Stub mode
        return (
            True,
            0.0,
            {
                "status": "stub",
                "message": f"Reflex '{name}' not yet implemented -- stub mode (success)",
            },
        )

    def on_reflex_completed(self, name: str, result: Dict[str, Any]) -> None:
        """Post-reflex hook: run convergence gate and stagnation detector."""
        conv_config = self.config.get("convergence", {})
        stag_config = self.config.get("stagnation", {})

        if not conv_config.get("enabled", False):
            return

        try:
            from tools.genesis.convergence import ConvergenceGate

            metric_value = result.get("metric_value", 0.0)
            if isinstance(metric_value, dict):
                metric_value = list(metric_value.values())[0] if metric_value else 0.0

            output_text = json.dumps(result.get("details", {}))
            state = self.reflex_states.get(name)
            generation = state.load().get("total_runs", 0) if state else 0

            # Get reflex description from config for goal drift
            reflex_cfg = self.config.get("reflexes", {}).get(name, {})
            description = reflex_cfg.get("description", name)

            gate = ConvergenceGate(conv_config)
            conv_result = gate.evaluate(name, metric_value, output_text, generation, description)

            self.log_audit(
                "genesis.convergence.evaluated",
                name,
                details={
                    "combined_drift": conv_result.get("combined_drift"),
                    "converged": conv_result.get("converged"),
                    "recommendation": conv_result.get("recommendation"),
                },
            )

            # Run stagnation detector if convergence flags issues
            if stag_config.get("enabled", False) and (
                conv_result.get("converged") or conv_result.get("retrospective_triggered")
            ):
                from tools.genesis.stagnation_detector import StagnationDetector

                detector = StagnationDetector(stag_config, self.config.get("llm", {}))
                detection = detector.detect(name)

                if detection.get("stagnation_detected"):
                    context = json.dumps(result.get("details", {}))
                    plateau_result = detector.break_plateau(name, detection["pattern_type"], context)
                    self.log_audit(
                        "genesis.stagnation.detected",
                        name,
                        details={
                            "pattern": detection["pattern_type"],
                            "persona": plateau_result.get("persona_used"),
                            "alternative": plateau_result.get("selected_alternative"),
                        },
                    )
        except ImportError:
            pass  # Convergence/stagnation modules not yet available
        except Exception as e:
            print(f"WARNING: Convergence/stagnation hook error: {e}")

    def _get_audit_table(self) -> str:
        return "genesis_audit"

    def get_extra_status(self) -> Dict[str, Any]:
        """Add Genesis-specific status fields."""
        return {
            "daemon": {
                "version": self.daemon_version,
                "enabled": self.config.get("enabled", False),
                "pid": os.getpid(),
                "pid_file": str(PID_FILE),
                "uptime": "running" if PID_FILE.exists() else "stopped",
            },
            "trust_kernel": {
                "circuit_breaker_max_failures": self.config.get("trust_kernel", {})
                .get("circuit_breaker", {})
                .get("max_consecutive_failures", 3),
            },
        }


# ---------------------------------------------------------------------------
# Backward-compat module-level functions (used by dashboard API, promoter, etc.)
# ---------------------------------------------------------------------------
def _load_config() -> Dict[str, Any]:
    """Load genesis configuration (backward-compat wrapper)."""
    return GenesisDaemon.load_config()


def _ensure_tables() -> None:
    """Create genesis tables (backward-compat wrapper)."""
    config = _load_config()
    daemon = GenesisDaemon(config)
    daemon.ensure_tables()


def _log_audit(
    event_type: str,
    reflex_name: str = None,
    risk_tier: str = None,
    details: Dict = None,
    success: bool = None,
    duration_ms: int = None,
    metric_name: str = None,
    metric_value: float = None,
    gkp_id: str = None,
) -> str:
    """Append an audit event (backward-compat wrapper)."""
    audit_id = generate_id("aud")
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO genesis_audit
                (id, event_type, reflex_name, risk_tier, details, success,
                 duration_ms, metric_name, metric_value, gkp_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                gkp_id,
                utcnow_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return audit_id


# Keep old ReflexState accessible as module-level class
ReflexState = GenesisReflexState

# Keep old TrustKernel accessible as module-level class
TrustKernel = GenesisTrustKernel


# Keep schedule helpers accessible at module level
def _parse_schedule(s):
    return __import__("tools.daemon.base", fromlist=["parse_schedule"]).parse_schedule(s)


def _is_due(sched, last):
    return __import__("tools.daemon.base", fromlist=["is_due"]).is_due(sched, last)


def _evaluate_metric(mc, v):
    return __import__("tools.daemon.base", fromlist=["evaluate_metric"]).evaluate_metric(mc, v)


# Keep _run_reflex accessible for reflexes that call it directly
def _run_reflex(name: str, config: Dict[str, Any], trust) -> Tuple[bool, float, Dict]:
    """Execute a single reflex (backward-compat wrapper)."""
    risk_tier = config.get("risk_tier", RISK_GREEN)
    if trust.requires_human_approval(risk_tier):
        return True, 0.0, {"status": "awaiting_human_approval", "risk_tier": risk_tier}
    try:
        module = importlib.import_module(f"tools.genesis.reflexes.{name}")
        if hasattr(module, "run"):
            # [DISPATCH POINT - backward-compat] Same importlib pattern as run_reflex_impl.
            # observe() wrapper should also be added here if _run_reflex is called directly.
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
            "message": f"Reflex '{name}' not yet implemented -- stub mode (success)",
        },
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point."""
    config = GenesisDaemon.load_config()
    daemon = GenesisDaemon(config)
    daemon.run_cli()


if __name__ == "__main__":
    main()
