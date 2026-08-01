# CUI // SP-CTI
"""OPT-72: Kanban state machine.

Adapted from jonwiggins/optio/packages/shared (MIT).
See https://github.com/jonwiggins/optio

Defines a formal state machine for kanban tasks with explicit transition
rules and a resume-cycle cap. Pure Python — no DB schema changes.

Persistence note:
    ICDEV's kanban_tasks.status CHECK constraint currently allows only
    {backlog, scheduled, in_progress, done, token_exhausted, suggested}.
    Extended states (pr_opened, ci_failed, merge_conflict,
    changes_requested, failed) are MAPPED DOWN to one of those when
    written to the DB. The finer-grained state is recorded in the
    audit_trail details JSON under 'workflow_state', which tools like
    pr_watcher (OPT-70) can consult as the authoritative source.

Transitions:
    backlog → scheduled → in_progress → pr_opened
                            ↓              ↓
                     token_exhausted ← ci_failed / merge_conflict /
                                       changes_requested → in_progress
                                                              ↓
                                                             done
                                                             failed
"""
from __future__ import annotations
from tools.kanban.gates import is_manual_gate
from tools.logging.icdev_logger import get_logger

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = get_logger(__name__)


class KanbanState(str, Enum):
    SUGGESTED = "suggested"
    BACKLOG = "backlog"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    PR_OPENED = "pr_opened"
    CI_FAILED = "ci_failed"
    MERGE_CONFLICT = "merge_conflict"
    CHANGES_REQUESTED = "changes_requested"
    TOKEN_EXHAUSTED = "token_exhausted"
    DECOMPOSED = "decomposed"
    DONE = "done"
    FAILED = "failed"


#: States that are terminal — no further transitions allowed.
TERMINAL_STATES: Set[KanbanState] = {
    KanbanState.DONE,
    KanbanState.FAILED,
}

#: Set of states where the task is "actively being worked". Resume
#: classifier transitions these into IN_PROGRESS to re-enter the loop.
RESUMABLE_STATES: Set[KanbanState] = {
    KanbanState.CI_FAILED,
    KanbanState.MERGE_CONFLICT,
    KanbanState.CHANGES_REQUESTED,
}

#: Max times a task can re-enter IN_PROGRESS from a resumable state
#: before auto-failing. Guards against infinite CI-failed loops.
MAX_RESUME_CYCLES = 5


#: Transition table. Keys are (from_state, to_state); value is a short
#: label used in audit logs. Any pair not in this table is rejected by
#: `transition()`.
_TRANSITIONS: Dict[Tuple[KanbanState, KanbanState], str] = {
    (KanbanState.SUGGESTED, KanbanState.BACKLOG): "accept",
    (KanbanState.SUGGESTED, KanbanState.FAILED): "reject",
    (KanbanState.BACKLOG, KanbanState.SCHEDULED): "schedule",
    (KanbanState.BACKLOG, KanbanState.IN_PROGRESS): "dispatch",
    (KanbanState.BACKLOG, KanbanState.FAILED): "cancel",
    (KanbanState.SCHEDULED, KanbanState.IN_PROGRESS): "dispatch",
    (KanbanState.SCHEDULED, KanbanState.BACKLOG): "unschedule",
    (KanbanState.SCHEDULED, KanbanState.FAILED): "cancel",
    (KanbanState.IN_PROGRESS, KanbanState.PR_OPENED): "pr_opened",
    # NOTE (done-hardening): this in_progress→done edge lets a caller reach 'done'
    # without passing through PR_OPENED/merge. The runner drives completion through
    # reflexes/kanban.py::_move_task, which now enforces a git/origin merge-verify
    # gate (a task can only reach 'done' when its work is actually on origin/main),
    # so this edge must only ever be taken POST-verify. Left in place because other
    # callers depend on it; the authoritative guarantee lives in _move_task.
    (KanbanState.IN_PROGRESS, KanbanState.DONE): "complete",
    (KanbanState.IN_PROGRESS, KanbanState.BACKLOG): "retry",
    (KanbanState.IN_PROGRESS, KanbanState.TOKEN_EXHAUSTED): "pause",
    (KanbanState.IN_PROGRESS, KanbanState.FAILED): "fail",
    (KanbanState.PR_OPENED, KanbanState.DONE): "merged",
    (KanbanState.PR_OPENED, KanbanState.CI_FAILED): "ci_failed",
    (KanbanState.PR_OPENED, KanbanState.MERGE_CONFLICT): "merge_conflict",
    (KanbanState.PR_OPENED, KanbanState.CHANGES_REQUESTED): "changes_requested",
    (KanbanState.PR_OPENED, KanbanState.FAILED): "fail",
    (KanbanState.CI_FAILED, KanbanState.IN_PROGRESS): "resume_from_ci",
    (KanbanState.CI_FAILED, KanbanState.FAILED): "fail",
    (KanbanState.MERGE_CONFLICT, KanbanState.IN_PROGRESS): "resume_from_conflict",
    (KanbanState.MERGE_CONFLICT, KanbanState.FAILED): "fail",
    (KanbanState.CHANGES_REQUESTED, KanbanState.IN_PROGRESS): "resume_from_review",
    (KanbanState.CHANGES_REQUESTED, KanbanState.FAILED): "fail",
    (KanbanState.TOKEN_EXHAUSTED, KanbanState.IN_PROGRESS): "resume_after_wait",
    (KanbanState.TOKEN_EXHAUSTED, KanbanState.BACKLOG): "give_up",
    (KanbanState.TOKEN_EXHAUSTED, KanbanState.FAILED): "fail",
    # Decomposed parents are closed by the auto-close sweep once all subtasks finish
    (KanbanState.DECOMPOSED, KanbanState.DONE): "auto_complete",
}


#: When persisting to the existing DB schema, map extended states down.
# TODO(kanban-hardening #4): PR_OPENED / CI_FAILED / MERGE_CONFLICT /
# CHANGES_REQUESTED all collapse to 'in_progress' below, and FAILED collapses to
# 'token_exhausted', because the PG kanban_tasks_status_check constraint only
# permits {backlog, scheduled, in_progress, done, token_exhausted, suggested,
# decomposed, validating, needs_decomposition}. Consequence: the board literally
# cannot distinguish "PR open, awaiting merge" from "still coding," nor "failed"
# from "token-exhausted" — it hides the true lifecycle. The follow-up is a coupled
# change: (a) a migration expanding the CHECK constraint to add pr_opened, merged,
# ci_failed, changes_requested, failed, blocked; (b) mapping each extended state to
# itself here; (c) adding the matching board columns in dashboard/templates/
# kanban.html. Deferred as a standalone PR because it touches schema + UI together.
_STATE_TO_DB_STATUS: Dict[KanbanState, str] = {
    KanbanState.SUGGESTED: "suggested",
    KanbanState.BACKLOG: "backlog",
    KanbanState.SCHEDULED: "scheduled",
    KanbanState.IN_PROGRESS: "in_progress",
    # Lifecycle states persist as themselves (migration 260 widened the
    # kanban_tasks.status CHECK). Previously these collapsed to in_progress /
    # token_exhausted, which hid "PR open, awaiting merge" from the board and let
    # genuine failures masquerade as resumable pauses. None of these are in the
    # dispatcher pickup set (backlog/scheduled/in_progress), so making them
    # distinct does not cause re-dispatch — it stops pr_opened tasks from being
    # counted as active work and stops failed tasks from auto-resuming.
    KanbanState.PR_OPENED: "pr_opened",
    KanbanState.CI_FAILED: "ci_failed",
    KanbanState.MERGE_CONFLICT: "merge_conflict",
    KanbanState.CHANGES_REQUESTED: "changes_requested",
    KanbanState.TOKEN_EXHAUSTED: "token_exhausted",
    KanbanState.DECOMPOSED: "decomposed",
    KanbanState.DONE: "done",
    KanbanState.FAILED: "failed",
}


class InvalidTransition(Exception):
    """Raised when a transition is not in the transition table."""


class ResumeCycleExceeded(InvalidTransition):
    """Raised when a task has already used MAX_RESUME_CYCLES attempts."""


@dataclass
class TransitionResult:
    task_id: str
    from_state: KanbanState
    to_state: KanbanState
    db_status: str
    reason: str
    label: str
    actor: str
    timestamp: str
    resume_cycle: int = 0
    audit_written: bool = False
    applied: bool = True  # False for no-op (same state)
    cot_trace_id: str = ""
    extra: Dict = field(default_factory=dict)


def is_terminal(state: KanbanState) -> bool:
    return state in TERMINAL_STATES


def can_transition(from_state: KanbanState, to_state: KanbanState) -> bool:
    """Return True if the transition is allowed."""
    if from_state == to_state:
        return True  # idempotent
    return (from_state, to_state) in _TRANSITIONS


def valid_next_states(from_state: KanbanState) -> List[KanbanState]:
    """Return all states reachable from `from_state` in one step."""
    return [to for (src, to) in _TRANSITIONS if src == from_state]


def db_status_for(state: KanbanState) -> str:
    return _STATE_TO_DB_STATUS[state]


# ────────────────────────────────────────────────────────────────────────────
# Core transition
# ────────────────────────────────────────────────────────────────────────────


def _ensure_kanban_cot_enabled() -> None:
    """Defensively add cot_enabled column to kanban_tasks if missing."""
    try:
        from tools.db.storage import get_connection, column_exists
        conn = get_connection()
        # Backend-aware column probe — works on PG + SQLite without translate_sql.
        if not column_exists(conn, "kanban_tasks", "cot_enabled"):
            conn.execute(
                "ALTER TABLE kanban_tasks ADD COLUMN cot_enabled INTEGER NOT NULL DEFAULT 0"
            )
            conn.commit()
        conn.close()
    except Exception:
        pass


def transition(
    task_id: str,
    from_state: KanbanState,
    to_state: KanbanState,
    *,
    reason: str = "",
    actor: str = "auto_remediator",
    resume_cycle: int = 0,
    db_exec=None,
    audit_exec=None,
    cot_trace_id: str = "",
) -> TransitionResult:
    """Validate + (optionally) persist a state transition.

    Parameters
    ----------
    task_id
        The kanban_tasks.id value (text PK).
    from_state, to_state
        Enum members representing current and next state.
    reason
        Free-text reason for the transition, written to the audit row.
    actor
        Who triggered the transition (tool name / agent id / user).
    resume_cycle
        Incremented count of times this task has been resumed. Enforced
        against MAX_RESUME_CYCLES for resumable transitions.
    db_exec
        Optional callable `(sql, params)` that executes an UPDATE. When
        None, the DB write is skipped and `applied` is still True for
        the in-memory state change.
    audit_exec
        Optional callable `(sql, params)` that executes an INSERT into
        audit_trail. Set to None to skip audit writes (useful in tests).
    """
    if not can_transition(from_state, to_state):
        raise InvalidTransition(
            f"Illegal transition {from_state.value} → {to_state.value} "
            f"for task {task_id}"
        )

    # Idempotent no-op
    if from_state == to_state:
        return TransitionResult(
            task_id=task_id,
            from_state=from_state,
            to_state=to_state,
            db_status=db_status_for(to_state),
            reason="no-op (already in target state)",
            label="noop",
            actor=actor,
            timestamp=datetime.now(timezone.utc).isoformat(),
            resume_cycle=resume_cycle,
            applied=False,
            cot_trace_id=cot_trace_id,
        )

    label = _TRANSITIONS[(from_state, to_state)]

    # Resume-cycle cap: if we're moving from a resumable state back into
    # IN_PROGRESS, enforce MAX_RESUME_CYCLES.
    if from_state in RESUMABLE_STATES and to_state == KanbanState.IN_PROGRESS:
        if resume_cycle >= MAX_RESUME_CYCLES:
            raise ResumeCycleExceeded(
                f"Task {task_id} exceeded MAX_RESUME_CYCLES "
                f"({MAX_RESUME_CYCLES}); transition "
                f"{from_state.value} → {to_state.value} rejected"
            )

    db_status = db_status_for(to_state)
    now = datetime.now(timezone.utc).isoformat()

    # Persist (optional)
    if db_exec is not None:
        try:
            db_exec(
                "UPDATE kanban_tasks SET status = %s, updated_at = %s "
                "WHERE id = %s",
                (db_status, now, task_id),
            )
        except Exception as exc:
            logger.warning(
                "state_machine: DB update failed for %s: %s", task_id, exc
            )

    audit_written = False
    if audit_exec is not None:
        try:
            audit_exec(
                "INSERT INTO audit_trail "
                "(created_at, event_type, actor, action, details) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    now,
                    "agent_task_completed"
                    if to_state == KanbanState.DONE
                    else "agent_task_submitted",
                    actor,
                    f"kanban.transition.{label}",
                    json.dumps({
                        "task_id": task_id,
                        "from_state": from_state.value,
                        "to_state": to_state.value,
                        "workflow_state": to_state.value,
                        "db_status": db_status,
                        "reason": reason,
                        "label": label,
                        "resume_cycle": resume_cycle,
                        "cot_trace_id": cot_trace_id,
                    }),
                ),
            )
            audit_written = True
        except Exception as exc:
            logger.warning(
                "state_machine: audit write failed for %s: %s", task_id, exc
            )

    # Best-effort write to kanban_status_transitions with structured CoT rationale
    try:
        from tools.db.storage import get_connection
        import secrets as _secrets
        conn = get_connection()
        kst_reason = reason
        if cot_trace_id:
            kst_reason = json.dumps({
                "cot_trace_id": cot_trace_id,
                "rationale": reason,
                "label": label,
            })
        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                "kst-" + _secrets.token_hex(6),
                task_id,
                from_state.value,
                to_state.value,
                actor,
                kst_reason,
                now,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("transition: best-effort INSERT into kanban_status_transitions failed (non-blocking): %s", _exc)

    # Wire Prometheus metrics — best-effort, never block on metric errors
    if to_state in TERMINAL_STATES:
        try:
            import tools.observability.metrics as _obs_m
            if _obs_m.kanban_tasks_total is not None:
                _obs_m.kanban_tasks_total.labels(status=db_status).inc()
        except Exception:
            pass

    return TransitionResult(
        task_id=task_id,
        from_state=from_state,
        to_state=to_state,
        db_status=db_status,
        reason=reason,
        label=label,
        actor=actor,
        timestamp=now,
        resume_cycle=resume_cycle,
        audit_written=audit_written,
        applied=True,
        cot_trace_id=cot_trace_id,
    )


def parse_state(value: Optional[str]) -> Optional[KanbanState]:
    """Best-effort enum parse. Returns None for empty/unknown input."""
    if not value:
        return None
    try:
        return KanbanState(value.lower())
    except (KeyError, ValueError):
        return None


# ────────────────────────────────────────────────────────────────────────────
# Decomposed-parent auto-close (km-autoclose)
# ────────────────────────────────────────────────────────────────────────────

# Pattern: a subtask ID looks like "{parent_id}-d{digits}", e.g. fd-floor-02-d3
_DECOMPOSED_SUBTASK_RE = re.compile(r"^(.+)-d\d+$")


def _infer_decomposed_parent_id(task_id: str) -> Optional[str]:
    """Extract the parent task ID from a decomposed subtask ID.

    Returns ``None`` if the ID doesn't match the ``{parent}-d{N}`` convention.

    Examples
    --------
    >>> _infer_decomposed_parent_id("fd-floor-02-d3")
    'fd-floor-02'
    >>> _infer_decomposed_parent_id("fd-floor-02")
    None
    """
    m = _DECOMPOSED_SUBTASK_RE.match(task_id)
    return m.group(1) if m else None


def auto_close_by_naming_convention(
    child_task_id: str,
    conn,
    actor: str = "auto_close_sweep",
) -> Optional[TransitionResult]:
    """Close a decomposed parent when every ``{parent_id}-d{N}`` subtask is done.

    This is Path 3 of the auto-close sweep — it handles the case where subtasks
    were created with the ``{parent}-d{N}`` ID convention but do NOT share a
    ``source_prediction_id`` with the parent and do NOT directly declare
    ``depends_on_task_id = parent_id``.

    The function is safe to call on any task completion — it is a no-op when
    the ID doesn't match the convention or the parent isn't in ``decomposed``
    status.

    Parameters
    ----------
    child_task_id:
        The just-completed subtask, e.g. ``fd-floor-02-d4``.
    conn:
        Open DB connection (``get_connection()`` wrapper). Caller commits.
    actor:
        Label written to the audit trail.

    Returns
    -------
    TransitionResult if the parent was closed, None otherwise.
    """
    parent_id = _infer_decomposed_parent_id(child_task_id)
    if not parent_id:
        return None

    parent_row = conn.execute(
        "SELECT status, title FROM kanban_tasks WHERE id = %s", (parent_id,)
    ).fetchone()
    if not parent_row:
        return None

    if hasattr(parent_row, "keys"):
        _pr = dict(parent_row)
        parent_status = _pr["status"]
        parent_title = _pr.get("title")
    else:
        parent_status = parent_row[0]
        parent_title = parent_row[1] if len(parent_row) > 1 else None

    # Manual-mode gate sentinels are held in_progress FOREVER by design.
    # Automation must never release them, even when their dependents finish.
    if is_manual_gate(parent_id, parent_title):
        logger.info(
            "auto_close_by_naming_convention: skipping manual-mode gate %s", parent_id
        )
        return None

    if parent_status == "done":
        return None
    if parent_status != "decomposed":
        # Only auto-close parents that were explicitly decomposed
        return None

    # Are ALL {parent_id}-d* subtasks done?
    pattern = f"{parent_id}-d%"
    total = conn.execute(
        "SELECT COUNT(*) FROM kanban_tasks WHERE id LIKE %s", (pattern,)
    ).fetchone()[0]
    if total == 0:
        return None

    undone = conn.execute(
        "SELECT COUNT(*) FROM kanban_tasks WHERE id LIKE %s AND status != 'done'",
        (pattern,),
    ).fetchone()[0]
    if undone > 0:
        return None

    def _db_exec(sql: str, params: tuple) -> None:
        conn.execute(sql, params)

    def _audit_exec(sql: str, params: tuple) -> None:
        conn.execute(sql, params)

    return transition(
        task_id=parent_id,
        from_state=KanbanState.DECOMPOSED,
        to_state=KanbanState.DONE,
        reason=f"auto-closed (naming-convention): all {total} subtask(s) done",
        actor=actor,
        db_exec=_db_exec,
        audit_exec=_audit_exec,
    )


def auto_close_parent_if_all_children_done(
    parent_id: str,
    conn,
    actor: str = "auto_close_sweep",
) -> Optional[TransitionResult]:
    """Close *parent_id* if every child task that depends on it is done.

    Parameters
    ----------
    parent_id:
        The kanban_tasks.id of the potential parent to close.
    conn:
        Open DB connection (get_connection() wrapper). Used for reads;
        the UPDATE is issued via the same conn so the caller commits.
    actor:
        Actor label written to the audit trail.

    Returns
    -------
    TransitionResult if the parent was closed, None if not eligible (already
    done, no children, or children not all done).
    """
    parent_row = conn.execute(
        "SELECT status, title FROM kanban_tasks WHERE id = %s", (parent_id,)
    ).fetchone()
    if not parent_row:
        return None

    _pr = dict(parent_row)
    parent_status = _pr["status"]
    parent_title = _pr.get("title")

    # Manual-mode gate sentinels (e.g. lpx-gate-00) are held in_progress
    # FOREVER by design; the FK backfill sweep must never auto-close them
    # once their dependents finish. This is the fix for the reported bug.
    if is_manual_gate(parent_id, parent_title):
        logger.info(
            "auto_close_parent_if_all_children_done: skipping manual-mode gate %s",
            parent_id,
        )
        return None

    if parent_status == "done":
        return None

    total_children = conn.execute(
        "SELECT COUNT(*) FROM kanban_tasks WHERE depends_on_task_id = %s",
        (parent_id,),
    ).fetchone()[0]
    if total_children == 0:
        return None

    undone = conn.execute(
        "SELECT COUNT(*) FROM kanban_tasks "
        "WHERE depends_on_task_id = %s AND status != 'done'",
        (parent_id,),
    ).fetchone()[0]
    if undone > 0:
        return None

    from_state = parse_state(parent_status) or KanbanState.IN_PROGRESS

    def _db_exec(sql: str, params: tuple) -> None:
        conn.execute(sql, params)

    def _audit_exec(sql: str, params: tuple) -> None:
        conn.execute(sql, params)

    return transition(
        task_id=parent_id,
        from_state=from_state,
        to_state=KanbanState.DONE,
        reason=f"auto-closed: all {total_children} child tasks done",
        actor=actor,
        db_exec=_db_exec,
        audit_exec=_audit_exec,
    )


def backfill_auto_close_parents(conn, actor: str = "startup_backfill") -> List[str]:
    """Sweep all non-done tasks that have dependents and close eligible ones.

    Runs three passes:
    1. FK-based: tasks that are parent via ``depends_on_task_id`` (existing path)
    2. Naming-convention: tasks in ``decomposed`` status whose ``{id}-d{N}``
       subtasks are all done (the new Path 3 — fixes the fd-floor-02 class of bug)

    Called once at dashboard startup and by the periodic kanban reflex sweep.
    Returns task IDs closed.
    """
    closed: List[str] = []

    # Pass 1 — FK-based parent close
    try:
        rows = conn.execute(
            "SELECT DISTINCT depends_on_task_id FROM kanban_tasks "
            "WHERE depends_on_task_id IS NOT NULL"
        ).fetchall()
        for row in rows:
            pid = dict(row).get("depends_on_task_id") if hasattr(row, "keys") else row[0]
            if not pid:
                continue
            result = auto_close_parent_if_all_children_done(pid, conn, actor=actor)
            if result and result.applied:
                closed.append(pid)
                logger.info("backfill_auto_close (fk): closed %s", pid)
    except Exception as exc:
        logger.warning("backfill_auto_close pass-1 failed: %s", exc)

    # Pass 2 — Naming-convention: decomposed parents with all {id}-d{N} subtasks done
    try:
        decomposed_rows = conn.execute(
            "SELECT id FROM kanban_tasks WHERE status = 'decomposed'"
        ).fetchall()
        for row in decomposed_rows:
            pid = dict(row)["id"] if hasattr(row, "keys") else row[0]
            if not pid or pid in closed:
                continue
            # Synthesise a child task ID to trigger the naming-convention check
            result = auto_close_by_naming_convention(f"{pid}-d1", conn, actor=actor)
            if result and result.applied:
                closed.append(pid)
                logger.info("backfill_auto_close (naming): closed %s", pid)
    except Exception as exc:
        logger.warning("backfill_auto_close pass-2 failed: %s", exc)

    if closed:
        conn.commit()

    return closed
