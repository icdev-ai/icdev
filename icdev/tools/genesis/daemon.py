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
# Path bootstrapping — MUST run before ANY `tools.*` / `icdev.*` import.
# Script-style launches (`python tools/genesis/daemon.py`) put only the script
# directory on sys.path[0]; a user-site `.pth` (e.g. fathomdesk-root.pth) can
# otherwise inject a STALE vendored copy of the repo ahead of this checkout and
# bind `sys.modules["tools"]` to it. Inserting the repo root at position 0
# before the first `tools.*` import guarantees this checkout wins. (shx-safe-05)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402 — must follow sys.path bootstrap

logger = get_logger(__name__)

from tools.daemon.base import (  # noqa: E402
    DaemonBase,
    ReflexStateBase,
    TrustKernelBase,
    RISK_GREEN,
    generate_id,
    utcnow,
    utcnow_iso,
    sha256_hex,
)
from tools.db.storage import get_connection, reflex_connection_scope  # noqa: E402
from tools.genesis.constants import TRUST_MODES  # noqa: E402

try:
    from tools.a2a.agent_client import A2AAgentClient  # noqa: E402
except Exception:  # ImportError or requests not installed
    A2AAgentClient = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DAEMON_VERSION = "2.0.0-alpha"
CONFIG_PATH = BASE_DIR / "args" / "genesis_config.yaml"
PID_FILE = BASE_DIR / ".tmp" / "genesis" / "daemon.pid"
STATE_FILE = BASE_DIR / ".tmp" / "genesis" / "state.json"

# Last-resort fallback timeout (seconds) — only used when genesis_config.yaml
# is unavailable AND no per-reflex timeout_seconds is set.  In normal operation
# the value is read from config.defaults.reflex_timeout_seconds so it can be
# tuned without code changes.
DEFAULT_REFLEX_TIMEOUT_SECONDS = 300

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
    "self_monitor",
    "fathomdesk_trap_scenarios",
    "migration_canvas",
    "academy_reflex",
    "academy_oracle_reflex",  # penta-aca-06: 6h in-app 7-lens AcademyOracleRunner → fa_oracle_* (replaces dead forge_academy_oracle)
    "e2e_runner",
    "qa_agent_reflex",  # 6-hour QA coverage gap sweep + E2E sweep scheduling
    "coherence_to_kanban_reflex",  # diffs coherence violations → files kanban bug tasks
    "flaky_tracker_reflex",         # ingests pytest XML → files [FLAKY] kanban tasks
    "dep_health_reflex",            # pip check + pip-audit → files [DEP-HEALTH] kanban tasks
    "dead_code_reflex",             # orphan files + dead functions + import cycles → [DEAD-CODE] tasks
    "kanban_stranded_reflex",       # done/validating tasks vs origin/main → [STRANDED] suggested cards
    "critical_task_watchdog_reflex",  # polls for critical kanban tasks → watchdog_alerts + sidecar JSON
    "api_contract_reflex",            # OpenAPI spec vs live responses → [API-CONTRACT] kanban tasks
    "route_perf_reflex",              # NAV_ROUTES smoke + p50 latency regression detection → [PERF] tasks
    "redaction_scan_reflex",          # scheduled at-rest PII/CUI scan → [PII-SCAN] remediation tasks
    "log_triage",
    "inspect_adapt",
    "cpmp_monitor",
    "pmo_option_tracker",
    "pmo_weekly_report",
    "slides",
    "aidp_monitor",
    "integrity_monitor",
    "harness",             # hcx-rt-01: 6h eval-harness metrics/degradation sweep + co-learning when ICDEV_HARNESS_COLEARN
    "foundry_cycle",
    "ace_team_monitor",
    "ace_skill_promoter",
    "pma_credential_monitor",
    "pma_int_gap_monitor",
    "skill_security_monitor",
    "sdc_control_expiry",  # shx-safe-04: SDC security control-expiry sweep (4h) — IQR anomaly threshold
    "cato_monitor",        # shx-safe-04: cATO continuous compliance monitoring (6h) — compliance/* IQE + POAM
    "bdc_isa_expiry",      # bdr-ops-1: BDC ISA expiry alerting (24h) — was registered-but-undispatched
    "freshness_guardian",  # dcpr-fix-06: DDC freshness quality sweep (1h) → dd_freshness_alerts/dd_quality_runs
    "cato_twin",           # bdr-ops-1: cATO twin continuous monitoring (6h) — config enabled:false until hardened query path soaks
    "ndc_topology_drift",  # ndc→ACOIC: topology config drift vs nc_versions baseline
    "dic_integration",     # dsyn-reflex-02: DIC Canvas Synergy — 15-min cadence
    "dic_review_cadence",  # dsyn-suggest-02: nightly collection review overdue check
    "dic_digest",          # dic-syn-gn: weekly digest of new docs + freshness alerts
    "doc_modernization_sweep",  # docmod-ops-01: nightly EOL/defacto refresh + doc scan + redlines + cards
    "confidence_sampler",  # trust-cal-01: random audit of what the system was already sure about
    "community_refresh",  # dic-graphrag-03: keep DIC GraphRAG community summaries fresh
    "dic_inbox_sweep",     # dic-inbox-02: 5-min sweep of the DIC drop folder (data/dic_inbox)
    "reflexion_loop",      # nova-echo: weekly batch Reflexion pass → improvement artifacts
    "evolution",           # nova-sela: weekly GEPA-style skill text mutation + promotion
    "wiki_lint",           # karpathy-wiki: nightly health checks on memory wiki (orphans/stale/overflow)
    "usage_rollup",        # ecr-bill-01: daily billing rollup from usage_events (00:05 UTC)
    "episodic_distiller",  # phase-a: distill episodic events → semantic facts every 6h
    "daily_briefing_reflex",      # second-brain: hourly check → generate+deliver user briefings
    "nightly_prep_reflex",        # second-brain: evening stalled-work scan + tomorrow prep card
    "thought_leadership_reflex",  # second-brain: Monday 07:00 UTC architecture digest per user
    "meeting_prep_reflex",        # second-brain: hourly — prep cards for upcoming customer meetings
    "objective_tracker_reflex",   # second-brain: daily 23:00 UTC — derive objective progress
    "commitment_watch_reflex",    # second-brain: daily 06:00 UTC — commitment date alerts
    "weekly_retro_reflex",        # second-brain: Friday 18:00 UTC — weekly retrospective
    "pdc_pipeline_stale",  # pdx-ops-01: PDC pipeline staleness alert (6h) — IQR anomaly threshold; was implemented-but-undispatched
    "twin_freshness_sweep",  # twx-cov-02: cross-canvas twin freshness sweep (6h) — observer-driven, nudges stale twins (AADC/Mission/residual)
    "observability_retention",  # obx-trc-05: 24h archive-then-prune of otel_spans/prov_*/shap_attributions (append-only → cold twin)
    "bgp_hijack_monitor",  # cnr-dsoc-05: DSOC RTBH auto-expiry (always) + best-effort BGP hijack/route-leak sweep (pmacct feed)
    "odc_coverage_refresh",  # obx-cov-02: 6h scheduled ODC MITRE coverage recompute + >15% drift detection → od_audit + suggested cards
    "retention_sweep",  # crx-db-03: 24h config-driven retention/archival (args/retention_policies.yaml); append-only tables archive-only, dry_run default
    "agent_cron_reflex",  # sag-cron-01: drains due user-facing cron jobs (agent_cron_jobs) — agent/script exec modes, retry/backoff, delivery
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
        _defaults = config.get("defaults", {})
        self._default_reflex_timeout: float = float(
            _defaults.get("reflex_timeout_seconds", DEFAULT_REFLEX_TIMEOUT_SECONDS)
        )
        self._stub_loc_min: int = int(_defaults.get("stub_loc_min", 10))
        self._stub_loc_full: int = int(_defaults.get("stub_loc_full", 15))

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
                CREATE TABLE IF NOT EXISTS agent_a2a_tasks (
                    id              TEXT PRIMARY KEY,
                    reflex_name     TEXT NOT NULL,
                    skill_id        TEXT NOT NULL,
                    agent_url       TEXT NOT NULL,
                    task_id         TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'submitted',
                    input_data      TEXT,
                    result          TEXT,
                    error           TEXT,
                    submitted_at    TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

    def _classify_reflex_impl(self, name: str) -> Dict[str, Any]:
        """Return implementation metadata for a reflex without executing it.

        Checks:
        - module exists (importable)
        - has 'run' callable
        - IMPLEMENTATION_STATUS constant (full | partial | stub)
        - run body is not just a stub return
        """
        status = {
            "exists": False,
            "has_run": False,
            "implementation_status": "missing",
            "is_stub": True,
            "loc": 0,
        }
        try:
            module = importlib.import_module(f"tools.genesis.reflexes.{name}")
            status["exists"] = True

            if hasattr(module, "run") and callable(getattr(module, "run")):
                status["has_run"] = True
                impl = getattr(module, "IMPLEMENTATION_STATUS", "unknown")
                status["implementation_status"] = impl

                # Detect stub by inspecting source
                import inspect

                try:
                    source = inspect.getsource(module.run)
                    status["loc"] = len(source.splitlines())
                    # Heuristic: if run() contains "stub" in return or is very short, mark stub
                    if impl == "stub" or ("stub" in source.lower() and status["loc"] < self._stub_loc_min):
                        status["is_stub"] = True
                    elif impl in ("full", "partial") or status["loc"] > self._stub_loc_full:
                        status["is_stub"] = False
                except Exception:
                    pass
            else:
                status["implementation_status"] = "no_run_function"
        except ImportError:
            status["implementation_status"] = "missing"
        except Exception as e:
            status["implementation_status"] = f"error: {e}"
        return status

    def _observe(self, name: str, fn, config: Dict[str, Any], trust: TrustKernelBase) -> Dict[str, Any]:
        """Wrap a reflex invocation with tracing and metrics.

        Logs start/end, captures timing, and records exceptions.
        Returns the reflex result unchanged.
        """
        import time
        start = time.time()
        logger.info(f"[GENESIS] Reflex '{name}' starting")
        try:
            result = fn(config, trust)
            elapsed_ms = round((time.time() - start) * 1000, 2)
            success = result.get("success", False) if isinstance(result, dict) else False
            logger.info(f"[GENESIS] Reflex '{name}' finished in {elapsed_ms}ms (success={success})")
            if isinstance(result, dict):
                result["_observed"] = {"duration_ms": elapsed_ms, "timestamp": utcnow_iso()}
            return result
        except Exception as exc:
            elapsed_ms = round((time.time() - start) * 1000, 2)
            logger.exception(f"[GENESIS] Reflex '{name}' failed after {elapsed_ms}ms")
            return {"success": False, "error": str(exc), "_observed": {"duration_ms": elapsed_ms, "timestamp": utcnow_iso()}}

    def run_reflex_impl(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Execute a reflex under a watchdog so a hung reflex can't wedge the daemon.

        The daemon runs reflexes sequentially (base.run_due_reflexes), so a single
        reflex that blocks forever — e.g. a network fetch with no socket timeout —
        freezes EVERY reflex behind it. This has caused multi-day stalls where the
        whole loop hung on an unresponsive HTTPS endpoint.

        We run the real implementation in a daemon thread and join with a timeout.
        On timeout we abandon the (leaked) thread and return a failure tuple; the
        base records it as a failure, so a persistently-hanging reflex trips its
        circuit breaker after `max_consecutive_failures` and stops being attempted.

        Timeout is per-reflex via `reflexes.<name>.timeout_seconds`, falling back
        to `defaults.reflex_timeout_seconds` in genesis_config.yaml.
        """
        import threading

        # crx-gen-03: per-reflex hard execution cap. `max_execution_seconds` is the
        # documented config name; `timeout_seconds` is kept as a backward-compat
        # alias. Falls back to defaults.reflex_timeout_seconds. Enforcement is the
        # existing watchdog join-with-timeout below; a breach returns a failure
        # tuple that base.run_reflex records as a genesis_audit failure row.
        timeout = float(
            config.get(
                "max_execution_seconds",
                config.get("timeout_seconds", self._default_reflex_timeout),
            )
        )
        box: Dict[str, Any] = {}

        def _target() -> None:
            try:
                box["result"] = self._run_reflex_impl_inner(name, config, trust)
            except Exception as exc:  # noqa: BLE001 — surface to caller below
                box["error"] = exc

        worker = threading.Thread(target=_target, name=f"reflex-{name}", daemon=True)
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            logger.error(
                "Reflex '%s' exceeded %.0fs watchdog timeout — abandoning (thread leaked) "
                "so the daemon loop can continue. Repeated timeouts will trip its circuit breaker.",
                name, timeout,
            )
            return False, 0.0, {"error": f"watchdog_timeout_{int(timeout)}s", "timeout": True}

        if "error" in box:
            return False, 0.0, {"error": str(box["error"]), "stage": "reflex_execution"}
        return box.get("result", (False, 0.0, {"error": "no result from reflex thread"}))

    def _record_a2a_task(
        self,
        reflex_name: str,
        agent_url: str,
        task_id: str,
        input_data: Dict,
        status: str = "submitted",
        error: str = None,
    ) -> None:
        """Persist an A2A task submission row to agent_a2a_tasks."""
        record_id = generate_id("a2a")
        now = utcnow_iso()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO agent_a2a_tasks
                    (id, reflex_name, skill_id, agent_url, task_id, status,
                     input_data, error, submitted_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record_id,
                    reflex_name,
                    reflex_name,
                    agent_url,
                    task_id,
                    status,
                    json.dumps(input_data),
                    error,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _submit_reflex_a2a(self, name: str, config: Dict[str, Any]) -> Tuple[bool, float, Dict]:
        """Dispatch an a2a_eligible reflex to the configured A2A agent endpoint."""
        if A2AAgentClient is None:
            return False, 0.0, {"error": "A2AAgentClient unavailable (requests not installed)", "stage": "a2a_dispatch"}

        a2a_cfg = self.config.get("a2a", {})
        agent_url = a2a_cfg.get("gateway_url") or os.environ.get(
            "ICDEV_A2A_GATEWAY_URL", "https://localhost:8443"
        )

        session_ctx: Dict[str, Any] = {
            "reflex_name": name,
            "reflex_config": config,
            "daemon_version": self.daemon_version,
        }

        try:
            client = A2AAgentClient(verify_ssl=False)
            result = client.submit_task(skill_id=name, input_data=session_ctx, agent_url=agent_url)
            task_id = result.get("id") or result.get("task_id", "")
            self._record_a2a_task(name, agent_url, task_id, session_ctx, status="submitted")
            logger.info("[GENESIS] Reflex '%s' dispatched via A2A: task_id=%s", name, task_id)
            return True, 0.0, {"status": "a2a_submitted", "task_id": task_id, "agent_url": agent_url}
        except Exception as exc:
            logger.error("[GENESIS] A2A dispatch failed for reflex '%s': %s", name, exc)
            self._record_a2a_task(name, agent_url, "", session_ctx, status="error", error=str(exc))
            return False, 0.0, {"error": str(exc), "stage": "a2a_dispatch"}

    def _run_reflex_impl_inner(self, name: str, config: Dict[str, Any], trust: TrustKernelBase) -> Tuple[bool, float, Dict]:
        """Actual reflex dispatch (wrapped by the watchdog in run_reflex_impl)."""
        risk_tier = config.get("risk_tier", RISK_GREEN)

        # ORANGE tier — log that human review is needed
        if trust.requires_human_approval(risk_tier):
            return True, 0.0, {"status": "awaiting_human_approval", "risk_tier": risk_tier}

        # A2A fan-out: dispatch to agent network when reflex is eligible
        a2a_cfg = self.config.get("a2a", {})
        if a2a_cfg.get("enabled", False) and config.get("a2a_eligible", False):
            return self._submit_reflex_a2a(name, config)

        try:
            module = importlib.import_module(f"tools.genesis.reflexes.{name}")
            if hasattr(module, "run"):
                # [DISPATCH POINT] Centralized reflex invocation via importlib.
                # All 22 reflexes in REFLEX_NAMES are dispatched here.
                # crx-gen-01: run inside a per-reflex connection scope so a reflex
                # that raises mid-transaction (or otherwise leaks a get_connection()
                # handle) has that pooled connection rolled back and returned to the
                # pool on scope exit — preventing pool exhaustion / idle-in-txn lock
                # storms from one bad reflex cascading onto every reflex behind it.
                with reflex_connection_scope():
                    result = self._observe(name, module.run, config, trust)
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

        # Stub mode — log warning for operational visibility
        logger.warning(
            f"Reflex '{name}' executed in stub mode — no real implementation found. "
            "Install or wire the reflex module to remove this warning."
        )
        return (
            True,
            0.0,
            {
                "status": "stub",
                "message": f"Reflex '{name}' not yet implemented -- stub mode (success)",
            },
        )

    def get_reflex_coverage(self) -> Dict[str, Any]:
        """Return coverage statistics for all configured reflexes."""
        total = len(self.reflex_names)
        real = 0
        partial = 0
        stubs = 0
        missing = 0
        details = []

        for name in self.reflex_names:
            meta = self._classify_reflex_impl(name)
            impl = meta["implementation_status"]
            is_stub = meta["is_stub"]

            if impl == "full":
                real += 1
            elif impl == "partial":
                partial += 1
            elif is_stub or impl in ("missing", "no_run_function"):
                stubs += 1
            else:
                missing += 1

            details.append(
                {
                    "reflex": name,
                    "exists": meta["exists"],
                    "has_run": meta["has_run"],
                    "implementation_status": impl,
                    "is_stub": is_stub,
                    "loc": meta["loc"],
                }
            )

        coverage_pct = round((real / total) * 100, 1) if total else 0.0
        return {
            "total": total,
            "real": real,
            "partial": partial,
            "stubs": stubs,
            "missing": missing,
            "coverage_percent": coverage_pct,
            "details": details,
        }

    def on_reflex_completed(self, name: str, result: Dict[str, Any]) -> None:
        """Post-reflex hook: critical-reflex alerting, convergence, stagnation."""
        # crx-gen-02: turn recent critical-reflex failures into operator alerts on
        # the /monitoring page (shared `alerts` table), with per-reflex cooldown.
        # Guarded so a health-alerting hiccup can never break the reflex loop.
        try:
            if self.config.get("reflex_health", {}).get("enabled", True):
                from tools.genesis.reflex_health import open_critical_reflex_alerts
                open_critical_reflex_alerts(self.config)
        except Exception as exc:  # noqa: BLE001
            logger.debug("reflex_health alerting hook skipped: %s", exc)

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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
