# CUI // SP-CTI
"""Is the status-transition log complete? (autonomy flag 1)

`kanban_status_transitions` is the primary evidence three detectors read —
`status_churn`, `landed_dispatch_survey`, `merge_stall` — and none of them can
tell a move that never happened from a move that happened and was never written
down.

THE TEST THAT MATTERS MOST is the refusal case. `_move_task` records a BLOCKED
transition with a `REFUSED_` pseudo-status: the row means the guard fired and
the task STAYED. Counting those as departures is the mistake that produced the
first draft of this measurement, and it is why the module matches a PREFIX
rather than the two names that happen to exist today.

The second is the trend. 189 (June) / 74 (July) / 10 (August) is mostly
historical debt on a falling curve; a bare 273 reads as an ongoing haemorrhage
and would be triaged as one.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import transition_log_gap as g  # noqa: E402


def _t(task_id, frm, to):
    return {"task_id": task_id, "from_status": frm, "to_status": to}


class _Conn:
    """Answers the module's three queries in the order it asks them."""

    def __init__(self, done_rows, known_rows, touch_rows):
        self._answers = [done_rows, known_rows, touch_rows]
        self._next = None

    def execute(self, sql, *_a):
        if "FROM kanban_tasks" in sql:
            self._next = self._answers[0]
        elif "DISTINCT task_id" in sql:
            self._next = self._answers[1]
        else:
            self._next = self._answers[2]
        return self

    def fetchall(self):
        return self._next

    def close(self):
        return None


# --------------------------------------------------------------------------- #
# 1. A REFUSED move is not a departure
# --------------------------------------------------------------------------- #
def test_a_refused_departure_leaves_the_task_done():
    """The guard FIRED and the task stayed. Counting it as a departure inflates
    the gap — the exact error the first draft of this made."""
    state = g.last_done_state([
        _t("t-1", "backlog", "done"),
        _t("t-1", "done", "REFUSED_uncomplete_backlog"),
    ])
    assert state["t-1"] == "in"


def test_the_other_refusal_status_is_also_not_a_departure():
    state = g.last_done_state([
        _t("t-1", "backlog", "done"),
        _t("t-1", "done", "REFUSED_done_unmerged"),
    ])
    assert state["t-1"] == "in"


def test_any_refused_prefix_counts_as_a_refusal():
    """A PREFIX, not the two names that exist today: a third guard added later
    must not silently start inflating the number."""
    state = g.last_done_state([
        _t("t-1", "backlog", "done"),
        _t("t-1", "done", "REFUSED_something_invented_later"),
    ])
    assert state["t-1"] == "in"


def test_a_real_departure_is_a_departure():
    state = g.last_done_state([
        _t("t-1", "backlog", "done"),
        _t("t-1", "done", "backlog"),
    ])
    assert state["t-1"] == "out"


def test_the_last_move_decides_not_the_first():
    """done -> backlog -> done is stably done. Counting arrivals alone would
    call a task done on the strength of a move it later reversed; counting
    departures alone would do the opposite."""
    state = g.last_done_state([
        _t("t-1", "backlog", "done"),
        _t("t-1", "done", "backlog"),
        _t("t-1", "backlog", "done"),
    ])
    assert state["t-1"] == "in"


# --------------------------------------------------------------------------- #
# 2. What counts as the gap
# --------------------------------------------------------------------------- #
def test_a_task_with_no_recorded_arrival_is_the_finding():
    report = g.measure(conn=_Conn(
        done_rows=[{"id": "t-1", "updated_at": "2026-08-20T10:00:00+00:00"}],
        known_rows=[{"task_id": "t-1"}],
        touch_rows=[_t("t-1", "done", "backlog")],
    ))
    assert report["unlogged"] == 1
    assert report["by_month"] == {"2026-08": 1}


def test_a_task_with_no_transitions_at_all_is_not_the_finding():
    """It has no history to contradict the board — seeded straight to done, or
    older than the log. Counting it would blame a writer for a row that was
    never expected."""
    report = g.measure(conn=_Conn(
        done_rows=[{"id": "t-1", "updated_at": "2026-08-20T10:00:00+00:00"}],
        known_rows=[],
        touch_rows=[],
    ))
    assert report["unlogged"] == 0


def test_a_properly_logged_task_is_not_the_finding():
    report = g.measure(conn=_Conn(
        done_rows=[{"id": "t-1", "updated_at": "2026-08-20T10:00:00+00:00"}],
        known_rows=[{"task_id": "t-1"}],
        touch_rows=[_t("t-1", "in_progress", "done")],
    ))
    assert report["unlogged"] == 0


def test_the_trend_is_reported_not_just_the_total():
    """189/74/10 is debt on a falling curve. A bare 273 reads as an ongoing
    haemorrhage and gets triaged as one."""
    report = g.measure(conn=_Conn(
        done_rows=[
            {"id": "a", "updated_at": "2026-06-01T00:00:00+00:00"},
            {"id": "b", "updated_at": "2026-07-01T00:00:00+00:00"},
            {"id": "c", "updated_at": "2026-08-01T00:00:00+00:00"},
        ],
        known_rows=[{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}],
        touch_rows=[_t("a", "done", "backlog"), _t("b", "done", "backlog"),
                    _t("c", "done", "backlog")],
    ))
    assert report["by_month"] == {"2026-06": 1, "2026-07": 1, "2026-08": 1}


# --------------------------------------------------------------------------- #
# 3. Unmeasurable is never "complete"
# --------------------------------------------------------------------------- #
def test_an_unreadable_log_is_unmeasurable():
    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError("no such table")

        def close(self):
            return None

    report = g.measure(conn=_Boom())
    assert report["state"] == g.UNMEASURABLE
    assert report["unlogged"] is None, "an unmeasurable report gave a count"


def test_a_board_with_nothing_done_is_unmeasurable_not_complete():
    """A fresh board has nothing to check. Reporting 0 unlogged would call it
    a complete log on the strength of an empty table."""
    report = g.measure(conn=_Conn(done_rows=[], known_rows=[], touch_rows=[]))
    assert report["state"] == g.UNMEASURABLE
    assert report["unlogged"] is None


def test_it_names_no_culprit():
    """39 sites UPDATE status outside `_move_task`, and the CLI's own recorder
    is best-effort — so a gap can come from a bypassing writer OR a recorder
    that tried and failed. Naming one would be a guess; the gap is the fact."""
    import inspect

    src = inspect.getsource(g.measure)
    for guess in ("cli.py", "orphan_sweep", "dashboard", "pr_watcher"):
        assert guess not in src, f"measure() attributes the gap to {guess}"
