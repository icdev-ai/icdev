# CUI // SP-CTI
"""Re-queue a kanban task for a clean rebuild — without manufacturing a
phantom triage queue.

The problem this closes
-----------------------
There was no supported way to send a task back for a fresh build. Doing it by
hand meant an ad-hoc UPDATE: ``status`` -> backlog/scheduled and (if you
remembered) ``branch_name`` -> NULL. That UPDATE also bumps ``updated_at`` while
leaving ``last_failure_reason`` set — and that pair is precisely what
``tools/workflow/failure_triage.py::find_recent_failures`` selects on::

    last_failure_reason IS NOT NULL
      AND updated_at > cutoff
      AND status IN ('backlog','failed','scheduled','needs_decomposition')

So a *clean* re-queue looks identical to a *fresh failure*. Measured 2026-08-08:
re-queueing the nine ``sbx`` tasks (closing stale PRs so they would rebuild
against current main) pushed five of them into the autofix queue with nothing
wrong with them. ``ICDEV_AUTOFIX_ENABLED=true`` at the time; only the absence of
``ICDEV_AUTOFIX_AUTOMERGE`` kept generated patches off main. See PR #1379.

What a correct re-queue does
----------------------------
* clears ``last_failure_reason`` — the failure it describes is not the current
  attempt, so triage must not see it;
* clears ``branch_name`` — the point of a re-queue is to rebuild from current
  main, not to resume the branch whose PR was just closed;
* sets ``status`` (and stamps ``scheduled_at`` when the target is ``scheduled``,
  because ``_get_due_tasks`` requires a non-NULL stamp — without it the row is
  invisible to the dispatcher);
* **preserves ``failure_count`` and ``last_failure_at``.** This is deliberate.
  The count is the recovery guard's budget (``tools/kanban/recovery_guard.py``)
  and the task's real history; zeroing it would launder a task that has genuinely
  failed five times into a fresh one;
* records a ``kanban_status_transitions`` row, so the re-queue is attributable
  rather than an anonymous field edit.

Why this is a separate surface from ``--set-status``
----------------------------------------------------
``cli.py``'s ``VALID_STATUSES`` does not include ``pr_opened``, and the dashboard
move API rejects it too (``_VALID_STATUSES``, ``tools/dashboard/api/kanban.py``).
That state is pipeline-owned: ``tools/ci/pr_watcher.py::_set_task_status`` owns
the ``pr_opened`` edge. The consequence is that a task parked at ``pr_opened``
with a closed PR — exactly the sbx case — had no clean path back at all. This
function reads the *current* status from the row rather than validating it
against the CLI's write vocabulary, so a pipeline-owned state can be re-queued
without widening what ``--set-status`` is allowed to write.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Statuses a task may be re-queued *into*. Both are dispatcher pickup states;
#: anything else is a status change, not a re-queue, and belongs on
#: ``--set-status``.
REQUEUE_STATUSES = frozenset({"backlog", "scheduled"})

#: Columns cleared by a re-queue. ``branch_name`` is guarded at runtime because
#: it arrives via migration 114 (``114_kanban_vibe_tier2``) rather than the base
#: table DDL, so an un-migrated database will not have it.
_CLEARED_COLUMNS = ("last_failure_reason", "branch_name")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_transition(conn, task_id: str, from_status: Optional[str],
                       to_status: str, reason: str, actor: str) -> bool:
    """Append the re-queue to ``kanban_status_transitions``.

    Best-effort, matching every other writer of this table
    (``cli.py::_record_manual_transition``, ``pr_watcher.py::_set_task_status``,
    ``state_machine.py::transition``): audit bookkeeping must never break a
    status change. Unlike those, the outcome is *returned* rather than only
    logged, so a caller can report that the row did not land instead of
    reporting a clean re-queue that left no trace.
    """
    try:
        from tools.kanban.transition_reason import resolve_transition_reason

        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                "kst-" + secrets.token_hex(6),
                task_id,
                from_status,
                to_status,
                actor,
                resolve_transition_reason(
                    reason or "tools/kanban/requeue.py::requeue_task",
                    from_status=from_status, to_status=to_status, actor=actor,
                )[:200],
                _now(),
            ),
        )
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort; logged, never raised
        logger.warning(
            "requeue: best-effort INSERT into kanban_status_transitions failed "
            "(non-blocking) for %s: %s", task_id, exc,
        )
        return False


def requeue_task(
    task_id: str,
    *,
    status: str = "backlog",
    reason: str = "",
    actor: str = "manual",
    force: bool = False,
    get_conn: Optional[Callable[[], Any]] = None,
) -> Dict[str, Any]:
    """Re-queue one task for a clean rebuild.

    Parameters
    ----------
    task_id
        ``kanban_tasks.id``.
    status
        Target status; must be in :data:`REQUEUE_STATUSES`.
    reason
        Why this task is being re-queued. Recorded on the transition row.
    actor
        Who is re-queueing. Recorded on the transition row.
    force
        Re-queue a manual-mode gate sentinel anyway. Gate sentinels
        (``<prefix>-gate-00``) are held ``in_progress`` on purpose to keep
        ``promote_backlog_to_scheduled`` from dispatching a MANUAL-ONLY board;
        moving one to backlog/scheduled releases the whole board.
    get_conn
        Connection factory, for tests. Defaults to
        ``tools.db.storage.get_connection``.

    Returns
    -------
    dict with ``requeued`` (bool), ``from_status``, ``to_status``,
    ``failure_count`` (unchanged — echoed so a caller can see it was preserved),
    ``cleared`` (the columns actually set to NULL), ``transition_recorded``, and
    ``error``/``reason_refused`` when it did not happen. Never raises for an
    unknown task; a caller reporting its own state must get an explicit
    ``requeued: False`` rather than a traceback.
    """
    result: Dict[str, Any] = {
        "task_id": task_id,
        "requeued": False,
        "from_status": None,
        "to_status": status,
        "failure_count": None,
        "cleared": [],
        "transition_recorded": False,
        "error": None,
    }

    if status not in REQUEUE_STATUSES:
        result["error"] = (
            f"invalid re-queue target '{status}'. Valid: "
            f"{', '.join(sorted(REQUEUE_STATUSES))}"
        )
        return result

    if get_conn is None:
        from tools.db.storage import get_connection

        get_conn = get_connection

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if row is None:
            result["error"] = "not found"
            return result

        task = dict(row)
        result["from_status"] = task.get("status")
        # Echoed from the row read BEFORE the UPDATE; the UPDATE never names
        # this column, so the value below is what the row still carries.
        result["failure_count"] = task.get("failure_count")

        # A gate sentinel is held in_progress by design — see CLAUDE.md's
        # MANUAL-only rule. Releasing one lets the runner dispatch every task
        # behind it, which is never what "rebuild this task" means.
        try:
            from tools.kanban.gates import is_manual_gate

            if is_manual_gate(task_id, task.get("title")) and not force:
                result["error"] = (
                    f"{task_id} is a manual-mode gate sentinel — re-queueing it "
                    f"releases every task gated behind it. Pass force=True "
                    f"(--force) if that is genuinely what you want."
                )
                return result
        except Exception as exc:  # noqa: BLE001 - guard must not wedge a re-queue
            logger.debug("requeue: manual-gate probe skipped for %s: %s", task_id, exc)

        now = _now()
        sets = ["status = %s", "updated_at = %s"]
        vals: list = [status, now]
        # Only NULL columns the live row actually has: branch_name arrives via
        # migration 114, and naming a missing column would abort the statement
        # (and, on PostgreSQL, poison the surrounding transaction).
        for col in _CLEARED_COLUMNS:
            if col in task:
                sets.append(f"{col} = NULL")
                result["cleared"].append(col)
        if status == "scheduled":
            # _get_due_tasks requires `scheduled_at IS NOT NULL AND
            # scheduled_at <= now()`. COALESCE rather than overwrite: a task
            # already carrying a due time keeps it instead of being pushed back.
            sets.append("scheduled_at = COALESCE(scheduled_at, %s)")
            vals.append(now)
        vals.append(task_id)

        conn.execute(
            f"UPDATE kanban_tasks SET {', '.join(sets)} WHERE id = %s",  # nosec B608 — column names are module constants
            tuple(vals),
        )
        result["transition_recorded"] = _record_transition(
            conn, task_id, result["from_status"], status, reason, actor,
        )
        result["requeued"] = True

    logger.info(
        "requeue: %s %s -> %s (failure_count preserved at %s, cleared %s)",
        task_id, result["from_status"], status, result["failure_count"],
        ",".join(result["cleared"]) or "nothing",
    )
    return result
