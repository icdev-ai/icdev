# CUI // SP-CTI
"""ACE CoWorkerThread — execution unit for a single ACE co-worker.

Each CoWorkerThread runs one co-worker's full role step sequence in a
dedicated daemon thread, handling inter-coworker messaging and HITL gates.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from icdev.tools.ace.message_bus import MessageBus
from icdev.tools.ace.role_loader import RoleLoader, RoleNotFoundError
from icdev.tools.ace.step_executor import (
    StepExecutor,
    ToolPermissionDeniedError,
    TrustKernelDeniedError,
)
from icdev.tools.ace.team_assembler import CoWorkerSpec
from icdev.tools.chat.chat_manager import ChatManager
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.coworker_thread")

_DB_ENV = "ICDEV_ACE_DB_URL"
# Event-driven HITL wake: the waiting thread blocks on a threading.Event that
# HITLGate.resolve() sets the instant a human approves.  A 30 s fallback poll is
# retained so a resolution written by a *different* process (e.g. the dashboard
# Flask worker) is still noticed even though it can't set this in-process Event.
_HITL_EVENT_WAIT = 30.0  # seconds — cross-process fallback re-check interval
_DEFAULT_MONITOR_INTERVAL = 10  # steps between behavioral compliance checks

# ---------------------------------------------------------------------------
# HITL wake registry — one threading.Event per coworker_id.
#
# The confidence gate and the required-step gate both block a co-worker thread
# until a human inserts a matching ``hitl_resolved`` row.  Instead of busy-
# polling every 2 s, the waiter blocks on this Event; HITLGate.resolve() (which
# may run in a different thread within the same process, e.g. the Flask worker)
# sets it so the waiter wakes immediately.  Access is guarded by a lock because
# resolve() and the waiter run on different threads.
# ---------------------------------------------------------------------------
_hitl_events_lock = threading.Lock()
_hitl_events: dict[str, threading.Event] = {}


def _get_hitl_event(coworker_id: str) -> threading.Event:
    """Return (creating if absent) the wake Event for ``coworker_id``."""
    with _hitl_events_lock:
        ev = _hitl_events.get(coworker_id)
        if ev is None:
            ev = threading.Event()
            _hitl_events[coworker_id] = ev
        return ev


def _signal_hitl_event(coworker_id: str) -> None:
    """Wake any thread waiting on ``coworker_id``'s HITL gate (best-effort)."""
    with _hitl_events_lock:
        ev = _hitl_events.get(coworker_id)
    if ev is not None:
        ev.set()


def _discard_hitl_event(coworker_id: str) -> None:
    """Drop the wake Event once the wait is over (best-effort cleanup)."""
    with _hitl_events_lock:
        _hitl_events.pop(coworker_id, None)

# Topics that may legitimately appear alongside task.assigned (gateway prerequisites only).
# Reactive callback topics (e.g. doc.review_feedback) must NOT be listed here; they
# create circular deadlocks when a co-worker blocks on them before running any step.
_BOOTSTRAP_TOPICS: frozenset[str] = frozenset({"task.assigned"})


def _filter_listen_topics(topics: list[str], coworker_id: str) -> list[str]:
    """Return filtered listen_topics, dropping reactive entries when task.assigned is present.

    If task.assigned is in the list, every topic NOT in _BOOTSTRAP_TOPICS is logged as a
    WARNING and removed from the effective list.  This prevents future role YAMLs that
    accidentally list reactive topics (e.g. doc.review_feedback) from deadlocking the
    thread by blocking on a message that will never arrive before steps run.
    """
    if "task.assigned" not in topics:
        return list(topics)
    filtered: list[str] = []
    for topic in topics:
        if topic in _BOOTSTRAP_TOPICS:
            filtered.append(topic)
        else:
            logger.warning(
                "ace_listen_topics_guard: coworker=%s skipping non-bootstrap topic %r "
                "(task.assigned is present; reactive topics belong in role steps, not listen_topics)",
                coworker_id,
                topic,
            )
    return filtered


def _load_monitor_interval() -> int:
    """Read monitor_interval from args/ace/ace_config.yaml; default 10."""
    try:
        import yaml

        cfg_path = Path(__file__).parents[4] / "args" / "ace" / "ace_config.yaml"
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return int(cfg.get("monitor_interval", _DEFAULT_MONITOR_INTERVAL))
    except Exception:
        return _DEFAULT_MONITOR_INTERVAL


def _load_trust_overrides() -> dict[str, Any]:
    """Read confidence-gate overrides from args/ace/trust.yaml.

    Returns a dict with two keys (always present, defaulting to empty):
      * ``initial_trust``     — {role_id: float} seed score consulted by the
        confidence gate before falling back to the learned trust score.
      * ``auto_approve_roles`` — [role_id, ...] whose fresh co-workers skip the
        human confidence gate entirely.

    Defaults keep today's behavior: no overrides, no auto-approvals, so every
    role starts at 0.5 (< TRUST_SUPERVISED) and the gate fires.
    """
    out: dict[str, Any] = {"initial_trust": {}, "auto_approve_roles": []}
    try:
        import yaml

        # Resolve args/ace/ by walking up from this file — works whether the
        # live module is tools/ace/… or the mirrored icdev/tools/ace/… copy.
        here = Path(__file__).resolve()
        cfg_path = None
        for parent in here.parents:
            cand = parent / "args" / "ace" / "trust.yaml"
            if cand.exists():
                cfg_path = cand
                break
        if cfg_path is None:
            return out
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        it = cfg.get("initial_trust")
        if isinstance(it, dict):
            out["initial_trust"] = {str(k): float(v) for k, v in it.items()}
        ar = cfg.get("auto_approve_roles")
        if isinstance(ar, list):
            out["auto_approve_roles"] = [str(r) for r in ar]
    except Exception:
        return out
    return out


# ---------------------------------------------------------------------------
# HITLGate
# ---------------------------------------------------------------------------


class HITLGate:
    """Tracks and resolves human-in-the-loop approval requests.

    Pending requests are ace_audit_log rows with action='hitl_pending'.
    A request is resolved when a matching 'hitl_resolved' row (same coworker +
    same detail) is inserted via HITLGate.resolve().
    """

    @staticmethod
    def get_pending(coworker_id: str) -> list[dict[str, Any]]:
        """Return unresolved HITL requests for the given coworker.

        Returns:
            List of dicts with keys: id, detail, created_at.
            Empty list when all requests are resolved.
        """
        try:
            from icdev.tools.db.storage import get_canvas_connection

            conn = get_canvas_connection(_DB_ENV)
            try:
                pending_rows = conn.execute(
                    """SELECT id, detail, created_at
                       FROM ace_audit_log
                       WHERE coworker_id = %s AND action = 'hitl_pending'
                       ORDER BY created_at DESC""",
                    (coworker_id,),
                ).fetchall()

                if not pending_rows:
                    return []

                resolved_rows = conn.execute(
                    """SELECT detail FROM ace_audit_log
                       WHERE coworker_id = %s AND action = 'hitl_resolved'""",
                    (coworker_id,),
                ).fetchall()
                resolved_details = {r[0] for r in resolved_rows}

                return [
                    {"id": r[0], "detail": r[1], "created_at": r[2]}
                    for r in pending_rows
                    if r[1] not in resolved_details
                ]
            finally:
                conn.close()
        except Exception:
            return []

    @staticmethod
    def resolve(coworker_id: str, detail: str, instance_id: str = "") -> None:
        """Mark a HITL request as resolved.

        Args:
            coworker_id: The coworker whose HITL request to resolve.
            detail:      Must match the detail string used when creating the request.
            instance_id: ACE instance ID for audit row.
        """
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "INSERT INTO ace_audit_log "
                    "(instance_id, coworker_id, action, detail, actor, created_at) "
                    "VALUES (%s, %s, 'hitl_resolved', %s, 'hitl_gate', %s)",
                    (instance_id, coworker_id, detail, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("resolve: best-effort INSERT into ace_audit_log failed (non-blocking): %s", exc)
        # Wake the waiting co-worker thread immediately (event-driven, no 2 s
        # poll delay).  Best-effort and idempotent — set even if the insert
        # above failed so a same-process waiter re-checks the DB promptly.
        _signal_hitl_event(coworker_id)


# ---------------------------------------------------------------------------
# CoWorkerThread
# ---------------------------------------------------------------------------


class CoWorkerThread(threading.Thread):
    """Execution unit for a single ACE co-worker.

    Runs the co-worker's full role step sequence in a daemon thread, polling
    the message bus between steps and delegating to HITL gates when required
    steps fail.

    Args:
        spec:         Fully-resolved CoWorkerSpec from TeamAssembler.
        instance_id:  ACE instance that owns this coworker.
        message_bus:  Shared MessageBus for this ACE instance.
        trust_kernel: Trust enforcement gateway (TrustKernelBase).
    """

    def __init__(
        self,
        spec: CoWorkerSpec,
        instance_id: str,
        message_bus: MessageBus,
        trust_kernel: Any,
        monitor_interval: int | None = None,
    ) -> None:
        super().__init__(name=f"ace-cw-{spec.coworker_id}", daemon=True)
        self.spec = spec
        self.instance_id = instance_id
        self.message_bus = message_bus
        self.trust_kernel = trust_kernel
        # NOTE: named _ace_context, not _context — Python 3.14's threading.Thread
        # uses an internal `self._context` (a contextvars.Context) to manage
        # contextvars across thread starts. Shadowing it with a plain dict
        # silently kills the thread with no traceback. See memory
        # feedback_ace_threading_context_conflict.md.
        self._ace_context: dict[str, Any] = {"instance_id": instance_id}
        self._stop_event = threading.Event()
        self._step_count = 0
        self._monitor_interval: int = (
            monitor_interval if monitor_interval is not None else _load_monitor_interval()
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the thread to stop at the next step boundary."""
        self._stop_event.set()
        # If the thread is parked in a HITL wait, wake it now so it observes the
        # stop signal promptly instead of after the 30 s fallback poll.
        _signal_hitl_event(self.spec.coworker_id)

    # ------------------------------------------------------------------
    # Event-driven HITL wait
    # ------------------------------------------------------------------

    def _wait_for_hitl_resolution(self) -> bool:
        """Block until this coworker's pending HITL clears or stop is signalled.

        Event-driven: parks on a threading.Event that HITLGate.resolve() (and
        stop()) sets, so approval is observed within milliseconds.  A 30 s
        fallback re-check covers cross-process resolution (a dashboard worker
        inserting hitl_resolved cannot set our in-process Event).

        Returns:
            True  — resolved; the caller may continue.
            False — stop was signalled while waiting.
        """
        # Release the per-run write connection before parking — a HITL wait can
        # last minutes, and holding a (possibly pooled PG) connection idle that
        # long would starve the pool. It recreates lazily on the next write once
        # the gate clears.
        self._close_write_conn()
        ev = _get_hitl_event(self.spec.coworker_id)
        try:
            while not self._stop_event.is_set():
                # Clear BEFORE reading so a resolve() that lands after this read
                # still leaves the Event set → next ev.wait() returns at once
                # (no lost wakeup).
                ev.clear()
                if not HITLGate.get_pending(self.spec.coworker_id):
                    return True
                # Woken by resolve(), stop(), or the cross-process fallback.
                ev.wait(timeout=_HITL_EVENT_WAIT)
            return False
        finally:
            _discard_hitl_event(self.spec.coworker_id)

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._run_inner()
        except Exception as exc:
            self._set_state("failed")
            self._audit("coworker_failed", str(exc))
            logger.exception("CoWorkerThread %s raised unhandled exception", self.spec.coworker_id)
        finally:
            # Close the per-run canvas connection opened lazily for state/audit
            # writes.  Reads in _run_inner manage their own connections.
            self._close_write_conn()

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _run_inner(self) -> None:
        # 0. Enrich context with instance-level data from DB.
        #    ace_instances stores the launch config (including problem_text) in
        #    its config_json column — there is no standalone problem_text column.
        try:
            import json as _json
            from icdev.tools.db.storage import get_canvas_connection
            _conn = get_canvas_connection(_DB_ENV)
            try:
                _row = _conn.execute(
                    "SELECT config_json FROM ace_instances WHERE id = %s",
                    (self.instance_id,),
                ).fetchone()
                if _row:
                    _cfg = dict(_row).get("config_json") or ""
                    if isinstance(_cfg, str):
                        try:
                            _cfg = _json.loads(_cfg)
                        except (ValueError, TypeError):
                            _cfg = {}
                    if isinstance(_cfg, dict):
                        self._ace_context["problem_text"] = _cfg.get("problem_text", "")
            finally:
                _conn.close()
        except Exception as _exc:
            logger.debug("ace context enrich failed for %s: %s", self.instance_id, _exc)

        # 1. Load role
        loader = RoleLoader()
        try:
            role = loader.get_role(self.spec.role_id)
        except RoleNotFoundError as exc:
            self._set_state("failed")
            self._audit("role_not_found", str(exc))
            return

        # 1b. Defensive listen_topics guard — prune reactive topics when task.assigned present
        raw_topics: list[str] = list(role.communication.get("listen_topics") or [])
        _filter_listen_topics(raw_topics, self.spec.coworker_id)
        # NOVA SOUL: inject identity preamble into dispatch context so
        # $soul_preamble is available to all step argument substitutions.
        try:
            from icdev.tools.ace.soul_manager import build_identity_preamble
            preamble = build_identity_preamble(self.spec.role_id)
            # Optional Second Brain enrichment: inject the launching user's world
            # model context. This is best-effort and MUST NOT break core soul
            # injection — inject_user_profile_context may be absent on installs
            # without the Second Brain feature, and an ImportError here previously
            # silently killed the entire preamble.
            try:
                from icdev.tools.ace.soul_manager import inject_user_profile_context
                user_id = getattr(self.spec, "user_id", None) or self._ace_context.get("user_id") or "default"
                preamble = inject_user_profile_context(preamble, user_id)
            except Exception as _uexc:
                logger.debug("[SOUL] user profile enrichment skipped for %s: %s", self.spec.role_id, _uexc)
            if preamble:
                self._ace_context["soul_preamble"] = preamble
                self._audit("soul_preamble_injected", f"role={self.spec.role_id} len={len(preamble)}")
                logger.debug("[SOUL] preamble injected for %s (%d chars)", self.spec.role_id, len(preamble))
        except Exception as exc:
            logger.warning("[SOUL] preamble injection failed for %s: %s", self.spec.role_id, exc)

        # 2. Confidence gate (NOVA TRUST) — if coworker trust_score < 0.6 (supervised
        #    band) require human approval before the step loop begins.
        try:
            from icdev.tools.ace.trust_calibrator import (
                get_trust_score,
                TRUST_SUPERVISED,
            )
            _overrides = _load_trust_overrides()
            _auto_approve = _overrides.get("auto_approve_roles") or []
            _initial_map = _overrides.get("initial_trust") or {}

            if self.spec.role_id in _auto_approve:
                # Operator has pre-authorized this role: skip the gate entirely.
                logger.info(
                    "ace_trust_gate: role=%s in auto_approve_roles — skipping confidence gate",
                    self.spec.role_id,
                )
                self._audit(
                    "hitl_auto_approved",
                    f"role={self.spec.role_id} in auto_approve_roles (confidence gate skipped)",
                )
            else:
                # initial_trust override (if present) seeds the gate decision;
                # otherwise consult the learned trust score.
                if self.spec.role_id in _initial_map:
                    _ts = float(_initial_map[self.spec.role_id])
                else:
                    _ts = get_trust_score(self.spec.role_id)
                if _ts < TRUST_SUPERVISED:
                    logger.info(
                        "ace_trust_gate: coworker=%s role=%s score=%.2f < 0.6 — "
                        "HITL required (supervised band)",
                        self.spec.coworker_id,
                        self.spec.role_id,
                        _ts,
                    )
                    _reason = (
                        f"low_confidence: trust_score={_ts:.2f} "
                        "(supervised band, threshold=0.6)"
                    )
                    self._set_state("hitl_pending")
                    self._audit("hitl_pending", _reason)
                    self._emit_hitl_pending_notification(_reason)
                    if not self._wait_for_hitl_resolution():
                        return  # stop signalled while waiting
                    self._audit("hitl_resolved", "confidence gate cleared by human approval")
                    logger.info(
                        "ace_trust_gate: HITL resolved for %s, proceeding",
                        self.spec.coworker_id,
                    )
        except Exception as _tg_exc:
            logger.debug("ace_trust_gate: check skipped: %s", _tg_exc)

        # 3. Transition to working state
        self._set_state("working")

        # 4. Dispatch: agent mode runs an agentic LLM loop that re-prompts the
        #    LLM after each tool call until it calls `done`; otherwise the
        #    deterministic step list.
        if getattr(role, "mode", "steps") == "agent":
            self._run_agent_loop(role)
        else:
            self._run_step_mode(role)

    # ------------------------------------------------------------------
    # Step-mode loop (deterministic fixed step list)
    # ------------------------------------------------------------------

    def _run_step_mode(self, role: Any) -> None:
        """Execute the role's fixed ``steps`` list (the legacy default mode)."""
        executor = StepExecutor()
        attempted = 0
        succeeded = 0

        # 4 & 5. Execute each step; poll inbox between steps
        for raw_step in role.steps:
            if self._stop_event.is_set():
                break

            # 4. Poll inbox for control messages before each step
            self._drain_inbox()

            if self._stop_event.is_set():
                break

            step = self._normalise_step(raw_step)
            self._set_assigned_step(step.get("id", str(raw_step)))

            attempted += 1
            try:
                result = executor.run(step, self._ace_context, self.spec, self.trust_kernel)
                succeeded += 1
                self._audit(
                    "step_complete",
                    f"step={step.get('id')} result_type={type(result).__name__}",
                )
            except (ToolPermissionDeniedError, TrustKernelDeniedError, ImportError, AttributeError) as exc:
                # 5. Required step failure → HITL gate
                if step.get("required", False):
                    if not self._handle_hitl_required(step, exc):
                        return  # stop signalled during HITL wait
                else:
                    self._audit("step_failed_optional", f"step={step.get('id')} reason={exc}")
                continue

            # 6. Behavioral compliance check every monitor_interval steps
            self._step_count += 1
            if self._monitor_interval > 0 and self._step_count % self._monitor_interval == 0:
                step_text = str(result) if result is not None else ""
                if self._check_behavioral_compliance(step_text, role):
                    return  # instance paused to hitl_pending

        # A co-worker that attempted work and had EVERY step fail has produced
        # nothing. Reporting 'done' there is how the llm_step permission
        # deadlock stayed invisible for 88 of 90 roles: each step raised, each
        # raise was swallowed as optional, and the instance still finished
        # 'complete' with zero artifacts. Individual steps stay optional; total
        # failure is not.
        if attempted and not succeeded:
            self._set_state("failed")
            self._audit(
                "all_steps_failed",
                f"attempted={attempted} succeeded=0 — no output produced",
            )
            return

        self._finish_done(role, detail="all steps completed")

    # ------------------------------------------------------------------
    # Agent-mode loop (agentic LLM re-prompting with native tool use)
    # ------------------------------------------------------------------

    def _run_agent_loop(self, role: Any) -> None:
        """Run an agentic LLM loop (mode=agent): LLM → tool_calls → result → re-prompt."""
        self._role_ref = role  # for _on_agent_turn behavioral compliance checks

        try:
            from icdev.tools.llm.agent_loop import (
                AgentLoopUnsupported,
                run_agent_loop,
                run_agent_loop_with_rubric,
            )
            from icdev.tools.ace.agent_tools import AgentToolRegistry
            from tools.llm.router import LLMRouter
        except ImportError as exc:
            self._set_state("failed")
            self._audit("agent_loop_import_failed", str(exc))
            logger.exception("agent_loop import failed for %s", self.spec.coworker_id)
            return

        # Build tool schema + handlers bound to this coworker's folder_access /
        # icdev_tools / trust_tier scope.
        registry = AgentToolRegistry(self.spec, self.instance_id, stop_event=self._stop_event)
        agent_tools = list(getattr(role, "agent_tools", []) or [])
        tools, tool_handlers = registry.build(agent_tools)
        if not tools:
            self._set_state("failed")
            self._audit("agent_loop_no_tools", f"agent_tools={agent_tools!r} resolved to empty set")
            return

        tool_inventory = ", ".join(sorted(tool_handlers.keys()))
        soul = self._ace_context.get("soul_preamble", "")
        system_prompt = (
            f"{getattr(role, 'description', '') or 'AI co-worker'}\n\n"
            "You are running in an agentic loop. Call tools to make progress and "
            "call `done` once the task is complete and verified. Available tools: "
            f"{tool_inventory}.\n"
        )
        if soul:
            system_prompt += f"\n{soul}\n"

        # Co-learning: inject role-specific improvement suggestions from prior sessions.
        try:
            from icdev.tools.llm.co_learning_store import build_system_prompt_patch as _build_colearn_patch
            _colearn_patch = _build_colearn_patch(self.spec.role_id)
            if _colearn_patch:
                system_prompt = system_prompt + "\n\n" + _colearn_patch
        except Exception:
            pass  # non-fatal

        problem_text = self._ace_context.get("problem_text", "")
        user_prompt = (
            f"INSTANCE: {self.instance_id}\n"
            f"COWORKER: {self.spec.coworker_id} (role={self.spec.role_id})\n\n"
            f"TASK:\n{problem_text or '(no task description provided)'}\n\n"
            "Accomplish the task using the available tools, then call done."
        )

        try:
            router = LLMRouter()
        except Exception as exc:
            self._set_state("failed")
            self._audit("agent_loop_router_failed", str(exc))
            logger.exception("agent_loop router init failed for %s", self.spec.coworker_id)
            return

        max_iter = int(getattr(role, "max_iterations", 12))
        max_total_tokens = getattr(role, "agent_max_total_tokens", None)
        max_cost_usd = getattr(role, "agent_max_cost_usd", None)
        # Per-role cost cap from args/llm_config.yaml → agent_loop.role_cost_caps.
        # Takes the more restrictive of the role YAML cap and the config cap.
        try:
            from icdev.tools.llm.role_cost_caps import get_cap_for_role as _get_cap
            _config_cap = _get_cap(self.spec.role_id)
            if _config_cap is not None:
                max_cost_usd = min(max_cost_usd, _config_cap) if max_cost_usd is not None else _config_cap
                logger.info(
                    "coworker: cost cap $%.2f applied for role=%s",
                    max_cost_usd,
                    self.spec.role_id,
                )
        except Exception:
            pass  # non-fatal; proceed without config cap
        context_window_tokens = getattr(role, "agent_context_window_tokens", None)
        compression_budget_tokens = getattr(role, "agent_compression_budget_tokens", None)
        llm_function = getattr(self.spec, "llm_function", "") or "code_generation"
        self._audit("agent_loop_start", f"tools={tool_inventory} max_iter={max_iter}")

        # ----------------------------------------------------------------
        # Trust-tier pre-tool hook
        # Block write/exec tools before the handler is invoked when the
        # co-worker is not green-tier, giving the LLM a clear permission
        # message rather than a TrustKernelDeniedError traceback.
        # ----------------------------------------------------------------
        _WRITE_EXEC_TOOLS: frozenset[str] = frozenset({"write_file", "run_tool"})
        _trust_tier = self.spec.trust_tier

        def _trust_pre_hook(name: str, inp: dict) -> "str | None":
            if name in _WRITE_EXEC_TOOLS and _trust_tier != "green":
                return (
                    f"Permission denied: '{name}' requires green trust tier; "
                    f"this co-worker is trust_tier={_trust_tier!r}. "
                    "Use a read-only tool or request promotion to green tier."
                )
            return None

        # ----------------------------------------------------------------
        # HITL mid-turn checkpoint hook
        # If the role spec declares hitl_tools, pause and wait for human
        # approval before executing any of those tools mid-loop.
        # ----------------------------------------------------------------
        _hitl_tool_names = frozenset(getattr(role, "hitl_tools", []) or [])
        _hitl_hook = None
        if _hitl_tool_names:
            try:
                from icdev.tools.llm.agent_hitl import build_hitl_hook as _build_hitl
                _hitl_hook = _build_hitl(
                    trigger_tools=_hitl_tool_names,
                    instance_id=self.instance_id,
                    coworker_id=self.spec.coworker_id,
                    stop_event=self._stop_event,
                    timeout_seconds=300,
                    poll_interval_seconds=5.0,
                )
                self._audit(
                    "agent_hitl_armed",
                    f"hitl_tools={sorted(_hitl_tool_names)}",
                )
            except Exception as _hitl_exc:  # noqa: BLE001
                logger.warning("ace: HITL hook init failed for %s: %s", self.spec.coworker_id, _hitl_exc)

        def _combined_pre_hook(name: str, inp: dict) -> "str | None":
            blocked = _trust_pre_hook(name, inp)
            if blocked:
                return blocked
            if _hitl_hook is not None:
                return _hitl_hook(name, inp)
            return None

        # ----------------------------------------------------------------
        # on_stop hook: audit result_subtype + save session for resume.
        # ----------------------------------------------------------------
        _coworker_id = self.spec.coworker_id
        _role_id = self.spec.role_id
        _instance_id = self.instance_id
        _system_prompt_ref = system_prompt

        def _on_stop_hook(loop_result: Any) -> None:
            self._audit(
                "agent_loop_subtype",
                f"result_subtype={loop_result.result_subtype} "
                f"session_id={loop_result.session_id}",
            )
            # Persist session for potential resume.
            try:
                from icdev.tools.llm.agent_loop_session import save_session
                save_session(
                    loop_result,
                    instance_id=_instance_id,
                    coworker_id=_coworker_id,
                    llm_function=llm_function,
                    system_prompt=_system_prompt_ref,
                )
            except Exception as _exc:
                logger.debug("ace: session save failed for %s: %s", _coworker_id, _exc)

            # Save episodic memory entry for this completed agent run.
            try:
                if loop_result.done and loop_result.final_content:
                    from tools.memory.memory_write import write_to_db as _mem_write
                    _mem_write(
                        content=f"[ACE:{_coworker_id}] {loop_result.final_content[:800]}",
                        entry_type="event",
                        importance=min(10, max(1, loop_result.turns)),
                        source="hook",
                        tier="episodic",
                        session_ref=loop_result.session_id,
                        trace_id=getattr(loop_result, "trace_id", None) or None,
                    )
            except Exception as _exc:
                logger.debug("ace: episodic memory save failed for %s: %s", _coworker_id, _exc)

            # Co-learning: persist improvement suggestions so next session benefits.
            try:
                from icdev.tools.llm.co_learning_store import auto_record_from_loop_result as _colearn_record
                _colearn_record(loop_result.session_id, _role_id, loop_result)
            except Exception as _exc:
                logger.debug("ace: co-learning record failed for %s: %s", _coworker_id, _exc)

            try:
                from icdev.tools.ace import event_bus as _eb
                _eb.publish(_instance_id, {
                    "type": "loop_done",
                    "coworker_id": _coworker_id,
                    "result_subtype": loop_result.result_subtype,
                    "turns": loop_result.turns,
                    "done": loop_result.done,
                    "session_id": loop_result.session_id,
                })
            except Exception:
                pass

        # Every kwarg the plain loop uses; the opt-in rubric wrapper forwards the
        # exact same set via **loop_kwargs (run_agent_loop_with_rubric passes
        # them straight through to run_agent_loop).
        loop_kwargs = dict(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            tool_handlers=tool_handlers,
            llm_function=llm_function,
            max_iterations=max_iter,
            stop_event=self._stop_event,
            on_turn=self._on_agent_turn,
            on_pre_tool_use=_combined_pre_hook,
            on_stop=_on_stop_hook,
            max_total_tokens=max_total_tokens,
            max_cost_usd=max_cost_usd,
            context_window_tokens=context_window_tokens,
            compression_budget_tokens=compression_budget_tokens,
            parent_session_id=getattr(self, "_parent_session_id", ""),
            tenant_id=getattr(self, "tenant_id", ""),
            user_id=getattr(self, "user_id", ""),
        )

        # Opt-in real-time rubric gating: a role that declares `rubric: pipeline`
        # is graded by the delivery pipeline (build -> gates -> revise in-session)
        # instead of trusting the loop's own `done`. Absent/other = plain loop
        # (byte-unchanged behaviour). Agent-mode co-workers edit the SHARED repo
        # checkout scoped by folder_access (no isolated worktree), so the grader
        # runs against that repo root — the same root FileAccessBroker resolves.
        _rubric_mode = str(getattr(role, "rubric", "") or "").strip().lower()
        try:
            if _rubric_mode == "pipeline":
                from icdev.tools.ace.file_access_broker import _REPO_ROOT as _repo_root
                from tools.workflow.pipeline_grader import make_pipeline_grader

                def _changed(_root=str(_repo_root)):
                    # Repo files changed vs origin/main; never let a diff failure
                    # crash the grader (make_pipeline_grader accepts a callable).
                    try:
                        from tools.integrity.pr_gates import _git_changed_files
                        return _git_changed_files("origin/main", False, Path(_root))
                    except Exception:
                        return []

                grader = make_pipeline_grader(
                    cwd=str(_repo_root),
                    task_id=self.instance_id,
                    modified_files=_changed,
                    run_e2e=False,
                    # Conformance needs a kanban task's acceptance criteria; an
                    # agent-mode co-worker has none, so skip it (record-only).
                    run_conformance=False,
                    compare_to_main=True,
                )
                self._audit("agent_loop_rubric", "delivery-pipeline rubric gating enabled")
                rubric_result = run_agent_loop_with_rubric(
                    router,
                    grader=grader,
                    max_grading_iterations=3,
                    # One harness_eval decision keyed on the run instance
                    # (mirrors the kanban runner keying on the task id).
                    harness_task_id=self.instance_id,
                    **loop_kwargs,
                )
                self._audit(
                    "agent_loop_rubric_done",
                    f"satisfied={rubric_result.satisfied} "
                    f"grading_attempts={rubric_result.grading_attempts}",
                )
                result = rubric_result.result
            else:
                result = run_agent_loop(router, **loop_kwargs)
        except AgentLoopUnsupported as exc:
            # Provider can't do native tool use — fall back to step mode with audit.
            self._audit("agent_loop_unsupported", str(exc))
            logger.warning(
                "ace agent_loop unsupported for %s: %s — falling back to step mode",
                self.spec.coworker_id,
                exc,
            )
            self._run_step_mode(role)
            return
        except Exception as exc:
            self._set_state("failed")
            self._audit("agent_loop_failed", str(exc))
            logger.exception("agent_loop failed for %s", self.spec.coworker_id)
            return

        self._audit(
            "agent_loop_complete",
            f"done={result.done} truncated={result.truncated} turns={result.turns} "
            f"tool_calls={len(result.tool_call_log)} subtype={result.result_subtype} "
            f"session_id={result.session_id}",
        )
        detail = "agent loop done" if result.done else f"agent loop truncated ({result.result_subtype})"
        self._finish_done(role, detail=detail)

    def _on_agent_turn(self, turn: int, response: Any, messages: list[dict[str, Any]]) -> None:
        """Persist + audit each agent-loop turn; periodic behavioral compliance check."""
        text = getattr(response, "content", "") or ""
        tool_calls = getattr(response, "tool_calls", None) or []
        tool_summary = ", ".join(
            f"{tc.get('name')}({list((tc.get('input') or {}).keys())})" for tc in tool_calls
        ) or "(no tools)"
        self._set_assigned_step(f"agent_turn_{turn + 1}")
        self._audit("agent_turn", f"turn={turn + 1} tools={tool_summary}")
        self._persist_agent_turn(turn + 1, text, tool_summary)

        try:
            from icdev.tools.ace import event_bus as _eb
            _eb.publish(self.instance_id, {
                "type": "agent_turn",
                "coworker_id": self.spec.coworker_id,
                "turn": turn + 1,
                "tool_summary": tool_summary,
                "text_len": len(text),
            })
        except Exception:
            pass

        # Behavioral compliance check every monitor_interval turns.
        self._step_count += 1
        if self._monitor_interval > 0 and self._step_count % self._monitor_interval == 0:
            if self._check_behavioral_compliance(text or tool_summary, getattr(self, "_role_ref", None)):
                return  # paused to hitl_pending (state set by _trigger_compliance_hitl)

    def _persist_agent_turn(self, turn: int, text: str, tool_summary: str) -> None:
        """Write one agent-loop turn to ``ace_messages`` (best-effort).

        Mirrors ``MessageBus._persist`` — the same table the dashboard's
        ``/api/ace/<id>/messages`` endpoint reads — so agent-mode turns appear
        in the co-worker chat view alongside step-mode messages. Uses the
        canvas connection's placeholder style and a uuid id; ``created_at``
        defaults to now() in the schema.
        """
        try:
            import json as _json
            import uuid as _uuid

            self._write(
                "INSERT INTO ace_messages "
                "(id, instance_id, coworker_id, message_type, role, content, metadata_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    str(_uuid.uuid4()),
                    self.instance_id,
                    self.spec.coworker_id,
                    "agent_turn",
                    "assistant",
                    f"{text}\n[tools: {tool_summary}]",
                    _json.dumps(
                        {"turn": turn, "tools": tool_summary},
                        ensure_ascii=False,
                    ),
                ),
                "persist_agent_turn",
            )
        except Exception as exc:
            logger.debug("ace agent turn persist failed for %s: %s", self.spec.coworker_id, exc)

    # ------------------------------------------------------------------
    # Shared completion
    # ------------------------------------------------------------------

    def _finish_done(self, role: Any, detail: str = "completed") -> None:
        """Broadcast done, set state=done, audit, and capture success pattern for green tier."""
        try:
            self.message_bus.broadcast(
                self.spec.coworker_id,
                "cw_broadcast",
                {
                    "event": "done",
                    "instance_id": self.instance_id,
                    "coworker_id": self.spec.coworker_id,
                },
            )
        except Exception as exc:
            logger.warning("broadcast failed for %s: %s", self.spec.coworker_id, exc)

        self._set_state("done")
        self._audit("coworker_done", detail)
        self._post_completion_chat_feedback(role, detail)

        # Phase 3: capture success pattern for green-tier co-workers (SIPA-gated promotion)
        if self.spec.trust_tier == "green":
            self._capture_success_pattern(role)

    # ------------------------------------------------------------------
    # Inbox message handling
    # ------------------------------------------------------------------

    def _drain_inbox(self) -> None:
        """Non-blocking inbox drain — handles ACE control messages."""
        try:
            messages = self.message_bus.poll_inbox(self.spec.coworker_id, timeout_s=0.1)
        except Exception:
            return

        for msg in messages:
            subject = msg.get("subject", "")
            msg_type = subject.removeprefix("ACE:") if subject.startswith("ACE:") else ""
            try:
                payload: dict[str, Any] = json.loads(msg.get("body") or "{}")
            except (json.JSONDecodeError, ValueError):
                payload = {}

            if msg_type == "cw_verify_request":
                self._handle_verify_request(payload)
            elif msg_type == "cw_negotiate_propose":
                self._handle_negotiate_propose(payload)

    def _handle_verify_request(self, payload: dict[str, Any]) -> None:
        """Suspend, execute verification steps, then resume."""
        verify_steps = payload.get("steps", [])
        self._set_state("suspended")
        self._audit("verify_suspended", f"verify_steps_count={len(verify_steps)}")

        executor = StepExecutor()
        for raw_vstep in verify_steps:
            vstep = self._normalise_step(raw_vstep)
            try:
                executor.run(vstep, self._ace_context, self.spec, self.trust_kernel)
            except Exception as exc:
                self._audit("verify_step_failed", f"step={vstep.get('id')} reason={exc}")

        self._set_state("working")
        self._audit("verify_resumed", "verification steps complete")

    def _handle_negotiate_propose(self, payload: dict[str, Any]) -> None:
        """Enter negotiation handler for an incoming proposal."""
        to_role = payload.get("to_role", "")
        self._audit("negotiate_received", f"to_role={to_role}")
        try:
            self.message_bus.negotiate(
                from_coworker_id=self.spec.coworker_id,
                to_role=to_role or self.spec.role_id,
                payload=payload,
            )
        except Exception as exc:
            self._audit("negotiate_failed", str(exc))

    # ------------------------------------------------------------------
    # HITL handling
    # ------------------------------------------------------------------

    def _handle_hitl_required(self, step: dict[str, Any], exc: Exception) -> bool:
        """Create HITL instance, set state=hitl_pending, poll for resolution.

        Returns:
            True if resolved and work can continue.
            False if stop was signalled while waiting.
        """
        step_id = step.get("id", "unknown")
        hitl_detail = f"step={step_id}"

        self._set_state("hitl_pending")
        self._audit("hitl_pending", hitl_detail)
        self._emit_hitl_pending_notification(f"required step failed: {hitl_detail}")
        logger.warning(
            "HITL required: coworker=%s step=%s reason=%s",
            self.spec.coworker_id,
            step_id,
            exc,
        )

        # Event-driven wait — wakes the instant a human resolves the gate.
        if not self._wait_for_hitl_resolution():
            return False

        self._set_state("working")
        return True

    # ------------------------------------------------------------------
    # Behavioral compliance monitoring
    # ------------------------------------------------------------------

    def _check_behavioral_compliance(self, step_output: str, role: Any) -> bool:
        """Run Mode B reconciliation on step output; trigger HITL if dangerous.

        Returns True when HITL was triggered (caller should stop the thread).
        """
        try:
            from tools.integrity.intent_reconciler import reconcile_mode_b

            claimed = list(getattr(role, "tool_permissions", []) or [])
            findings = reconcile_mode_b(step_output, claimed)
            dangerous = [
                f for f in findings
                if f.get("finding_type") in ("undisclosed_capability", "dangerous_api")
            ]
            if dangerous:
                self._trigger_compliance_hitl(dangerous)
                return True
        except Exception as exc:
            logger.warning(
                "behavioral_monitor: reconcile failed for %s: %s",
                self.spec.coworker_id,
                exc,
            )
        return False

    def _trigger_compliance_hitl(self, findings: list[dict]) -> None:
        """Pause instance to hitl_pending, emit compliance event, create kanban card."""
        self._set_state("hitl_pending")
        self._audit("compliance_gap_found", f"findings_count={len(findings)}")
        logger.warning(
            "behavioral_monitor: %d compliance finding(s) for coworker=%s — entering hitl_pending",
            len(findings),
            self.spec.coworker_id,
        )
        self._emit_compliance_event(findings)
        self._create_hitl_kanban_card(findings)
        self._stop_event.set()

    def _emit_compliance_event(self, findings: list[dict]) -> None:
        """Publish compliance.gap.found to the canvas event bus (best-effort)."""
        try:
            from icdev.tools.canvas.event_bus import publish

            publish(
                source_canvas="ace",
                event_type="compliance.gap.found",
                payload_dict={
                    "topic": "compliance.gap.found",
                    "instance_id": self.instance_id,
                    "coworker_id": self.spec.coworker_id,
                    "findings_count": len(findings),
                    "finding_types": sorted({f.get("finding_type") for f in findings}),
                },
                target_canvas="ace",
            )
        except Exception as exc:
            logger.warning(
                "behavioral_monitor: event publish failed for %s: %s",
                self.spec.coworker_id,
                exc,
            )

    def _create_hitl_kanban_card(self, findings: list[dict]) -> None:
        """Insert a backlog HITL kanban task for human review (best-effort)."""
        try:
            from icdev.tools.db.storage import get_connection

            now = datetime.now(timezone.utc).isoformat()
            card_id = f"ace-hitl-{self.spec.coworker_id[:8]}-{uuid.uuid4().hex[:6]}"
            summary = "; ".join(
                f"{f.get('finding_type')}({f.get('severity', '?')})" for f in findings[:3]
            )
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO kanban_tasks "
                    "(id, title, description, task_type, priority, status, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        card_id,
                        f"HITL Review: ACE compliance gap — {self.spec.coworker_id}",
                        (
                            f"Behavioral monitor detected a compliance gap in ACE instance "
                            f"'{self.instance_id}' coworker '{self.spec.coworker_id}'.\n"
                            f"Findings: {summary}"
                        ),
                        "hitl",
                        "high",
                        "backlog",
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning(
                "behavioral_monitor: kanban card creation failed for %s: %s",
                self.spec.coworker_id,
                exc,
            )

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Per-run canvas connection reuse
    #
    # State/assigned-step/audit/message writes happen dozens of times per run.
    # Opening and closing a fresh canvas connection for each was wasteful, so a
    # single connection is created lazily on first write and reused for the rest
    # of the run (closed in run()'s finally).  Connections can die mid-run, so
    # _write() closes and recreates once on any failure before giving up.
    # ------------------------------------------------------------------

    def _get_write_conn(self):
        """Return the lazily-created per-run canvas connection."""
        conn = getattr(self, "_canvas_conn", None)
        if conn is None:
            from icdev.tools.db.storage import get_canvas_connection

            conn = get_canvas_connection(_DB_ENV)
            self._canvas_conn = conn
        return conn

    def _close_write_conn(self) -> None:
        """Close and drop the per-run canvas connection (best-effort)."""
        conn = getattr(self, "_canvas_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._canvas_conn = None

    def _write(self, sql: str, params: tuple, label: str) -> None:
        """Execute one write on the reused connection; retry once on failure.

        A dead/aborted connection is discarded and re-created a single time so a
        transient DB hiccup doesn't silently drop every subsequent write.
        """
        for attempt in (1, 2):
            try:
                conn = self._get_write_conn()
                conn.execute(sql, params)
                conn.commit()
                return
            except Exception as exc:
                # Drop the (possibly poisoned) connection; a fresh one is made on
                # the retry.  On the second failure, log and give up (best-effort).
                self._close_write_conn()
                if attempt == 2:
                    logger.warning("ace %s write failed for %s: %s", label, self.spec.coworker_id, exc)

    def _set_state(self, state: str) -> None:
        """Update ace_coworkers.state for this coworker (best-effort)."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._write(
            "UPDATE ace_coworkers SET state = %s, last_active_at = %s WHERE id = %s",
            (state, now, self.spec.coworker_id),
            "set_state",
        )

    def _set_assigned_step(self, step_id: str) -> None:
        """Update ace_coworkers.assigned_step (best-effort)."""
        self._write(
            "UPDATE ace_coworkers SET assigned_step = %s WHERE id = %s",
            (step_id, self.spec.coworker_id),
            "set_assigned_step",
        )

    def _audit(self, action: str, detail: str = "") -> None:
        """Append one row to ace_audit_log (best-effort, never crashes)."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Canvas DB is PG-primary at runtime — use %s placeholders.
        self._write(
            "INSERT INTO ace_audit_log "
            "(instance_id, coworker_id, action, detail, actor, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (self.instance_id, self.spec.coworker_id, action, detail, "coworker_thread", now),
            "audit",
        )

    # ------------------------------------------------------------------
    # HITL visibility — SSE + canvas notification
    # ------------------------------------------------------------------

    def _emit_hitl_pending_notification(self, reason: str) -> None:
        """Actively announce that a co-worker has entered the HITL gate.

        Three best-effort channels (each independently guarded):
          1. in-process ACE event bus → the instance ``/api/ace/<id>/stream`` SSE
          2. dashboard-wide ``ace_progress`` SSE (home monitor tile)
          3. canvas event bus (mirrors _emit_compliance_event) — durable notice
        """
        payload = {
            "type": "hitl_pending",
            "instance_id": self.instance_id,
            "coworker_id": self.spec.coworker_id,
            "role_id": self.spec.role_id,
            "reason": reason,
        }
        try:
            from icdev.tools.ace import event_bus as _eb
            _eb.publish(self.instance_id, payload)
        except Exception:
            pass
        try:
            from tools.dashboard.sse_manager import sse_manager
            sse_manager.broadcast(
                {
                    "type": "ace_progress",
                    "instance_id": self.instance_id,
                    "coworker_id": self.spec.coworker_id,
                    "phase": "hitl_pending",
                    "detail": reason,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                event_type="ace_progress",
            )
        except Exception:
            pass
        try:
            from icdev.tools.canvas.event_bus import publish
            publish(
                source_canvas="ace",
                event_type="hitl.pending",
                payload_dict={
                    "topic": "hitl.pending",
                    "instance_id": self.instance_id,
                    "coworker_id": self.spec.coworker_id,
                    "role_id": self.spec.role_id,
                    "reason": reason,
                },
                target_canvas="ace",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Chat feedback loop — write completion summary back to originating chat context
    # ------------------------------------------------------------------

    def _post_completion_chat_feedback(self, role: Any, detail: str) -> None:
        """Write a co-worker completion summary into the originating chat context.

        Reads trigger_ref from ace_instances.config_json.  Only writes when
        trigger_source == 'chat' and trigger_ref is a non-empty context ID.
        No-ops gracefully on any error so completion is never blocked.
        """
        try:
            from icdev.tools.db.storage import get_canvas_connection

            conn = get_canvas_connection(_DB_ENV)
            try:
                row = conn.execute(
                    "SELECT config_json FROM ace_instances WHERE id = %s",
                    (self.instance_id,),
                ).fetchone()
            finally:
                conn.close()

            if not row:
                return

            cfg: dict[str, Any] = {}
            try:
                cfg = json.loads(row[0] if isinstance(row, (tuple, list)) else dict(row).get("config_json", "{}") or "{}")
            except Exception:
                return

            if cfg.get("trigger_source") != "chat":
                return

            ctx_id: str = cfg.get("trigger_ref", "")
            if not ctx_id:
                return

            role_display = getattr(role, "display_name", None) or self.spec.role_id
            problem_text = cfg.get("problem_text", "")
            summary_lines = [
                f"**Co-worker `{self.spec.coworker_id}` ({role_display}) completed.**",
                f"Instance: `{self.instance_id}`",
            ]
            if problem_text:
                excerpt = problem_text[:200].rstrip()
                if len(problem_text) > 200:
                    excerpt += "…"
                summary_lines.append(f"Task: {excerpt}")
            summary_lines.append(f"Detail: {detail}")
            summary = "\n".join(summary_lines)

            user_id = cfg.get("user_id", "system")
            mgr = ChatManager(user_id=user_id, classification="CUI")
            mgr.add_message(
                ctx_id,
                role="assistant",
                content=summary,
                content_type="action_card",
                metadata={
                    "source": "ace_coworker",
                    "instance_id": self.instance_id,
                    "coworker_id": self.spec.coworker_id,
                    "role_id": self.spec.role_id,
                },
            )
            logger.debug(
                "ace_chat_feedback: wrote completion summary to ctx=%s for coworker=%s",
                ctx_id,
                self.spec.coworker_id,
            )
        except Exception as exc:
            logger.debug(
                "ace_chat_feedback: no-op for coworker=%s: %s",
                self.spec.coworker_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Phase 3: success pattern capture
    # ------------------------------------------------------------------

    def _capture_success_pattern(self, role: Any) -> None:
        """Persist a success pattern to ace_skill_candidates for SIPA-gated promotion.

        Only called for green-tier co-workers that completed all steps without
        error.  Best-effort — never raises or crashes the caller.
        """
        try:
            import yaml
            import uuid as _uuid

            from icdev.tools.db.storage import get_canvas_connection

            # Derive candidate role_id: versioned slug so it doesn't overwrite the live role
            role_id = getattr(role, "role_id", self.spec.role_id)
            candidate_id = str(_uuid.uuid4())
            candidate_role_id = f"{role_id}_success_{candidate_id[:8]}"

            # Build a minimal candidate YAML from the role's current proven config
            steps = [
                (s.name if hasattr(s, "name") else str(s)) for s in getattr(role, "steps", [])
            ]
            spec: dict = {
                "role_id": candidate_role_id,
                "display_name": f"{getattr(role, 'display_name', role_id)} (Promoted)",
                "description": (
                    f"Auto-promoted success pattern from role '{role_id}', "
                    f"instance '{self.instance_id}', coworker '{self.spec.coworker_id}'."
                ),
                "version": "1.0",
                "source": "promoted_candidate",
                "trust_tier": self.spec.trust_tier,
                "steps": steps,
                "llm_function": getattr(self.spec, "llm_function", ""),
                "tool_permissions": list(getattr(self.spec, "tool_permissions", [])),
                "folder_access": list(getattr(self.spec, "folder_access", [])),
                "icdev_tools": list(getattr(self.spec, "icdev_tools", [])),
                "communication": dict(getattr(role, "communication", {})),
                "genesis_reflex": getattr(role, "genesis_reflex", ""),
            }
            candidate_yaml = yaml.dump(spec, allow_unicode=True, sort_keys=False)

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "INSERT INTO ace_skill_candidates "
                    "(id, role_id, source_role, instance_id, candidate_yaml, trust_tier, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)",
                    (candidate_id, candidate_role_id, role_id, self.instance_id, candidate_yaml, self.spec.trust_tier, now),
                )
                conn.commit()
            finally:
                conn.close()

            self._audit(
                "skill_candidate_captured",
                f"candidate_id={candidate_id} source_role={role_id}",
            )
            logger.debug(
                "[ACE skill] captured success pattern: candidate_id=%s role=%s",
                candidate_id,
                candidate_role_id,
            )
        except Exception as exc:
            logger.debug("_capture_success_pattern failed for %s: %s", self.spec.coworker_id, exc)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _normalise_step(self, raw_step: Any) -> dict[str, Any]:
        """Convert a role step into the dict schema ``StepExecutor.run`` expects.

        Accepts three shapes:

        - ``dict``  — already in executor schema; passed through untouched.
        - ``RoleStep`` — the dataclass ``RoleLoader`` actually produces
          (``name``/``tool``/``params``/``condition``). If it declares a
          ``tool``, that tool is invoked with ``params`` as args and the
          declared ``condition`` preserved; otherwise it degrades to the same
          LLM invocation as a bare string.
        - ``str``   — a bare step name, expanded into an LLM invocation.

        RoleStep used to fall through to the ``str(raw_step)`` branch, so the
        step id became the dataclass repr —
        ``"RoleStep(name='analyze_requirements', tool='', params={}, condition=None)"``
        — which was written to ``ace_coworkers.assigned_step`` and fed into the
        LLM prompt, while any declared ``tool``/``params``/``condition`` was
        silently discarded.
        """
        if isinstance(raw_step, dict):
            return raw_step

        # RoleStep (or anything exposing the same attributes) — duck-typed so a
        # test double does not have to import the dataclass.
        name = getattr(raw_step, "name", None)
        if name is not None:
            step_name = str(name)
            declared_tool = str(getattr(raw_step, "tool", "") or "")
            condition = getattr(raw_step, "condition", None)
            if declared_tool:
                return {
                    "id": step_name,
                    "tool": declared_tool,
                    "args": dict(getattr(raw_step, "params", None) or {}),
                    "condition": condition,
                    "output_var": f"{step_name}_result",
                    "required": False,
                }
            step = self._llm_step(step_name)
            if condition is not None:
                step["condition"] = condition
            return step

        return self._llm_step(str(raw_step))

    def _llm_step(self, step_name: str) -> dict[str, Any]:
        """Build an LLM-invoke step dict for a step that declares no tool."""
        return {
            "id": step_name,
            "tool": "icdev.tools.ace.llm_step.invoke",
            "args": {
                "step_name": step_name,
                "instance_id": self.instance_id,
                "coworker_id": self.spec.coworker_id,
                "llm_function": self.spec.llm_function or "code_generation",
                "problem_text": self._ace_context.get("problem_text", ""),
                "role_description": self.spec.description or "",
            },
            "output_var": f"{step_name}_result",
            "required": False,
        }
