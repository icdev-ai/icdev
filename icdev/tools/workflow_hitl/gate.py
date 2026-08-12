# CUI // SP-CTI
"""HITLGate — hooks into the Kanban state machine to block in_progress→done.

Fail-closed contract (exa-policy-06)
-----------------------------------
This module answers exactly one question: *is a human approval outstanding for
this task?*  A "no" from here is what lets the Kanban state machine advance a
task to ``done``.

Until exa-policy-06 the lookup was wrapped in ``except Exception: return None``,
so a DB outage, a missing ``wf_approvals`` table, or an RLS error produced the
same answer as "no approval is pending" — and the transition proceeded.  An
approval gate that approves whenever it malfunctions is not a gate.

The lookup now raises :class:`HITLGateUnavailable` when it cannot determine the
answer.  Callers must treat that as *gated* (block), never as *clear*.
``should_gate`` does exactly that and returns ``True``.

Staged rollout: setting ``ICDEV_HITL_GATE_FAIL_OPEN=1`` restores the legacy
fail-open behaviour for a deployment whose HITL tables were never migrated.  It
logs at ERROR on every use so the escape hatch cannot be left on quietly.
"""
from __future__ import annotations

import os

from tools.logging.icdev_logger import get_logger


from tools.db.storage import get_connection

logger = get_logger(__name__)


class HITLGateUnavailable(RuntimeError):
    """The gate could not determine whether an approval is pending.

    Raised instead of returning ``None`` so that an infrastructure failure is
    distinguishable from a genuine "no approval outstanding". Callers must fail
    closed on this — block the transition.
    """

    def __init__(self, task_id: str, cause: BaseException):
        self.task_id = task_id
        self.cause = cause
        super().__init__(
            f"HITL approval state for task {task_id!r} is undeterminable "
            f"({type(cause).__name__}: {cause}) — failing closed"
        )


def _fail_open_enabled() -> bool:
    """True when the operator has explicitly opted back into legacy fail-open."""
    return os.getenv("ICDEV_HITL_GATE_FAIL_OPEN", "").strip().lower() in ("1", "true", "yes")


class HITLGate:

    def should_gate(self, task_id: str) -> bool:
        """Return True if this task must not advance.

        True means either (a) an approval is genuinely pending, or (b) the gate
        could not tell — both block. Only a clean "nothing pending" returns
        False.
        """
        try:
            return self.get_pending(task_id) is not None
        except HITLGateUnavailable:
            # Fail closed: undeterminable == gated.
            return True

    def get_pending(self, task_id: str) -> dict | None:
        """Return the pending approval dict for this task, or None if no gate active.

        Raises:
            HITLGateUnavailable: the approval state could not be read. Never
                swallow this into ``None`` — that is the fail-open bug this
                method exists to avoid.
        """
        try:
            conn = get_connection()
        except Exception as exc:
            if _fail_open_enabled():
                logger.error(
                    "HITL gate FAILING OPEN for %s (ICDEV_HITL_GATE_FAIL_OPEN=1) — "
                    "could not connect: %s", task_id, exc,
                )
                return None
            raise HITLGateUnavailable(task_id, exc) from exc

        try:
            row = conn.execute(
                """SELECT wa.* FROM wf_approvals wa
                   JOIN wf_instances wi ON wi.id = wa.instance_id
                   WHERE wi.task_id=%s
                     AND wa.status='pending'
                     AND wi.status IN ('active','waiting_external')
                   ORDER BY wa.created_at DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            if _fail_open_enabled():
                logger.error(
                    "HITL gate FAILING OPEN for %s (ICDEV_HITL_GATE_FAIL_OPEN=1) — "
                    "approval lookup failed: %s", task_id, exc,
                )
                return None
            logger.warning(
                "HITL gate could not read approval state for %s: %s — failing closed",
                task_id, exc,
            )
            raise HITLGateUnavailable(task_id, exc) from exc
        finally:
            try:
                conn.close()
            except Exception:  # nosec B110 — close failure must not mask the real error
                pass

    def get_instance_for_task(self, task_id: str) -> dict | None:
        """Return the active workflow instance for a task.

        Raises:
            HITLGateUnavailable: the instance state could not be read.
        """
        try:
            conn = get_connection()
        except Exception as exc:
            if _fail_open_enabled():
                logger.error(
                    "HITL gate FAILING OPEN for %s (ICDEV_HITL_GATE_FAIL_OPEN=1) — "
                    "could not connect: %s", task_id, exc,
                )
                return None
            raise HITLGateUnavailable(task_id, exc) from exc

        try:
            row = conn.execute(
                "SELECT * FROM wf_instances WHERE task_id=%s AND status IN ('active','waiting_external') LIMIT 1",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            if _fail_open_enabled():
                logger.error(
                    "HITL gate FAILING OPEN for %s (ICDEV_HITL_GATE_FAIL_OPEN=1) — "
                    "instance lookup failed: %s", task_id, exc,
                )
                return None
            logger.warning(
                "HITL gate could not read instance state for %s: %s — failing closed",
                task_id, exc,
            )
            raise HITLGateUnavailable(task_id, exc) from exc
        finally:
            try:
                conn.close()
            except Exception:  # nosec B110 — close failure must not mask the real error
                pass
