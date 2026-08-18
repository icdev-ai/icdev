# CUI // SP-CTI
"""Seeding order must not gate dispatch as if it were a dependency.

The board carries TWO dependency systems:

  `kanban_task_deps` (junction)   the REAL graph — fan-in prerequisites.
                                  cef-di-03 -> cef-rsv-01, cef-rsv-02
  `depends_on_task_id` (scalar)   SEEDING ORDER, written when a batch is created.
                                  cef-di-03 -> cef-di-02

`_deps_satisfied` required BOTH, so a false linear chain overrode the true graph.

MEASURED 2026-08-18: cef-di-03/04/05/06 had every real prerequisite satisfied
(cef-rsv-01/02/03 all done) and were blocked solely by seeding order — five
independent migrations of five different modules onto one already-built API,
forced to run one at a time. Across a whole day exactly one task was ever in
flight: 16 backlog, 15 dependency-blocked, 1 manual gate, ZERO dispatchable.

THE RULE. Junction rows are a task's dependency DECLARATION; when they exist the
scalar is seeding order and must not add a second gate. When there are none, the
scalar IS the only declaration and is still honoured — which is exactly how a
task is held behind a manual gate.

Both halves matter, and the second is the one that keeps this safe: kpr-watch-01
has zero junction rows and a scalar pointing at the held gate kpr-gate-02. It
must keep holding.
"""
from __future__ import annotations

from tools.kanban import promote_backlog_to_scheduled as promote


class _Conn:
    """Minimal stand-in for the two queries `_deps_satisfied` makes."""

    def __init__(self, scalar_dep=None, junction=(), statuses=None):
        self.scalar_dep = scalar_dep
        self.junction = list(junction)
        self.statuses = statuses or {}
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        if "depends_on_task_id FROM kanban_tasks" in sql:
            return _R([{"depends_on_task_id": self.scalar_dep}])
        if "kanban_task_deps" in sql:
            return _R([{"depends_on_id": d} for d in self.junction])
        if "status FROM kanban_tasks" in sql:
            tid = params[0]
            if tid not in self.statuses:
                return _R([])
            return _R([{"status": self.statuses[tid]}])
        raise AssertionError(f"unexpected query: {sql}")


class _R:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


# ── the junction graph is the declaration when it exists ───────────────────
def test_a_satisfied_junction_graph_beats_an_unfinished_scalar():
    """The measured case: cef-di-03's real prerequisites are done, and only
    seeding order held it."""
    conn = _Conn(
        scalar_dep="cef-di-02",
        junction=["cef-rsv-01", "cef-rsv-02", "cef-rsv-03"],
        statuses={"cef-di-02": "in_progress", "cef-rsv-01": "done",
                  "cef-rsv-02": "done", "cef-rsv-03": "done"},
    )
    assert promote._deps_satisfied("cef-di-03", conn) is True


def test_an_unsatisfied_junction_dep_still_blocks():
    """Narrowed, not disarmed — a real prerequisite must still hold the task."""
    conn = _Conn(
        scalar_dep=None,
        junction=["cef-rsv-01", "cef-rsv-02"],
        statuses={"cef-rsv-01": "done", "cef-rsv-02": "in_progress"},
    )
    assert promote._deps_satisfied("cef-di-03", conn) is False


def test_a_junction_dep_that_does_not_exist_blocks():
    """A dangling prerequisite is not evidence that anything finished."""
    conn = _Conn(scalar_dep=None, junction=["ghost-01"], statuses={})
    assert promote._deps_satisfied("t-1", conn) is False


# ── the scalar remains the declaration when there is no junction graph ─────
def test_the_scalar_still_holds_a_task_with_no_junction_rows():
    """kpr-watch-01 -> kpr-gate-02: zero junction rows, and the manual gate MUST
    keep holding. This is the half that keeps the change safe."""
    conn = _Conn(
        scalar_dep="kpr-gate-02",
        junction=[],
        statuses={"kpr-gate-02": "in_progress"},
    )
    assert promote._deps_satisfied("kpr-watch-01", conn) is False


def test_a_satisfied_scalar_with_no_junction_rows_releases():
    conn = _Conn(scalar_dep="a-01", junction=[], statuses={"a-01": "done"})
    assert promote._deps_satisfied("a-02", conn) is True


def test_decomposed_counts_as_satisfied_on_both_paths():
    """A parent split into children is finished for gating purposes."""
    assert promote._deps_satisfied(
        "t", _Conn(scalar_dep="p", junction=[], statuses={"p": "decomposed"})) is True
    assert promote._deps_satisfied(
        "t", _Conn(scalar_dep=None, junction=["p"],
                   statuses={"p": "decomposed"})) is True


def test_a_task_with_no_dependencies_at_all_is_free():
    assert promote._deps_satisfied("t", _Conn(scalar_dep=None, junction=[])) is True


def test_a_dangling_scalar_still_blocks_when_there_is_no_junction_graph():
    """Unchanged: a scalar pointing at a task that does not exist is not a
    reason to dispatch."""
    conn = _Conn(scalar_dep="ghost", junction=[], statuses={})
    assert promote._deps_satisfied("t", conn) is False


# ── the scalar is not consulted at all once a junction graph exists ────────
def test_the_scalar_is_not_even_read_when_a_junction_graph_exists():
    """Structural, and it is the point of the change: if the scalar were still
    consulted 'just as a warning', the next refactor would quietly restore it as
    a gate. A satisfied junction graph ends the question."""
    conn = _Conn(
        scalar_dep="anything",
        junction=["p"],
        statuses={"p": "done"},
    )
    assert promote._deps_satisfied("t", conn) is True
    assert not any("depends_on_task_id FROM kanban_tasks" in q
                   for q in conn.queries), (
        "the scalar was queried even though a junction graph exists — it is "
        "seeding order, and reading it here is how it becomes a gate again")
