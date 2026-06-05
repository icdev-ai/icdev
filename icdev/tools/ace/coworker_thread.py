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
from datetime import datetime, timezone
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
    ) -> None:
        super().__init__(name=f"ace-cw-{spec.coworker_id}", daemon=True)
        self.spec = spec
        self.instance_id = instance_id
        self.message_bus = message_bus
        self.trust_kernel = trust_kernel
        self._context: dict[str, Any] = {"instance_id": instance_id}
        self._stop_event = threading.Event()

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
