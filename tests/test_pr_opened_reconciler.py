# CUI // SP-CTI
"""A task with an open PR must reach `pr_opened` (rem-hyg-18).

TWO SURFACES SHARE THE NAME "Awaiting Merge" AND READ DIFFERENT SOURCES. The
Home panel reads the FORGE (`gh pr list`); the Kanban column (kanban.html)
reads `kanban_tasks.status = 'pr_opened'`. Measured 2026-08-21: the panel listed
three open PRs while the column was EMPTY, and the board showed those tasks as
`scheduled` / `in_progress`.

THE CAUSE. `pr_opened` had exactly ONE writer in the whole tree — the
stale-reaper — gated on `WHERE status = 'in_progress'`. A task sitting in
`scheduled` or `backlog` with an open PR could therefore never reach `pr_opened`
by ANY path. That is not an edge case: it is what every PR opened outside the
runner produces, including every one a human raises by hand. (77 `pr_opened`
transitions in seven days, and zero tasks in that state, is the shape of a status
reachable only as a side effect.)

WHAT THIS DELIBERATELY DOES NOT DO. It does not touch `in_progress`. The
stale-reaper already handles that, and handles it better — it waits for the task
to stop heartbeating ("finished, not stalled"). A task whose worker is alive and
pushing commits genuinely IS in progress, and moving it early would free a
dispatch slot while that worker still burns tokens, quietly raising concurrency
above MAX_IN_PROGRESS.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []
        self.committed = False

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            self._last = [dict(r) for r in self._rows]
        else:
            self.updates.append((sql, params))
            self._last = []
        return self

    def fetchall(self):
        return getattr(self, "_last", [])

    def commit(self):
        self.committed = True

    def close(self):
        return None


def _run(monkeypatch, rows, branches):
    conn = _Conn(rows)
    monkeypatch.setattr(k, "_open_pr_head_branches", lambda _root: set(branches))
    monkeypatch.setattr(k, "get_connection", lambda *a, **kw: conn)
    moved = k._reconcile_pr_opened()
    return moved, conn


# --------------------------------------------------------------------------- #
# 1. The gap nothing else could reach
# --------------------------------------------------------------------------- #
def test_a_scheduled_task_with_an_open_pr_moves(monkeypatch):
    """rem-hyg-17's exact shape: PR raised by hand, task left `scheduled`."""
    moved, conn = _run(monkeypatch,
                       [{"id": "rem-hyg-17", "status": "scheduled"}],
                       ["kanban/rem-hyg-17"])
    assert moved == 1
    assert conn.committed
    sql, params = conn.updates[0]
    assert "pr_opened" in sql
    assert "rem-hyg-17" in params


def test_a_backlog_task_with_an_open_pr_moves(monkeypatch):
    moved, _ = _run(monkeypatch, [{"id": "t-1", "status": "backlog"}], ["kanban/t-1"])
    assert moved == 1


def test_a_task_without_a_pr_is_left_alone(monkeypatch):
    moved, conn = _run(monkeypatch, [{"id": "t-2", "status": "scheduled"}], ["kanban/other"])
    assert moved == 0
    assert conn.updates == []


# --------------------------------------------------------------------------- #
# 2. What it must NOT touch
# --------------------------------------------------------------------------- #
def test_in_progress_is_never_moved_here(monkeypatch):
    """The stale-reaper owns that case and waits for the heartbeat to stop.
    Moving it early frees a dispatch slot while the worker is still running."""
    moved, conn = _run(monkeypatch,
                       [{"id": "t-3", "status": "in_progress"}], ["kanban/t-3"])
    assert moved == 0, "an in_progress task with a live worker must stay in_progress"
    assert conn.updates == []


def test_the_update_is_guarded_on_the_status_it_read(monkeypatch):
    """The UPDATE re-asserts the status in its WHERE clause, so a task that
    moved between the SELECT and the UPDATE is not overwritten — the two-writers
    race this board has already been bitten by."""
    _moved, conn = _run(monkeypatch,
                        [{"id": "t-4", "status": "scheduled"}], ["kanban/t-4"])
    sql, params = conn.updates[0]
    assert "AND status = %s" in sql
    assert "scheduled" in params


# --------------------------------------------------------------------------- #
# 3. It only ever moves FORWARD, on positive evidence
# --------------------------------------------------------------------------- #
def test_no_open_prs_is_a_no_op(monkeypatch):
    """An empty branch set means either no open PRs or `gh` was unavailable.
    Both are correctly a no-op: this moves a task only on positive evidence
    that its PR exists."""
    moved, conn = _run(monkeypatch, [{"id": "t-5", "status": "scheduled"}], [])
    assert moved == 0
    assert conn.updates == []


def test_a_state_ahead_of_pr_opened_is_never_rewritten(monkeypatch):
    """`changes_requested`, `merge_conflict`, `ci_failed` and `done` are all
    AHEAD of pr_opened. Moving one backwards would erase a review outcome — so
    they must not even be selected, whatever their PR looks like."""
    ahead = [
        {"id": "t-a", "status": "changes_requested"},
        {"id": "t-b", "status": "merge_conflict"},
        {"id": "t-c", "status": "ci_failed"},
        {"id": "t-d", "status": "done"},
    ]
    # The real query filters these out in SQL; the stub returns whatever it is
    # given, so this asserts the ELIGIBLE LIST is what bounds the write.
    moved, conn = _run(monkeypatch, ahead,
                       ["kanban/t-a", "kanban/t-b", "kanban/t-c", "kanban/t-d"])
    eligible_written = [p for _s, p in conn.updates]
    for _sql, params in conn.updates:
        assert params[-1] in ("scheduled", "backlog"), (
            f"a state ahead of pr_opened was rewritten: {params}"
        )
    assert moved == len(eligible_written)


def test_a_database_failure_never_wedges_the_cycle(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(k, "_open_pr_head_branches", lambda _r: {"kanban/t"})
    monkeypatch.setattr(k, "get_connection", _boom)
    assert k._reconcile_pr_opened() == 0


def test_nothing_is_committed_when_nothing_moved(monkeypatch):
    _moved, conn = _run(monkeypatch, [{"id": "t-6", "status": "scheduled"}], ["kanban/zzz"])
    assert conn.committed is False
