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
    description TEXT,
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
          heartbeat=None, updated=None, description=None):
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, description, priority, status,"
        " depends_on_task_id, last_heartbeat_at, updated_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (tid, title if title is not None else f"task {tid}", description, priority,
         status, dep, heartbeat, updated or _now()),
    )


def _gate(conn, gid, *, updated=None, description="RISK: needs a human in the loop."):
    """A gate that STATES a risk by default.

    The ranking tests below are about volume and priority; leaving them
    unjustified would make every one of them pass for the wrong reason, since
    an unjustified gate outranks everything (kpr-idle-02).
    """
    _task(conn, gid, status="in_progress",
          title="MANUAL-MODE GATE — held, do not dispatch",
          priority="critical", updated=updated or _now(), description=description)


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


def test_review_bound_also_names_the_gated_backlog_behind_it(conn):
    """Both facts, one line — the omission cost a second diagnosis.

    review_bound wins precedence whenever ANY task is scheduled, so on 2026-08-09
    the scheduler reported three withheld tasks for hours and never said the 37
    backlog tasks behind them were gate-held. Merging the three PRs bought three
    dispatches and idled again, because the real ceiling was never mentioned.
    """
    _task(conn, "t-1", status="scheduled")
    _gate(conn, "g-00")
    _task(conn, "t-2", status="backlog", dep="g-00")
    _task(conn, "t-3", status="backlog", dep="g-00")
    d = A.diagnose(conn)
    assert d["reason"] == A.REVIEW_BOUND, "precedence is unchanged"
    assert "merge or close" in d["detail"]
    assert "2 backlog task(s) sit behind 1 held gate(s)" in d["detail"]
    assert "g-00" in d["detail"], "name the gate — the operator has to pick one"


def test_review_bound_says_nothing_extra_when_no_gate_is_held(conn):
    """The clause is evidence, not decoration: no gate, no sentence."""
    _task(conn, "t-1", status="scheduled")
    _task(conn, "t-2", status="backlog")
    d = A.diagnose(conn)
    assert d["reason"] == A.REVIEW_BOUND
    assert "held gate" not in d["detail"]


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


# --------------------------------------------------------------------------- #
# kpr-idle-02 — a gate is only justified by a stated risk
# --------------------------------------------------------------------------- #

from tools.kanban.gates import declared_risk  # noqa: E402


def test_an_explicit_risk_line_is_read():
    risk, confidence = declared_risk(
        "Sentinel.\nRISK: these tasks classify CUI and need a human in the loop.\n"
    )
    assert confidence == "explicit"
    assert "classify CUI" in risk


def test_risk_shaped_prose_is_accepted_but_marked_implicit():
    """Existing gates are not all condemned overnight — but the gap stays visible."""
    risk, confidence = declared_risk(
        "Held in_progress forever — these are design decisions, not agent work."
    )
    assert confidence == "implicit"
    assert risk


def test_a_consequence_of_dispatching_counts_as_a_stated_risk():
    """The real agov-gate-00 text, which the advisor once told a human to release.

    Its 19 tasks edit .claude/hooks/pre_tool_use.py and approval_gate.py while
    pr_watcher auto-merges anything CI-green. None of the original phrases
    matched, so it scored risk=None, took the +10000 unjustified penalty, and
    came out top of the release ranking — a guard confidently recommending the
    one release its own subject forbids.
    """
    risk, confidence = declared_risk(
        "Holding gate for the AGOV card. AGOV touches .claude/hooks/pre_tool_use.py "
        "and tools/agent_runtime/approval_gate.py, and tools/ci/pr_watcher.py "
        "auto-merges any CI-green kanban/* branch, so autonomous dispatch of this "
        "card is not acceptable."
    )
    assert confidence == "implicit", "prose is recognised, but still not the marker"
    assert risk


def test_widening_the_phrases_did_not_swallow_procedure():
    """The new phrases describe a CONSEQUENCE; these describe an ACTION.

    This is the guard on the guard: every phrase added to catch agov must leave
    the procedure/risk line exactly where it was, or the policy collapses into
    "any text at all justifies a gate".
    """
    for procedure in (
        "Do not move this to done without an explicit decision.",
        "Re-hold after /start.",
        "This task is created in_progress and is never worked.",
        "Human sessions pick tasks up explicitly.",
    ):
        assert declared_risk(procedure) == (None, "none"), procedure


def test_procedure_is_not_risk():
    """The distinction the whole policy rests on.

    "Do not move this without a decision" and "re-hold after /start" tell a
    reader what to DO. Neither says what goes wrong if the runner builds the
    card, so neither can be reviewed — there is nothing to weigh.
    """
    risk, confidence = declared_risk(
        "Manual hold for the card. Do NOT move this to done without an explicit "
        "decision to let the runner build it autonomously.\n"
        "Hazard: /start's reset releases this gate. Re-hold it after any /start."
    )
    assert confidence == "none"
    assert risk is None


def test_an_empty_description_states_no_risk():
    assert declared_risk("") == (None, "none")
    assert declared_risk(None) == (None, "none")


def test_an_unjustified_gate_outranks_a_justified_one_holding_far_more(conn):
    """Justification dominates volume — the policy, expressed as an assertion."""
    _task(conn, "silent-gate-00", status="in_progress",
          title="MANUAL-MODE GATE — silent", priority="critical")
    conn.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s",
                 ("Manual hold. Do not move without a decision.", "silent-gate-00"))
    _task(conn, "stated-gate-00", status="in_progress",
          title="MANUAL-MODE GATE — stated", priority="critical")
    conn.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s",
                 ("RISK: targets a private repo the runner cannot clone.",
                  "stated-gate-00"))

    _task(conn, "s-0", status="backlog", dep="silent-gate-00", priority="low")
    for i in range(12):
        _task(conn, f"j-{i}", status="backlog", dep="stated-gate-00", priority="critical")

    d = A.diagnose(conn)
    assert d["recommendation"]["release"] == "silent-gate-00", (
        "one low-priority task with no stated risk must still outrank twelve "
        "critical ones behind a gate that explains itself"
    )
    assert d["recommendation"]["justified"] is False
    assert "states no risk" in d["recommendation"]["why"]


def test_among_justified_gates_volume_decides_again(conn):
    """The precedence rule must not flatten ordinary ranking."""
    for gid, n in (("a-gate-00", 1), ("b-gate-00", 6)):
        _task(conn, gid, status="in_progress", title=f"MANUAL-MODE GATE {gid}")
        conn.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s",
                     ("RISK: needs a human in the loop.", gid))
        for i in range(n):
            _task(conn, f"{gid}-t{i}", status="backlog", dep=gid, priority="high")
    assert A.diagnose(conn)["recommendation"]["release"] == "b-gate-00"


def test_a_justified_gate_carries_its_risk_into_the_output(conn):
    _task(conn, "g-00", status="in_progress", title="MANUAL-MODE GATE")
    conn.execute("UPDATE kanban_tasks SET description=%s WHERE id=%s",
                 ("RISK: the tasks need a human in the loop for classification.",
                  "g-00"))
    _task(conn, "t-1", status="backlog", dep="g-00")
    cand = A.diagnose(conn)["candidates"][0]
    assert cand["justified"] is True
    assert cand["risk_confidence"] == "explicit"
    assert "human in the loop" in cand["risk"]
    assert not any("NO stated risk" in c for c in cand["caveats"])
