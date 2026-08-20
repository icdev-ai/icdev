# CUI // SP-CTI
"""kpr-watch-11: two writers taking turns on one row, and nothing noticing.

On 2026-08-19 `cef-ui-03` flipped ``done`` <-> ``backlog`` 95 times in 5.5
hours. pr_watcher completed it because its PR had merged; the scheduler demoted
it because its run had run out of budget. Every individual transition was
legitimate, so no per-move guard could see it — the board read ``scheduled``
throughout and the scheduler reported ``idle``, and it took a human asking why
the dispatcher looked dead.

`kpr-dup-09` fixed the mechanism behind that loop. This detects the SHAPE, so
the next pair of writers that disagree shows up in minutes.

The two properties that decide whether it is useful rather than noise are both
pinned here: a RETURN is a pair (progression through distinct statuses is not
churn however many steps it takes), and CONTESTED means two or more writers.
Measured on the live board, 34 tasks oscillate and only 3 are contested — the
rest are one writer retrying, which is the system working.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.kanban import status_churn as sc

T0 = datetime(2026, 8, 19, tzinfo=timezone.utc)


def _t(task, frm, to, actor="scheduler", minutes=0):
    when = T0 + timedelta(minutes=minutes)
    # `_when` is what `load_transitions` parses onto every row, and
    # `find_returns` consumes that output — a fixture without it would be
    # testing a shape the function is never handed.
    return {"task_id": task, "from_status": frm, "to_status": to,
            "actor": actor, "recorded_at": when.isoformat(), "_when": when}


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _sql, _params=()):
        return self

    def fetchall(self):
        return self._rows


def _flip(task, a, b, n, actor_a="pr_watcher", actor_b="scheduler"):
    """n full A->B->A returns, alternating the two writers."""
    rows, m = [], 0
    for _ in range(n + 1):
        rows.append(_t(task, a, b, actor_b, m)); m += 1
        rows.append(_t(task, b, a, actor_a, m)); m += 1
    return rows


# ── what counts as a return ────────────────────────────────────────────────
def test_progression_is_not_churn():
    """A task moving through distinct statuses changes status five times and is
    simply progressing. A detector that flags that is unusable."""
    rows = [_t("t-01", "backlog", "scheduled", minutes=0),
            _t("t-01", "scheduled", "in_progress", minutes=1),
            _t("t-01", "in_progress", "pr_opened", minutes=2),
            _t("t-01", "pr_opened", "done", minutes=3)]
    assert sc.find_returns(rows) == {}


def test_an_A_B_A_pair_is_a_return():
    rows = [_t("t-01", "done", "backlog", minutes=0),
            _t("t-01", "backlog", "done", minutes=1)]
    found = sc.find_returns(rows)
    assert list(found) == ["t-01"]
    assert found["t-01"][0]["cycle"] == "done -> backlog -> done"


def test_returns_are_counted_per_task_not_across_tasks():
    """Two tasks each flipping once must not add up to one task flipping twice."""
    rows = [_t("t-01", "done", "backlog", minutes=0),
            _t("t-01", "backlog", "done", minutes=1),
            _t("t-02", "done", "backlog", minutes=2),
            _t("t-02", "backlog", "done", minutes=3)]
    found = sc.find_returns(rows)
    assert {k: len(v) for k, v in found.items()} == {"t-01": 1, "t-02": 1}


# ── the threshold ──────────────────────────────────────────────────────────
def test_one_return_is_routine_and_not_reported():
    """Surveyed: 11.9% of all tasks have at least one return. Firing on that is
    noise, and noise is how a detector gets ignored."""
    rows = [_t("t-01", "done", "backlog", minutes=0),
            _t("t-01", "backlog", "done", minutes=1)]
    report = sc.churn_report(_Conn(rows), window_hours=10**6)
    assert report["tasks_with_any_return"] == 1
    assert report["oscillating"] == 0


def test_the_surveyed_default_is_ten():
    """Ten puts the fire rate at 1.09%, below the 1.63% CLAUDE.md already calls
    refusing routine work. Lowering it needs a fresh survey, not a preference."""
    assert sc.DEFAULT_MIN_RETURNS == 10


def test_a_task_over_the_threshold_is_reported():
    report = sc.churn_report(_Conn(_flip("cef-ui-03", "done", "backlog", 12)),
                             window_hours=10**6, min_returns=10)
    assert report["oscillating"] == 1
    assert report["tasks"][0]["task_id"] == "cef-ui-03"
    assert report["tasks"][0]["returns"] >= 10


# ── contested is the dangerous shape ───────────────────────────────────────
def test_two_writers_are_CONTESTED():
    """The live case: pr_watcher and the scheduler answering different questions
    about the same row. The fix is a rule about ownership."""
    report = sc.churn_report(_Conn(_flip("cef-ui-03", "done", "backlog", 12)),
                             window_hours=10**6, min_returns=10)
    row = report["tasks"][0]
    assert row["contested"] is True
    assert set(row["actors"]) == {"pr_watcher", "scheduler"}
    assert report["contested"] == 1


def test_one_writer_retrying_is_NOT_contested():
    """`in_progress -> token_exhausted -> in_progress` by the scheduler alone is
    a task being re-attempted. Measured: 31 of the 34 oscillating tasks on the
    live board are this, and calling them a fight would bury the 3 that are."""
    rows = _flip("t-01", "in_progress", "token_exhausted", 12,
                 actor_a="scheduler", actor_b="scheduler")
    report = sc.churn_report(_Conn(rows), window_hours=10**6, min_returns=10)
    assert report["oscillating"] == 1
    assert report["tasks"][0]["contested"] is False
    assert report["contested"] == 0


def test_contested_sorts_ahead_of_a_busier_retry_loop():
    """A two-writer fight needs an ownership rule; a retry loop needs a budget.
    Different fixes, so the rarer and more dangerous one must not be buried
    under a noisier but benign one."""
    rows = _flip("retry-01", "in_progress", "token_exhausted", 40,
                 actor_a="scheduler", actor_b="scheduler")
    rows += _flip("fight-01", "done", "backlog", 11)
    report = sc.churn_report(_Conn(rows), window_hours=10**6, min_returns=10)
    assert [r["task_id"] for r in report["tasks"]][0] == "fight-01"
    assert report["tasks"][0]["returns"] < report["tasks"][1]["returns"]


# ── honest absence ─────────────────────────────────────────────────────────
def test_an_idle_board_is_UNMEASURABLE_not_clean():
    """An idle weekend, or a fresh worktree, must not read as proof that
    nothing is oscillating."""
    report = sc.churn_report(_Conn([]))
    assert report["measurable"] is False
    assert "oscillating" not in report


def test_a_busy_board_with_no_churn_says_so_plainly():
    """The measured clean case is different from the unmeasured one, and the
    renderer must say which it is."""
    rows = [_t("t-01", "backlog", "scheduled", minutes=0),
            _t("t-01", "scheduled", "done", minutes=1)]
    report = sc.churn_report(_Conn(rows), window_hours=10**6)
    assert report["measurable"] is True and report["oscillating"] == 0
    assert "No task is oscillating" in sc.render(report)


def test_the_report_is_never_a_gate():
    """It measures the BOARD, not a diff. Failing a commit on it would block
    work the committer did not cause, and the check would be neutralised."""
    import inspect

    src = inspect.getsource(sc.main)
    assert 'add_argument("--gate"' not in src
    assert '--gate' not in src
