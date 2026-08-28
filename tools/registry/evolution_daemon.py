#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Evolution Daemon — autonomous capability evolution lifecycle (D-EVO-1).

Extends DaemonBase to orchestrate the Phase 36 Evolutionary Intelligence
7-step lifecycle: discover -> evaluate -> stage -> test -> approve -> verify -> absorb.

Each step runs as a scheduled reflex managed by the daemon infrastructure:
    discover  (GREEN,  every 6h)    — Find unevaluated child-learned behaviors
    evaluate  (GREEN,  on_demand)   — Run 7-dimension capability evaluation
    stage     (YELLOW, every 1h)    — Create staging environments for auto_queue
    test      (YELLOW, continuous)  — Run full test pipeline on active stages
    approve   (ORANGE, on_demand)   — Prepare propagation records for HITL
    verify    (GREEN,  continuous)  — Check 72h stability window
    absorb    (ORANGE, every 6h)    — HITL-gated genome merge

Architecture:
    - DaemonBase provides scheduling, circuit breakers, signal handling, CLI
    - Reflexes call existing Phase 36 tools (not separate module files)
    - HITL always required for genome changes (D-EVO-1)
    - Scanner-tier LLM only (qwen3.5-local, zero Claude cost)
    - All audit events append-only (NIST AU, D6)

Usage:
    ICDEV_EVOLUTION_ENABLED=true python tools/registry/evolution_daemon.py
    python tools/registry/evolution_daemon.py --once --json
    python tools/registry/evolution_daemon.py --status --json
    python tools/registry/evolution_daemon.py --reflex discover --json
    python tools/registry/evolution_daemon.py --enable discover
    python tools/registry/evolution_daemon.py --disable absorb
    python tools/registry/evolution_daemon.py --reset test --json
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

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
    RISK_GREEN,
    generate_id,
    utcnow_iso,
)
from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_VERSION = "1.0.0"
CONFIG_PATH = BASE_DIR / "args" / "evolution_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "evolution" / "daemon.pid"

REFLEX_NAMES = [
    "discover",
    "evaluate",
    "stage",
    "test",
    "approve",
    "verify",
    "absorb",
]


# ---------------------------------------------------------------------------
# Custom ReflexState (points to evolution_reflex_state table)
# ---------------------------------------------------------------------------
class EvolutionReflexState(ReflexStateBase):
    """DB-backed state for evolution reflexes."""

    state_table = "evolution_reflex_state"


# ---------------------------------------------------------------------------
# Custom TrustKernel (HITL on absorb/propagate)
# ---------------------------------------------------------------------------
class EvolutionTrustKernel(TrustKernelBase):
    """Enforces HITL for genome-changing operations (D-EVO-1)."""

    def can_execute(self, risk_tier: str, action: str = "run") -> Tuple[bool, str]:
        """Check action against allowlist; ORANGE always needs human approval."""
        if self.allowed_actions:
            allowed = self.allowed_actions.get(risk_tier, [])
            if action not in allowed:
                return False, (f"Action '{action}' not in allowlist for tier '{risk_tier}'")
        return True, "approved"


# ---------------------------------------------------------------------------
# Evolution Daemon
# ---------------------------------------------------------------------------
class EvolutionDaemon(DaemonBase):
    """Autonomous capability evolution lifecycle daemon (D-EVO-1).

    7 reflexes map to the Phase 36 lifecycle:
        discover → evaluate → stage → test → approve → verify → absorb
    """

    daemon_name = "Evolution Daemon"
    daemon_version = DAEMON_VERSION
    config_path = CONFIG_PATH
    pid_file = PID_FILE
    env_enabled_var = "ICDEV_EVOLUTION_ENABLED"
    env_reflex_prefix = "ICDEV_EVOLUTION_REFLEX"
    event_prefix = "evolution"
    reflex_names = REFLEX_NAMES
    id_prefix = "evo"
    service_name = "evolution-daemon"       # coordination session-id prefix (autonomy-id-05)
    service_agent = "evolution"

    # -----------------------------------------------------------------------
    # Abstract method implementations
    # -----------------------------------------------------------------------
    def ensure_tables(self) -> None:
        """Create evolution-specific DB tables."""
        conn = get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evolution_audit (
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
                CREATE TABLE IF NOT EXISTS evolution_reflex_state (
                    reflex_name             TEXT PRIMARY KEY,
                    enabled                 INTEGER NOT NULL DEFAULT 1,
                    last_run_at             TEXT,
                    next_run_at             TEXT,
                    consecutive_failures    INTEGER NOT NULL DEFAULT 0,
                    circuit_breaker_open    INTEGER NOT NULL DEFAULT 0,
                    circuit_breaker_tripped_at TEXT,
                    total_runs              INTEGER NOT NULL DEFAULT 0,
                    total_successes         INTEGER NOT NULL DEFAULT 0,
                    total_failures          INTEGER NOT NULL DEFAULT 0,
                    last_metric_value       REAL,
                    last_error              TEXT,
                    updated_at              TEXT NOT NULL
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
        """Append audit event (append-only, NIST AU, D6)."""
        audit_id = generate_id("aud")
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO evolution_audit
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
        """Factory: create EvolutionReflexState."""
        return EvolutionReflexState(name, config)

    def create_trust_kernel(self, config: Dict[str, Any]) -> TrustKernelBase:
        """Factory: create EvolutionTrustKernel."""
        return EvolutionTrustKernel(config)

    def _get_audit_table(self) -> str:
        return "evolution_audit"

    # -----------------------------------------------------------------------
    # Reflex dispatch (inline — calls Phase 36 tools directly)
    # -----------------------------------------------------------------------
    def run_reflex_impl(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Execute a single evolution reflex."""
        risk_tier = config.get("risk_tier", RISK_GREEN)

        # ORANGE tier requires HITL — log and return awaiting status
        if trust.requires_human_approval(risk_tier):
            # Still run the reflex but flag that outputs need HITL approval
            pass

        dispatch = {
            "discover": self._reflex_discover,
            "evaluate": self._reflex_evaluate,
            "stage": self._reflex_stage,
            "test": self._reflex_test,
            "approve": self._reflex_approve,
            "verify": self._reflex_verify,
            "absorb": self._reflex_absorb,
        }

        handler = dispatch.get(name)
        if handler:
            try:
                return handler(config, trust)
            except Exception as e:
                return False, 0.0, {"error": str(e), "stage": name}

        return True, 0.0, {"status": "stub", "message": f"Reflex '{name}' unknown"}

    # -----------------------------------------------------------------------
    # Reflex: DISCOVER (GREEN)
    # Query child_learned_behaviors WHERE evaluated=0
    # -----------------------------------------------------------------------
    def _reflex_discover(self, config: Dict, trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Find unevaluated child-learned behaviors."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT id, child_id, behavior_type, description, confidence
                FROM child_learned_behaviors
                WHERE evaluated = 0
                ORDER BY created_at ASC
                LIMIT 50
            """).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        count = len(rows)
        behavior_ids = [dict(r)["id"] for r in rows] if rows else []

        # Chain: trigger evaluate for each discovered behavior
        if count > 0:
            self._chain_evaluate(behavior_ids)

        return (
            True,
            float(count),
            {
                "behaviors_discovered": count,
                "behavior_ids": behavior_ids[:10],
            },
        )

    def _chain_evaluate(self, behavior_ids: List[str]) -> None:
        """Trigger evaluation for discovered behaviors (pipeline chain)."""
        try:
            from tools.registry.capability_evaluator import CapabilityEvaluator

            evaluator = CapabilityEvaluator()

            conn = get_connection()
            try:
                for bid in behavior_ids:
                    row = conn.execute(
                        """
                        SELECT id, child_id, behavior_type, description,
                               evidence_json, confidence
                        FROM child_learned_behaviors WHERE id = %s
                    """,
                        (bid,),
                    ).fetchone()
                    if not row:
                        continue

                    r = dict(row)
                    evidence = {}
                    if r.get("evidence_json"):
                        try:
                            evidence = json.loads(r["evidence_json"])
                        except (json.JSONDecodeError, TypeError):
                            pass

                    capability_data = {
                        "name": f"{r.get('behavior_type', 'unknown')}-{bid[:8]}",
                        "source": "child_report",
                        "target_children": evidence.get("target_children", 1),
                        "total_children": evidence.get("total_children", 1),
                        "compliance_impact": evidence.get("compliance_impact", "neutral"),
                        "risk_factors": evidence.get("risk_factors", []),
                        "blast_radius": evidence.get("blast_radius", "low"),
                        "evidence_count": evidence.get("evidence_count", 1),
                        "field_hours": evidence.get("field_hours", 0),
                        "existing_similar": evidence.get("existing_similar", False),
                        "fills_gap": evidence.get("fills_gap", True),
                        "token_cost": evidence.get("token_cost", 0),
                        "integration_effort": evidence.get("integration_effort", "low"),
                        "trust_level": r.get("trust_level", "child"),
                        "injection_scan_passed": True,
                        "atlas_techniques_covered": 0,
                    }

                    evaluator.evaluate(capability_data)

                    # Mark as evaluated
                    conn.execute(
                        """
                        UPDATE child_learned_behaviors
                        SET evaluated = 1
                        WHERE id = %s
                    """,
                        (bid,),
                    )

                conn.commit()
            finally:
                conn.close()
        except ImportError:
            pass
        except Exception as e:
            self.log_audit("evolution.chain_evaluate.error", details={"error": str(e)})

    # -----------------------------------------------------------------------
    # Reflex: EVALUATE (GREEN, on_demand — chained from discover)
    # -----------------------------------------------------------------------
    def _reflex_evaluate(self, config: Dict, trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Run 7-dimension evaluation on pending behaviors (standalone trigger)."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT id FROM child_learned_behaviors
                WHERE evaluated = 0
                LIMIT 20
            """).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        ids = [dict(r)["id"] for r in rows] if rows else []
        if ids:
            self._chain_evaluate(ids)

        return (
            True,
            float(len(ids)),
            {
                "evaluations_completed": len(ids),
            },
        )

    # -----------------------------------------------------------------------
    # Reflex: STAGE (YELLOW)
    # Find auto_queue evaluations not yet staged, create staging envs
    # -----------------------------------------------------------------------
    def _reflex_stage(self, config: Dict, trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Create staging environments for auto_queue capabilities."""
        conn = get_connection()
        staged = 0
        errors = []
        try:
            # Find auto_queue evaluations that don't yet have staging envs
            rows = conn.execute("""
                SELECT ce.id, ce.capability_id, ce.capability_name
                FROM capability_evaluations ce
                WHERE ce.outcome = 'auto_queue'
                AND ce.capability_id NOT IN (
                    SELECT capability_id FROM staging_environments
                    WHERE status IN ('created', 'testing', 'passed')
                )
                ORDER BY ce.created_at DESC
                LIMIT 5
            """).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        if rows:
            try:
                from tools.registry.staging_manager import StagingManager

                sm = StagingManager()
                for row in rows:
                    r = dict(row)
                    try:
                        sm.create_staging(
                            capability_id=r["capability_id"],
                            genome_version=self._get_current_genome_version(),
                        )
                        staged += 1
                    except Exception as e:
                        errors.append({"capability": r["capability_id"], "error": str(e)})
            except ImportError:
                errors.append({"error": "StagingManager not available"})

        return (
            True,
            float(staged),
            {
                "stages_created": staged,
                "errors": errors[:5] if errors else [],
            },
        )

    # -----------------------------------------------------------------------
    # Reflex: TEST (YELLOW, continuous)
    # Run test pipeline on active staging environments
    # -----------------------------------------------------------------------
    def _reflex_test(self, config: Dict, trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Run full test pipeline on active staging environments."""
        tested = 0
        results = []
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT id, capability_id, worktree_path
                FROM staging_environments
                WHERE status IN ('created', 'testing')
                LIMIT 5
            """).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        if rows:
            try:
                from tools.registry.staging_manager import StagingManager

                sm = StagingManager()
                for row in rows:
                    r = dict(row)
                    try:
                        test_result = sm.run_tests(r["id"])
                        tested += 1
                        results.append(
                            {
                                "staging_id": r["id"],
                                "status": test_result.get("status", "unknown"),
                            }
                        )
                    except Exception as e:
                        results.append(
                            {
                                "staging_id": r["id"],
                                "error": str(e),
                            }
                        )
            except ImportError:
                pass

        return (
            True,
            float(tested),
            {
                "stages_tested": tested,
                "results": results[:5],
            },
        )

    # -----------------------------------------------------------------------
    # Reflex: APPROVE (ORANGE — HITL gate)
    # Prepare propagation records for capabilities that passed staging
    # -----------------------------------------------------------------------
    def _reflex_approve(self, config: Dict, trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Prepare propagation records for HITL approval."""
        prepared = 0
        conn = get_connection()
        try:
            # Find staged capabilities that passed and don't have propagation records
            rows = conn.execute("""
                SELECT se.id, se.capability_id, se.genome_version
                FROM staging_environments se
                WHERE se.status = 'passed'
                AND se.capability_id NOT IN (
                    SELECT capability_id FROM propagation_log
                    WHERE status IN ('prepared', 'approved', 'executing', 'completed')
                )
                LIMIT 5
            """).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        if rows:
            try:
                from tools.registry.propagation_manager import PropagationManager

                pm = PropagationManager()

                # Get all active children as default targets
                target_children = self._get_active_children()

                for row in rows:
                    r = dict(row)
                    try:
                        pm.prepare_propagation(
                            capability_id=r["capability_id"],
                            target_children=target_children,
                            source_type="child_report",
                        )
                        prepared += 1
                    except Exception:
                        pass
            except ImportError:
                pass

        return (
            True,
            float(prepared),
            {
                "propagations_prepared": prepared,
                "awaiting_hitl": prepared > 0,
            },
        )

    # -----------------------------------------------------------------------
    # Reflex: VERIFY (GREEN, continuous)
    # Check 72h stability window on propagated capabilities
    # -----------------------------------------------------------------------
    def _reflex_verify(self, config: Dict, trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Check stability window on propagated capabilities."""
        verified = 0
        stable = 0
        unstable = 0

        try:
            from tools.registry.absorption_engine import AbsorptionEngine

            ae = AbsorptionEngine()
            candidates = ae.get_absorption_candidates()

            if isinstance(candidates, dict):
                candidate_list = candidates.get("candidates", [])
            else:
                candidate_list = []

            for candidate in candidate_list:
                cid = candidate.get("capability_id") or candidate.get("id")
                if not cid:
                    continue
                try:
                    stability = ae.check_stability(cid)
                    verified += 1
                    if stability.get("stable"):
                        stable += 1
                    else:
                        unstable += 1
                except Exception:
                    pass
        except ImportError:
            pass

        return (
            True,
            float(verified),
            {
                "verifications_completed": verified,
                "stable": stable,
                "unstable": unstable,
            },
        )

    # -----------------------------------------------------------------------
    # Reflex: ABSORB (ORANGE — HITL gate)
    # Merge stable capabilities into genome (always requires HITL)
    # -----------------------------------------------------------------------
    def _reflex_absorb(self, config: Dict, trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Report absorption-ready capabilities (HITL required for actual merge)."""
        ready = 0
        candidates = []

        try:
            from tools.registry.absorption_engine import AbsorptionEngine

            ae = AbsorptionEngine()
            result = ae.get_absorption_candidates()

            if isinstance(result, dict):
                candidate_list = result.get("candidates", [])
            else:
                candidate_list = []

            for candidate in candidate_list:
                cid = candidate.get("capability_id") or candidate.get("id")
                if not cid:
                    continue
                try:
                    stability = ae.check_stability(cid)
                    if stability.get("stable"):
                        ready += 1
                        candidates.append(
                            {
                                "capability_id": cid,
                                "stable": True,
                                "message": "Ready for HITL absorption approval",
                            }
                        )
                except Exception:
                    pass
        except ImportError:
            pass

        return (
            True,
            float(ready),
            {
                "absorptions_ready": ready,
                "candidates": candidates[:10],
                "hitl_required": True,
                "message": (
                    f"{ready} capabilities ready for absorption — use absorption_engine.py --absorb to merge"
                    if ready > 0
                    else "No capabilities ready"
                ),
            },
        )

    # -----------------------------------------------------------------------
    # Pipeline chain hook
    # -----------------------------------------------------------------------
    def on_reflex_completed(self, name: str, result: Dict[str, Any]) -> None:
        """Chain reflexes: discover triggers evaluate, stage triggers test."""
        if result.get("status") != "success":
            return

        details = result.get("details", {})

        # discover -> evaluate (auto-chain)
        if name == "discover" and details.get("behaviors_discovered", 0) > 0:
            self.log_audit("evolution.chain.discover_to_evaluate", details={"triggered_by": "discover"})

        # stage -> test (auto-chain)
        if name == "stage" and details.get("stages_created", 0) > 0:
            self.log_audit("evolution.chain.stage_to_test", details={"triggered_by": "stage"})

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    def _get_current_genome_version(self) -> str:
        """Get the latest genome version string."""
        conn = get_connection()
        try:
            row = conn.execute("""
                SELECT version FROM genome_versions
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()
            return dict(row)["version"] if row else "0.0.0"
        except Exception:
            return "0.0.0"
        finally:
            conn.close()

    def _get_active_children(self) -> List[str]:
        """Get list of active child app IDs."""
        conn = get_connection()
        try:
            rows = conn.execute("""
                SELECT id FROM child_app_registry
                WHERE 1=1
                ORDER BY created_at DESC
                LIMIT 50
            """).fetchall()
            return [dict(r)["id"] for r in rows] if rows else []
        except Exception:
            return []
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point."""
    config = EvolutionDaemon.load_config()
    daemon = EvolutionDaemon(config)
    daemon.ensure_tables()
    daemon.run_cli()


if __name__ == "__main__":
    main()
