"""The E2E runner reflex seeds ONE task, not one per cycle.

THE DEFECT, MEASURED ON THE LIVE BOARD 2026-08-31. `_pending_run_exists`
enumerated the live states as `('backlog', 'in_progress')` and omitted
`scheduled` -- which is where a task spends most of its life, because the
scheduler promotes it out of backlog within minutes. The guard then saw nothing
pending and the next cycle seeded another: `task-e2e-393aebca` (08-30) sat in
`scheduled` while `task-e2e-324a69a9` was seeded on 08-31 with a byte-identical
description. Twenty-five earlier runs never doubled only because each happened
to complete before the next cycle came round.

WHAT IS PINNED IS THE INVERSION, not the list. Asking "is the status one of the
live ones I can name" fails the moment a status is added; asking "is it not
terminal" cannot. `validating` is already on this board and no version of the
enumeration mentioned it.
"""
from __future__ import annotations

import re

import pytest

from tools.genesis.reflexes.e2e_runner import _pending_run_exists
from tools.kanban.lane_conflicts import TERMINAL_STATUSES


class Conn:
    """A connection that answers the guard's query by READING it.

    IT INTERPRETS THE SQL, rather than assuming a shape. The first version of
    this fake derived the answer from the query's PARAMETERS -- so against the
    old predicate, which inlines its statuses and passes none, every status
    read as pending and the `scheduled` case passed for the wrong reason. A
    fake that only works against the implementation it was written beside
    proves that implementation is unchanged, which was never the question.

    So it parses whether the filter is `IN` or `NOT IN` and which statuses it
    names, from parameters OR from inlined literals, and applies it the way a
    database would.
    """

    def __init__(self, statuses: list[str]):
        self.statuses = statuses
        self.sql = ""
        self.params: tuple = ()

    def execute(self, sql, params=()):
        self.sql, self.params = sql, tuple(params)
        named = set(self.params) or set(re.findall(r"'([a-z_]+)'", sql))
        negated = "NOT IN" in sql
        keeps = ((lambda st: st not in named) if negated
                 else (lambda st: st in named))
        self.rows = [s for s in self.statuses if keeps(s)]
        return self

    def fetchone(self):
        return {"id": "task-e2e-x"} if self.rows else None


# --------------------------------------------------------------------------- #
# the states that mean "still open"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", ["backlog", "in_progress", "scheduled",
                                    "validating", "pr_opened", "review"])
def test_a_task_in_any_NON_TERMINAL_state_counts_as_pending(status):
    """`scheduled` is the one that was missing and it is the one that matters:
    the scheduler promotes a task there within minutes of seeding."""
    assert _pending_run_exists(Conn([status])) is True


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_a_task_in_a_TERMINAL_state_does_not_block_the_next_run(status):
    """Otherwise the reflex would seed once and never again."""
    assert _pending_run_exists(Conn([status])) is False


def test_an_empty_board_has_nothing_pending():
    assert _pending_run_exists(Conn([])) is False


def test_a_done_task_beside_an_open_one_still_reads_pending():
    assert _pending_run_exists(Conn(["done", "scheduled"])) is True


# --------------------------------------------------------------------------- #
# the inversion itself
# --------------------------------------------------------------------------- #
def test_the_query_EXCLUDES_terminal_rather_than_listing_live_states():
    """Asking "is it one of the live ones I can name" fails the moment a
    status is added; asking "is it not terminal" cannot. This reads the SQL the
    guard actually issued, so a future edit back to an enumeration fails here.
    """
    conn = Conn(["scheduled"])
    _pending_run_exists(conn)
    assert "NOT IN" in conn.sql
    assert set(conn.params) == set(TERMINAL_STATUSES)
    # And the live states are NOT enumerated in the SQL.
    for live in ("backlog", "in_progress", "scheduled", "validating"):
        assert f"'{live}'" not in conn.sql


def test_an_UNKNOWN_status_reads_as_pending_which_is_the_safe_direction():
    """The cost of a false pending is one skipped cycle; the cost of a false
    absence is a duplicate a human has to reconcile."""
    assert _pending_run_exists(Conn(["some_status_nobody_has_added_yet"])) is True
