# CUI // SP-CTI
"""kpr-idle-01 — the six idle states must be told apart, and never acted on.

``idle (no due tasks)`` is what the scheduler logged whether the board was
finished, paused, wedged, waiting on review, or waiting on a human decision.
Five of those need different actions and one needs none, so the value of this
module is entirely in the classification being RIGHT — a confident wrong reason
is worse than the bare line it replaces, because someone will act on it.

Each test therefore builds exactly one state and asserts the reason, including
the states that must NOT be reported (a finished board is not a problem).

The last group is the guardrail: the advisor is a read. If it ever moves a task
or opens a gate, a held gate stops being a control the moment anyone notices it
can be outwaited.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kanban import idle_advisor as A  # noqa: E402

_SCHEMA = """
CREATE TABLE kanban_tasks (
    id TEXT PRIMARY KEY,
    title TEXT,
    priority TEXT,
    status TEXT,
    depends_on_task_id TEXT,
    last_heartbeat_at TIMESTAMP,
    updated_at TIMESTAMP
);
CREATE TABLE kanban_task_deps (
    task_id TEXT,
    depends_on_id TEXT
);
"""


class _Conn:
    """sqlite with %s placeholders, matching what get_connection() returns."""

    def __init__(self):
        self._c = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
        self._c.row_factory = sqlite3.Row
        self._c.executescript(_SCHEMA)

    def execute(self, sql, params=()):
        return self._c.execute(sql.replace("%s", "?"), params)

    def close(self):
        self._c.close()


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def conn(monkeypatch):
    # No pause sentinel unless a test asks for one; the real one reads the
    # filesystem and would make these depend on the developer's checkout.
    monkeypatch.setattr(
        "tools.kanban.scheduler_control.manual_paused", lambda: False, raising=False
    )
    c = _Conn()
    yield c
    c.close()


def _task(conn, tid, *, status, title=None, priority="high", dep=None,
          heartbeat=None, updated=None):
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, priority, status, depends_on_task_id,"
        " last_heartbeat_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (tid, title if title is not None else f"task {tid}", priority, status, dep,
         heartbeat, updated or _now()),
    )


def _gate(conn, gid, *, updated=None):
    _task(conn, gid, status="in_progress",
          title="MANUAL-MODE GATE — held, do not dispatch",
          priority="critical", updated=updated or _now())


# --------------------------------------------------------------------------- #
# One state at a time
# --------------------------------------------------------------------------- #

def test_a_finished_board_is_not_a_problem(conn):
    _task(conn, "t-1", status="done")
    d = A.diagnose(conn)
    assert d["reason"] == A.DRAINED
    assert d["actionable"] is False, "nobody should be paged because work finished"


def test_a_pause_sentinel_outranks_everything(conn, monkeypatch):
    """A paused scheduler behind a held gate is a paused scheduler."""
    monkeypatch.setattr(
        "tools.kanban.scheduler_control.manual_paused", lambda: True, raising=False
    )
    _gate(conn, "g-00")
    _task(conn, "t-1", status="backlog", dep="g-00")
    assert A.diagnose(conn)["reason"] == A.PAUSED


def test_scheduled_but_undispatched_is_review_bound(conn):
    """The state that cost hours: slots free, work ready, every task has a PR."""
    _task(conn, "t-1", status="scheduled")
    d = A.diagnose(conn)
    assert d["reason"] == A.REVIEW_BOUND
    assert "merge or close" in d["detail"]


def test_backlog_behind_held_gates_is_decision_bound(conn):
    _gate(conn, "g-00")
    _task(conn, "t-1", status="backlog", dep="g-00")
    _task(conn, "t-2", status="backlog", dep="g-00")
    d = A.diagnose(conn)
    assert d["reason"] == A.DECISION_BOUND
    assert d["recommendation"]["release"] == "g-00"
    assert "Nothing is broken" in d["detail"]


def test_backlog_with_no_gate_is_chain_bound(conn):
    """Ordinary unmet dependencies are not a decision anyone has to make."""
    _task(conn, "pred", status="in_progress", title="real work, not a gate",
          heartbeat=_now())
    _task(conn, "t-1", status="backlog", dep="pred")
    assert A.diagnose(conn)["reason"] == A.CHAIN_BOUND


def test_every_slot_held_by_something_not_heartbeating_is_wedged(conn):
    old = _now() - timedelta(hours=9)
    _task(conn, "t-1", status="in_progress", title="real work",
          heartbeat=old, updated=old)
    d = A.diagnose(conn)
    assert d["reason"] == A.WEDGED
    assert d["stale"] == ["t-1"]


def test_a_gate_is_not_mistaken_for_wedged_work(conn):
    """Gates never heartbeat — that is their whole design, not a hang."""
    _gate(conn, "g-00", updated=_now() - timedelta(days=5))
    _task(conn, "t-1", status="backlog", dep="g-00")
    assert A.diagnose(conn)["reason"] == A.DECISION_BOUND


def test_live_work_still_beating_is_not_wedged(conn):
    _task(conn, "t-1", status="in_progress", title="real work", heartbeat=_now())
    assert A.diagnose(conn)["reason"] != A.WEDGED


# --------------------------------------------------------------------------- #
# The ranking
# --------------------------------------------------------------------------- #

def test_the_gate_releasing_more_ready_work_ranks_higher(conn):
    _gate(conn, "big-gate-00")
    _gate(conn, "small-gate-00")
    for i in range(5):
        _task(conn, f"b-{i}", status="backlog", dep="big-gate-00", priority="high")
    _task(conn, "s-0", status="backlog", dep="small-gate-00", priority="high")
    d = A.diagnose(conn)
    assert d["recommendation"]["release"] == "big-gate-00"
    assert d["recommendation"]["then"] == ["small-gate-00"]


def test_priority_outweighs_raw_count_when_close(conn):
    _gate(conn, "crit-gate-00")
    _gate(conn, "low-gate-00")
    _task(conn, "c-0", status="backlog", dep="crit-gate-00", priority="critical")
    _task(conn, "c-1", status="backlog", dep="crit-gate-00", priority="critical")
    for i in range(3):
        _task(conn, f"l-{i}", status="backlog", dep="low-gate-00", priority="low")
    assert A.diagnose(conn)["recommendation"]["release"] == "crit-gate-00"


def test_self_declared_blocked_tasks_are_penalised_and_surfaced(conn):
    """The OBS trap: releasing a gate onto work the board says cannot proceed."""
    _gate(conn, "trap-gate-00")
    _task(conn, "x-1", status="backlog", dep="trap-gate-00",
          title="BLOCKED — metric_snapshots is empty", priority="high")
    _task(conn, "x-2", status="backlog", dep="trap-gate-00",
          title="DECLINED — auto-create tasks from alerts", priority="high")
    cand = next(c for c in A.diagnose(conn)["candidates"]
                if c["gate_id"] == "trap-gate-00")
    assert len(cand["inert"]) == 2
    assert any("BLOCKED/DECLINED" in c for c in cand["caveats"])


def test_a_task_chained_behind_a_sibling_is_not_ready_on_release(conn):
    """Task count overstates parallelism when the tasks are serial."""
    _gate(conn, "g-00")
    _task(conn, "a", status="backlog", dep="g-00")
    _task(conn, "b", status="backlog", dep="g-00")
    conn.execute("INSERT INTO kanban_task_deps (task_id, depends_on_id) VALUES (%s,%s)",
                 ("b", "a"))
    cand = A.diagnose(conn)["candidates"][0]
    assert cand["open_tasks"] == 2
    assert cand["ready_on_release"] == 1
    assert any("start immediately" in c for c in cand["caveats"])


def test_the_recommendation_states_what_it_cannot_know(conn):
    """A ranking that hides its blind spots invites being trusted past them."""
    _gate(conn, "g-00")
    _task(conn, "t-1", status="backlog", dep="g-00")
    rec = A.diagnose(conn)["recommendation"]
    assert rec["blind_spots"], "the advisor must publish its own limits"
    assert any("business" in b for b in rec["blind_spots"])
    assert rec["command"].startswith("python tools/kanban/cli.py --set-status")


# --------------------------------------------------------------------------- #
# The guardrail
# --------------------------------------------------------------------------- #

def test_diagnosing_never_changes_the_board(conn):
    """The whole design rests on this: it recommends, it does not act.

    An advisor that opens a gate to keep the pipeline busy has substituted
    throughput for intent, and the gate stops being a control the moment
    anyone learns it can be outwaited.
    """
    _gate(conn, "g-00")
    _task(conn, "t-1", status="backlog", dep="g-00")
    _task(conn, "t-2", status="scheduled")
    before = sorted(tuple(r) for r in conn.execute(
        "SELECT id, status, depends_on_task_id FROM kanban_tasks").fetchall())

    A.diagnose(conn)
    A.diagnose(conn)

    after = sorted(tuple(r) for r in conn.execute(
        "SELECT id, status, depends_on_task_id FROM kanban_tasks").fetchall())
    assert before == after


def test_the_summary_line_names_the_reason(conn):
    _gate(conn, "g-00")
    _task(conn, "t-1", status="backlog", dep="g-00")
    line = A.summary_line(A.diagnose(conn))
    assert line.startswith("idle [decision_bound]")
    assert "recommended: release g-00" in line


def test_the_scheduler_falls_back_rather_than_dying(monkeypatch):
    """A heartbeat killed by its own diagnostic is worse than a mute one."""
    import tools.genesis.kanban_scheduler as S

    monkeypatch.setattr(S, "_idle_state", (None, None), raising=False)
    monkeypatch.setattr(
        A, "diagnose", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))
    )
    assert S._idle_reason(1) == "idle (no due tasks)"
