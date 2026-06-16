# CUI // SP-CTI
"""ACE CoWorkerThread — execution unit for a single ACE co-worker.

Each CoWorkerThread runs one co-worker's full role step sequence in a
dedicated daemon thread, handling inter-coworker messaging and HITL gates.
"""
from __future__ import annotations

import json
import logging
import threading
import time
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

logger = logging.getLogger("icdev.ace.coworker_thread")

_DB_ENV = "ICDEV_ACE_DB_URL"
_HITL_POLL_INTERVAL = 2.0  # seconds between HITL resolution checks
_DEFAULT_MONITOR_INTERVAL = 10  # steps between behavioral compliance checks


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
                       WHERE coworker_id = ? AND action = 'hitl_pending'
                       ORDER BY created_at DESC""",
                    (coworker_id,),
                ).fetchall()

                if not pending_rows:
                    return []

                resolved_rows = conn.execute(
                    """SELECT detail FROM ace_audit_log
                       WHERE coworker_id = ? AND action = 'hitl_resolved'""",
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
                    "VALUES (?, ?, 'hitl_resolved', ?, 'hitl_gate', ?)",
                    (instance_id, coworker_id, detail, now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass


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
        self._context: dict[str, Any] = {"instance_id": instance_id}
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

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _run_inner(self) -> None:
        # 1. Load role
        loader = RoleLoader()
        try:
            role = loader.get_role(self.spec.role_id)
        except RoleNotFoundError as exc:
            self._set_state("failed")
            self._audit("role_not_found", str(exc))
            return

        # 2. Transition to working state
        self._set_state("working")

        executor = StepExecutor()

        # 3 & 4. Execute each step; poll inbox between steps
        for raw_step in role.steps:
            if self._stop_event.is_set():
                break

            # 4. Poll inbox for control messages before each step
            self._drain_inbox()

            if self._stop_event.is_set():
                break

            step = self._normalise_step(raw_step)
            self._set_assigned_step(step.get("id", str(raw_step)))

            try:
                result = executor.run(step, self._context, self.spec, self.trust_kernel)
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

        # 6. Broadcast completion and update state
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
        self._audit("coworker_done", "all steps completed")

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
                executor.run(vstep, self._context, self.spec, self.trust_kernel)
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
        logger.warning(
            "HITL required: coworker=%s step=%s reason=%s",
            self.spec.coworker_id,
            step_id,
            exc,
        )

        # Poll HITLGate until the pending item is resolved or stop is signalled
        while not self._stop_event.is_set():
            pending = HITLGate.get_pending(self.spec.coworker_id)
            if not pending:
                break
            time.sleep(_HITL_POLL_INTERVAL)

        if self._stop_event.is_set():
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
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
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

    def _set_state(self, state: str) -> None:
        """Update ace_coworkers.state for this coworker (best-effort)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "UPDATE ace_coworkers SET state = ?, last_active_at = ? WHERE id = ?",
                    (state, now, self.spec.coworker_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as db_exc:
            logger.debug("_set_state(%s) failed: %s", state, db_exc)

    def _set_assigned_step(self, step_id: str) -> None:
        """Update ace_coworkers.assigned_step (best-effort)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "UPDATE ace_coworkers SET assigned_step = ? WHERE id = ?",
                    (step_id, self.spec.coworker_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def _audit(self, action: str, detail: str = "") -> None:
        """Append one row to ace_audit_log (best-effort, never crashes)."""
        try:
            from icdev.tools.db.storage import get_canvas_connection

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn = get_canvas_connection(_DB_ENV)
            try:
                conn.execute(
                    "INSERT INTO ace_audit_log "
                    "(instance_id, coworker_id, action, detail, actor, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (self.instance_id, self.spec.coworker_id, action, detail, "coworker_thread", now),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # audit is best-effort; never crash the thread

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_step(raw_step: Any) -> dict[str, Any]:
        """Convert a string step ID to a minimal step dict if needed."""
        if isinstance(raw_step, dict):
            return raw_step
        return {"id": str(raw_step)}
