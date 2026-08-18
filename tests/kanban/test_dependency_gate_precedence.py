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
    """Minimal stand-in for the queries `_deps_satisfied` makes.

    Two shapes read the scalar, because the rule now lives in
    `tools.kanban.deps` and that module reads the scalar and its parent's TITLE
    in one LEFT JOIN — it has to know whether the scalar points at a manual gate
    (see `test_a_scalar_pointing_at_a_manual_gate_holds_...` below). Both are
    served so this stub pins BEHAVIOUR rather than one module's query text.
    """

    def __init__(self, scalar_dep=None, junction=(), statuses=None, titles=None):
        self.scalar_dep = scalar_dep
        self.junction = list(junction)
        self.statuses = statuses or {}
        self.titles = titles or {}
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        if "LEFT JOIN kanban_tasks p" in sql:
            return _R([{"dep_id": self.scalar_dep,
                        "dep_title": self.titles.get(self.scalar_dep)}])
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

    def status_reads(self):
        """Ids whose STATUS was consulted — i.e. what actually gated."""
        return [p[0] for sql, p in self.queries
                if "status FROM kanban_tasks" in sql and p]


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


# ── a superseded scalar cannot gate ────────────────────────────────────────
def test_a_superseded_scalar_never_gates_when_a_junction_graph_exists():
    """Structural, and it is the point of the change: if the scalar were still
    allowed to gate 'just as a warning', the next refactor would quietly restore
    it. A satisfied junction graph ends the question.

    Asserted on the STATUS read rather than on the scalar read, because
    `tools.kanban.deps` must look the scalar up to see whether it points at a
    manual gate. That lookup reads the parent's id and title and NOTHING about
    whether it is finished — so if the scalar's status is never consulted, the
    scalar cannot have gated. Gating is the thing this test exists to prevent.
    """
    conn = _Conn(
        scalar_dep="anything",
        junction=["p"],
        statuses={"p": "done", "anything": "in_progress"},
    )
    assert promote._deps_satisfied("t", conn) is True
    assert "anything" not in conn.status_reads(), (
        "the scalar's status was consulted even though a junction graph "
        "exists — it is seeding order, and letting it decide here is how it "
        "becomes a gate again")


def test_a_scalar_pointing_at_a_manual_gate_holds_even_with_a_junction_graph():
    """The one carve-out, and the reason the scalar is still LOOKED UP.

    A gate is a HOLD — a human decided this card does not ship unattended — not
    seeding order, so a junction graph must not release it. Measured 2026-08-18:
    `kpr-stale-02` carries junction rows AND a scalar pointing at a held gate,
    so without this the guarantee would be accidental rather than stated.
    """
    conn = _Conn(
        scalar_dep="kpr-gate-02",
        junction=["p"],
        statuses={"p": "done", "kpr-gate-02": "in_progress"},
    )
    assert promote._deps_satisfied("kpr-stale-02", conn) is False


def test_a_satisfied_gate_scalar_releases_alongside_the_junction_graph():
    """Narrowed, not inverted: the carve-out HOLDS more, it never holds forever.
    A gate a human has released stops holding."""
    conn = _Conn(
        scalar_dep="kpr-gate-02",
        junction=["p"],
        statuses={"p": "done", "kpr-gate-02": "done"},
    )
    assert promote._deps_satisfied("kpr-stale-02", conn) is True
