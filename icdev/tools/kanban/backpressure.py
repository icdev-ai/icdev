# CUI // SP-CTI
"""Flow control for the autonomous build loop (clx-flow-01).

The dispatcher caps how many tasks may *execute* at once
(``KANBAN_MAX_IN_PROGRESS``, counted by ``_count_in_progress()`` over
``status = 'in_progress'``). It does not cap how much finished-but-unreviewed
output may accumulate. A task that finishes building moves to ``pr_opened`` and
waits for its PR to merge — at which point it stops being counted, a slot frees,
and the loop dispatches more work. Nothing bounds the number of open PRs.

That is deliberate as far as it goes: migration 260 gave ``pr_opened`` its own
status precisely so a task awaiting merge would stop occupying a *coding* slot.
This module adds the other half of the argument. Unreviewed output is still work
in flight: it can conflict with what is dispatched next, it duplicates effort
when two branches touch the same code, and it defers the human review that is
supposed to be the loop's feedback signal. A control loop that actuates faster
than it is measured is running open-loop.

So: count what is awaiting review, and stop dispatching when too much has piled
up. One open output per loop is the ideal; the default here is more permissive.

**Off by default.** Enabling this reduces autonomous throughput, and that is a
judgement call about how the board is run, not a bug fix. Set
``KANBAN_BACKPRESSURE_ENABLED=1`` to turn it on.

Usage::

    from tools.kanban.backpressure import apply_backpressure

    slots = apply_backpressure(available_slots)
    if slots <= 0:
        return []   # too much unreviewed output — let a human catch up
"""
from __future__ import annotations

import os
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.kanban.backpressure")

_TRUTHY = {"1", "true", "yes", "on"}

#: Statuses that mean "the loop produced something a human has not yet accepted".
#: ``validating`` is included: a task parked there has produced output and is
#: waiting on a verdict, which is the same backpressure signal.
UNREVIEWED_STATUSES = (
    "pr_opened",
    "ci_failed",
    "changes_requested",
    "merge_conflict",
    "validating",
)

#: Default ceiling on unreviewed output. Kyle's control-loop talk argues for
#: exactly one open PR per loop; 3 matches the existing MAX_IN_PROGRESS default
#: so switching this on does not immediately halt a board that is mid-flight.
DEFAULT_MAX_UNREVIEWED = 3

ENV_ENABLED = "KANBAN_BACKPRESSURE_ENABLED"
ENV_MAX = "KANBAN_MAX_UNREVIEWED"


def is_enabled() -> bool:
    """True when flow control is switched on. Off unless explicitly enabled."""
    return os.environ.get(ENV_ENABLED, "").strip().lower() in _TRUTHY


def max_unreviewed() -> int:
    """Ceiling on unreviewed output before dispatch stops."""
    raw = os.environ.get(ENV_MAX, "").strip()
    if not raw:
        return DEFAULT_MAX_UNREVIEWED
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "backpressure: %s=%r is not an integer; using default %d",
            ENV_MAX, raw, DEFAULT_MAX_UNREVIEWED,
        )
        return DEFAULT_MAX_UNREVIEWED
    return max(0, value)


def count_unreviewed(conn: Optional[Any] = None) -> int:
    """How many tasks are sitting in a state awaiting human review.

    Never raises: flow control must not be able to wedge the dispatcher. On any
    error this reports 0, which means "no backpressure" — the loop keeps its
    existing behaviour rather than stalling because a query failed.
    """
    owned = conn is None
    try:
        if conn is None:
            from tools.db.storage import get_connection

            conn = get_connection()
        placeholders = ", ".join(["%s"] * len(UNREVIEWED_STATUSES))
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM kanban_tasks WHERE status IN ({placeholders})",
            tuple(UNREVIEWED_STATUSES),
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(dict(row).get("cnt", 0))
        except (TypeError, ValueError):
            return int(row[0])
    except Exception as exc:  # noqa: BLE001 — fail open, never wedge dispatch
        logger.warning("backpressure: could not count unreviewed output: %s", exc)
        return 0
    finally:
        if owned and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def apply_backpressure(available_slots: int, conn: Optional[Any] = None) -> int:
    """Reduce ``available_slots`` by the amount of unreviewed output.

    Returns ``available_slots`` unchanged when flow control is disabled, so the
    call site is safe to add before anyone opts in.
    """
    if not is_enabled():
        return available_slots

    unreviewed = count_unreviewed(conn=conn)
    ceiling = max_unreviewed()
    headroom = ceiling - unreviewed

    if headroom <= 0:
        logger.info(
            "backpressure: holding dispatch — %d unreviewed output(s) at or above "
            "the ceiling of %d. Review or merge the open work before the loop "
            "produces more.",
            unreviewed, ceiling,
        )
        return 0

    if headroom < available_slots:
        logger.info(
            "backpressure: narrowing dispatch from %d to %d slot(s) — %d/%d "
            "unreviewed output(s) already in flight",
            available_slots, headroom, unreviewed, ceiling,
        )
        return headroom
    return available_slots


def status(conn: Optional[Any] = None) -> dict[str, Any]:
    """Report the current flow-control picture, for CLI/dashboard use."""
    unreviewed = count_unreviewed(conn=conn)
    ceiling = max_unreviewed()
    return {
        "enabled": is_enabled(),
        "unreviewed": unreviewed,
        "ceiling": ceiling,
        "headroom": max(0, ceiling - unreviewed),
        "holding": is_enabled() and unreviewed >= ceiling,
        "statuses": list(UNREVIEWED_STATUSES),
    }
