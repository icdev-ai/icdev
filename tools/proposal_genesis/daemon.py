#!/usr/bin/env python3
# CUI // SP-CTI
"""Proposal Genesis Daemon — autonomous proposal intelligence engine (D-PG-1).

Runs 25 Reflexes across 4 phases (CAPTURE, PROPOSE, DELIVER, LEARN) as
managed threads within a single process.  Phase A implements R1, R5, R6, R7, R8.

Usage:
    python tools/proposal_genesis/daemon.py                       # Run as daemon
    python tools/proposal_genesis/daemon.py --once                # Single pass
    python tools/proposal_genesis/daemon.py --status --json       # Show status
    python tools/proposal_genesis/daemon.py --reflex discover     # Run one reflex
    python tools/proposal_genesis/daemon.py --pipeline <opp_id>   # Full discover->polish
    python tools/proposal_genesis/daemon.py --enable discover     # Enable a reflex
    python tools/proposal_genesis/daemon.py --disable discover    # Disable a reflex
    python tools/proposal_genesis/daemon.py --reset discover      # Reset circuit breaker
    python tools/proposal_genesis/daemon.py --json                # JSON output
"""

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    RISK_YELLOW,
    generate_id,
    utcnow,
    utcnow_iso,
    sha256_hex,
)
from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_VERSION = "1.0.0-alpha"
CONFIG_PATH = BASE_DIR / "args" / "proposal_genesis_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "proposal_genesis" / "daemon.pid"

# --- Status-display thresholds (AI-ify opp 5380, hardcoded_threshold -> --
# anomaly_detection): named constants replace inline magic numbers in the
# human-readable status table so layout/truncation caps are tunable in one
# place. Mirrors the constant-extraction convention used by the sibling
# discover/review/adapt reflexes (33d08035b, a64c26a2b).
_STATUS_SEPARATOR_WIDTH = 100  # width of the "-" rule under the reflex header
_LAST_RUN_DISPLAY_CHARS = 16  # chars of the ISO timestamp shown in "Last Run"

REFLEX_NAMES = [
    # Phase 1: CAPTURE
    "discover",
    "scout",
    "shape",
    "engage",
    "regulate",
    "vehicle",
    "talent",
    "team",
    # Phase 2: PROPOSE
    "extract",
    "map",
    "draft",
    "polish",
    "decide",
    "review",
    "price",
    "comply_cmmc",
    "trace",
    # Phase 3: DELIVER
    "monitor",
    "fulfill",
    "publish",
    "bridge",
    # Phase 4: LEARN
    "analyze",
    "train",
    "adapt",
]

# Phase groupings (original 14 + 11 new)
PHASE_A_REFLEXES = ["discover", "extract", "map", "draft", "polish"]
PHASE_B_REFLEXES = ["scout", "shape"]
PHASE_C_REFLEXES = ["engage"]
PHASE_D_REFLEXES = ["publish"]
PHASE_E_REFLEXES = ["monitor", "fulfill"]
PHASE_F_REFLEXES = ["decide", "analyze", "train"]
# 3-Engine Research Enhancement reflexes
PHASE_G_REFLEXES = ["review", "price", "comply_cmmc", "trace"]
PHASE_H_REFLEXES = ["regulate", "vehicle", "talent", "team"]
PHASE_I_REFLEXES = ["bridge", "adapt"]

# Pipeline chain: discover -> extract -> map -> draft -> polish -> review -> decide
PIPELINE_CHAIN = {
    "discover": "extract",
    "extract": "map",
    "map": "draft",
    "draft": "polish",
    "polish": "review",
    "review": "decide",
}

# Backward-compat aliases
_generate_id = generate_id
_utcnow = utcnow
_utcnow_iso = utcnow_iso
_sha256 = sha256_hex


# ---------------------------------------------------------------------------
# Proposal Genesis-specific ReflexState
# ---------------------------------------------------------------------------
class PGReflexState(ReflexStateBase):
    state_table = "pg_proposal_genesis_state"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        # PG default: enabled only for PHASE_A + PHASE_B + PHASE_C reflexes
        # (handled via config, but base default is True — override)


# ---------------------------------------------------------------------------
# Proposal Genesis Trust Kernel (no ORANGE tier per D-PG-8)
# ---------------------------------------------------------------------------
class PGTrustKernel(TrustKernelBase):
    def can_execute(self, risk_tier: str, action: str = "run") -> Tuple[bool, str]:
        if risk_tier in (RISK_GREEN, RISK_YELLOW):
            return True, "approved"
        return False, f"Unknown risk tier: {risk_tier}"


# ---------------------------------------------------------------------------
# Proposal Genesis Daemon
# ---------------------------------------------------------------------------
class ProposalGenesisDaemon(DaemonBase):
    """Main daemon managing 25 Proposal Reflexes (D-PG-1)."""

    daemon_name = "Proposal Genesis Daemon"
    daemon_version = DAEMON_VERSION
    config_path = CONFIG_PATH
    pid_file = PID_FILE
    env_enabled_var = "ICDEV_PROPOSAL_GENESIS_ENABLED"
    env_reflex_prefix = "ICDEV_PG_REFLEX"
    event_prefix = "pg"
    reflex_names = REFLEX_NAMES
    id_prefix = "pg"
    service_name = "proposal-genesis"       # coordination session-id prefix (autonomy-id-05)
    service_agent = "proposal_genesis"

    def ensure_tables(self) -> None:
        conn = get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pg_proposal_genesis_audit (
                    id              TEXT PRIMARY KEY,
                    event_type      TEXT NOT NULL,
                    reflex_name     TEXT,
                    risk_tier       TEXT,
                    opportunity_id  TEXT,
                    details         TEXT,
                    success         INTEGER,
                    duration_ms     INTEGER,
                    metric_name     TEXT,
                    metric_value    REAL,
                    created_at      TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pg_proposal_genesis_state (
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
                CREATE TABLE IF NOT EXISTS pg_proposal_genesis_config (
                    key     TEXT PRIMARY KEY,
                    value   TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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
        opportunity_id: str = None,
        **kwargs,
    ) -> str:
        audit_id = generate_id("pga")
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO pg_proposal_genesis_audit
                    (id, event_type, reflex_name, risk_tier, opportunity_id,
                     details, success, duration_ms, metric_name, metric_value,
                     created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (
                    audit_id,
                    event_type,
                    reflex_name,
                    risk_tier,
                    opportunity_id,
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
        return PGReflexState(name, config)

    def create_trust_kernel(self, config: Dict[str, Any]) -> TrustKernelBase:
        return PGTrustKernel(config)

    def run_reflex_impl(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Execute a single reflex via tools/proposal_genesis/reflexes/<name>.py."""
        risk_tier = config.get("risk_tier", RISK_GREEN)
        ok, reason = trust.can_execute(risk_tier)
        if not ok:
            return False, 0.0, {"status": "blocked", "reason": reason}

        try:
            module = importlib.import_module(f"tools.proposal_genesis.reflexes.{name}")
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
                "message": f"Reflex '{name}' not yet implemented -- stub mode",
            },
        )

    def _get_audit_table(self) -> str:
        return "pg_proposal_genesis_audit"

    # --- Pipeline support ---
    def run_pipeline(self, opportunity_id: str = None) -> List[Dict[str, Any]]:
        """Run the full discover->extract->map->draft->polish->decide pipeline."""
        results = []
        self.log_audit(
            "pg.pipeline.discover_to_decide",
            details={
                "opportunity_id": opportunity_id,
                "chain": list(PIPELINE_CHAIN.keys()) + ["decide"],
            },
        )
        for reflex_name in ["discover", "extract", "map", "draft", "polish", "decide"]:
            if self._shutdown_event.is_set():
                break
            print(f"  Pipeline: running '{reflex_name}'...")
            result = self.run_reflex(reflex_name)
            results.append(result)
            if result.get("status") not in ("success", "skipped"):
                print(f"  Pipeline: '{reflex_name}' failed -- stopping chain")
                break
        return results

    def on_reflex_completed(self, name: str, result: Dict[str, Any]) -> None:
        """Trigger pipeline chain when discover finds new opportunities."""
        if name == "discover" and result.get("status") == "success":
            new_opps = result.get("details", {}).get("new_opportunities", 0)
            if new_opps > 0:
                print(
                    f"INFO: Discover found {new_opps} new opportunities "
                    f"-- triggering extract->map->draft->polish->decide chain"
                )
                for chain_reflex in ["extract", "map", "draft", "polish", "decide"]:
                    if self._shutdown_event.is_set():
                        break
                    chain_result = self.run_reflex(chain_reflex)
                    if chain_result.get("status") not in ("success", "skipped"):
                        break

    # --- Extra status ---
    def get_extra_status(self) -> Dict[str, Any]:
        """Add proposal-specific status fields."""
        # Add phase info to each reflex
        for name in self.reflex_names:
            pass  # Phase is already in config

        conn = get_connection()
        try:
            opp_row = conn.execute("SELECT COUNT(*) as cnt FROM proposal_opportunities").fetchone()
            total_opps = opp_row["cnt"] if opp_row else 0

            quality_row = conn.execute(
                "SELECT COUNT(*) as cnt, AVG(overall_score) as avg_score FROM pg_proposal_quality_scores"
            ).fetchone()
            quality_count = quality_row["cnt"] if quality_row else 0
            avg_quality = round(quality_row["avg_score"], 1) if quality_row and quality_row["avg_score"] else 0
        except Exception:
            total_opps = 0
            quality_count = 0
            avg_quality = 0
        finally:
            conn.close()

        return {
            "summary": {
                "total_opportunities": total_opps,
                "quality_checks": quality_count,
                "avg_quality_score": avg_quality,
            },
        }

    def _print_status_human(self, status: Dict[str, Any]) -> None:
        """Override to show phase column and proposal-specific summary."""
        d = status["daemon"]
        s = status.get("summary", {})
        print(f"Proposal Genesis Daemon v{d['version']}")
        print(f"  Enabled: {d['enabled']}")
        if s:
            print(f"  Opportunities: {s.get('total_opportunities', 0)}")
            print(f"  Quality Checks: {s.get('quality_checks', 0)} (avg: {s.get('avg_quality_score', 0)})")
        print(f"  Audit events (24h): {status['audit']['events_last_24h']}")
        print()
        print(
            f"{'Reflex':<12} {'Phase':<10} {'Tier':<8} {'Enabled':<9} "
            f"{'CB':<5} {'Runs':<6} {'OK':<6} {'Fail':<6} {'Last Run'}"
        )
        print("-" * _STATUS_SEPARATOR_WIDTH)
        for name, r in status["reflexes"].items():
            reflex_config = self.config.get("reflexes", {}).get(name, {})
            phase = reflex_config.get("phase", "unknown")
            cb = "OPEN" if r["circuit_breaker_open"] else "ok"
            last = r["last_run_at"][:_LAST_RUN_DISPLAY_CHARS] if r["last_run_at"] else "never"
            print(
                f"{name:<12} {phase:<10} {r['risk_tier']:<8} "
                f"{str(r['enabled']):<9} {cb:<5} "
                f"{r['total_runs']:<6} {r['total_successes']:<6} "
                f"{r['total_failures']:<6} {last}"
            )

    # --- CLI extensions ---
    def add_cli_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--pipeline", type=str, metavar="OPP_ID", nargs="?", const="all", help="Run full discover->polish pipeline"
        )

    def handle_cli_args(self, args: argparse.Namespace) -> Optional[bool]:
        if hasattr(args, "pipeline") and args.pipeline:
            self._init_states()
            results = self.run_pipeline(args.pipeline if args.pipeline != "all" else None)
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                for r in results:
                    status_str = r.get("status", "unknown")
                    name = r.get("reflex", "?")
                    print(f"  {name}: {status_str}")
            return True
        return None


# ---------------------------------------------------------------------------
# Backward-compat module-level functions
# ---------------------------------------------------------------------------
def _load_config() -> Dict[str, Any]:
    return ProposalGenesisDaemon.load_config()


def _ensure_tables() -> None:
    config = _load_config()
    daemon = ProposalGenesisDaemon(config)
    daemon.ensure_tables()


def _log_audit(
    event_type: str,
    reflex_name: str = None,
    risk_tier: str = None,
    opportunity_id: str = None,
    details: Dict = None,
    success: bool = None,
    duration_ms: int = None,
    metric_name: str = None,
    metric_value: float = None,
) -> str:
    audit_id = generate_id("pga")
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO pg_proposal_genesis_audit
                (id, event_type, reflex_name, risk_tier, opportunity_id,
                 details, success, duration_ms, metric_name, metric_value,
                 created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                audit_id,
                event_type,
                reflex_name,
                risk_tier,
                opportunity_id,
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


# Backward-compat class aliases
ReflexState = PGReflexState
TrustKernel = PGTrustKernel


def _parse_schedule(s):
    return __import__("tools.daemon.base", fromlist=["parse_schedule"]).parse_schedule(s)


def _is_due(sched, last):
    return __import__("tools.daemon.base", fromlist=["is_due"]).is_due(sched, last)


def _evaluate_metric(mc, v):
    return __import__("tools.daemon.base", fromlist=["evaluate_metric"]).evaluate_metric(mc, v)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point."""
    config = ProposalGenesisDaemon.load_config()
    daemon = ProposalGenesisDaemon(config)
    daemon.run_cli()


if __name__ == "__main__":
    main()
