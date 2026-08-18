# CUI // SP-CTI
"""A merged PR whose task is not done must not be invisible forever.

`pr_watcher.list_pr_tasks` polls BY STATUS:

    status IN ('in_progress','scheduled','pr_opened','ci_failed',
               'merge_conflict','changes_requested')

`backlog` is absent, and nothing in the tree reconciles "a merged PR whose task
is not done". So once a task carrying a live PR lands in a status outside that
set, the ONLY component that closes the loop stops looking at it, and the board
can never self-correct.

MEASURED 2026-08-18. kpr-watch-01 was dispatched at 16:09, opened PR #1744 at
16:27, and was reaped to `backlog` at 16:35 — eight minutes AFTER its PR
existed. The PR merged two days later with nothing watching. The task still read
`backlog` while its work was on main, and five kpr-watch-* tasks sat behind it.

One live case out of 424 PR-carrying tasks, so this is rare — but it is
PERMANENT and SILENT when it happens, and it takes a human noticing. The entry
paths are many (the stale reaper, the PR-flow rollback, auto-revive, the orphan
sweep, a manual move); the trap is one, so it is fixed once here rather than at
each writer.

Reconciling from the PR side is what makes it writer-agnostic: a task whose PR
is MERGED and whose status is not terminal is finished, whatever moved it.
"""
from __future__ import annotations

import json

import tools.ci.pr_watcher as pw

POLLED = ("in_progress", "scheduled", "pr_opened",
          "ci_failed", "merge_conflict", "changes_requested")


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            return _R(self.rows)
        self.updates.append((sql, params))
        return _R([])

    def commit(self):
        pass

    def close(self):
        pass


class _R:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _watcher(rows, states):
    # `_connection()` returns the callable itself, so this must be the
    # factory — wrapping it twice hands the caller a lambda, not a conn.
    conn = _Conn(rows)
    w = pw.PRWatcher(config={}, get_connection=lambda: conn)
    w.dry_run = False
    w._pr_state_runner = lambda number: states.get(str(number))
    w.conn = conn
    return w


# ── the reconcile ──────────────────────────────────────────────────────────
def test_a_merged_pr_on_a_backlog_task_is_reconciled_to_done():
    """The kpr-watch-01 case."""
    rows = [{"id": "kpr-watch-01", "status": "backlog",
             "executor_url": "https://github.com/o/r/pull/1744"}]
    w = _watcher(rows, {"1744": "MERGED"})
    out = w.reconcile_stranded_tasks()
    assert [e["task_id"] for e in out["reconciled"]] == ["kpr-watch-01"]


def test_an_OPEN_pr_on_a_stranded_task_is_left_alone():
    """Not done yet. Marking it done would be a lie the board never recovers
    from — the opposite error, and the more expensive one."""
    rows = [{"id": "t-1", "status": "backlog",
             "executor_url": "https://github.com/o/r/pull/900"}]
    w = _watcher(rows, {"900": "OPEN"})
    assert w.reconcile_stranded_tasks()["reconciled"] == []


def test_a_CLOSED_unmerged_pr_is_left_alone():
    """Closed without merging means the work did NOT land; backlog is correct."""
    rows = [{"id": "t-1", "status": "backlog",
             "executor_url": "https://github.com/o/r/pull/900"}]
    w = _watcher(rows, {"900": "CLOSED"})
    assert w.reconcile_stranded_tasks()["reconciled"] == []


def test_an_UNKNOWN_pr_state_is_left_alone():
    """Same rule as everywhere else in this watcher: an unanswerable query is
    not evidence. A gh timeout must not mark a task done."""
    rows = [{"id": "t-1", "status": "backlog",
             "executor_url": "https://github.com/o/r/pull/900"}]
    w = _watcher(rows, {})          # no answer for 900
    out = w.reconcile_stranded_tasks()
    assert out["reconciled"] == []
    assert out["unknown"] == ["t-1"]


# ── scope: it must cost nothing on a healthy board ─────────────────────────
def test_a_task_in_a_POLLED_status_is_not_touched():
    """The main loop already services those. Reconciling them here would give
    two components authority over the same transition."""
    for status in POLLED:
        rows = [{"id": "t-1", "status": status,
                 "executor_url": "https://github.com/o/r/pull/900"}]
        w = _watcher(rows, {"900": "MERGED"})
        assert w.reconcile_stranded_tasks()["reconciled"] == [], status


def test_a_terminal_task_is_not_touched():
    for status in ("done", "cancelled", "decomposed"):
        rows = [{"id": "t-1", "status": status,
                 "executor_url": "https://github.com/o/r/pull/900"}]
        w = _watcher(rows, {"900": "MERGED"})
        assert w.reconcile_stranded_tasks()["reconciled"] == [], status


def test_a_task_with_no_pr_url_is_not_queried():
    """No PR, nothing to reconcile — and no API call to pay for."""
    asked = []
    rows = [{"id": "t-1", "status": "backlog", "executor_url": ""}]
    w = _watcher(rows, {})
    w._pr_state_runner = lambda n: asked.append(n)
    assert w.reconcile_stranded_tasks()["reconciled"] == []
    assert asked == [], "a task with no PR must cost no forge call"


def test_a_healthy_board_makes_no_forge_calls():
    """Measured scope: 1 stranded task out of 424 carrying a PR. On a board with
    none, this sweep must be free, or it becomes a per-poll tax."""
    asked = []
    rows = [{"id": f"t-{i}", "status": "done",
             "executor_url": f"https://github.com/o/r/pull/{i}"} for i in range(50)]
    w = _watcher(rows, {})
    w._pr_state_runner = lambda n: asked.append(n)
    w.reconcile_stranded_tasks()
    assert asked == []


# ── it writes the transition, and says why ─────────────────────────────────
def test_the_transition_records_that_it_was_a_reconcile():
    """`done` with no explanation would read as a normal completion and hide
    that the board had been wrong."""
    rows = [{"id": "t-1", "status": "backlog",
             "executor_url": "https://github.com/o/r/pull/900"}]
    w = _watcher(rows, {"900": "MERGED"})
    out = w.reconcile_stranded_tasks()
    reason = out["reconciled"][0]["reason"].lower()
    assert "merged" in reason
    assert "900" in out["reconciled"][0]["reason"]


def test_dry_run_reports_without_writing():
    rows = [{"id": "t-1", "status": "backlog",
             "executor_url": "https://github.com/o/r/pull/900"}]
    w = _watcher(rows, {"900": "MERGED"})
    w.dry_run = True
    out = w.reconcile_stranded_tasks()
    assert [e["task_id"] for e in out["reconciled"]] == ["t-1"]
    assert out["written"] is False


def test_the_sweep_runs_from_poll_once():
    """A reconciler nobody calls is the defect it exists to fix, one level up."""
    import inspect

    assert "reconcile_stranded_tasks" in inspect.getsource(pw.PRWatcher.poll_once)


def test_the_pr_state_lookup_parses_the_forge_answer():
    """The default runner, exercised once so the stub above cannot be the only
    thing that is ever tested."""
    class _P:
        returncode, stdout, stderr = 0, json.dumps({"state": "MERGED"}), ""

    w = pw.PRWatcher(config={}, get_connection=lambda: None)
    assert w._pr_state("900", runner=lambda *a, **k: _P()) == "MERGED"

    class _Bad:
        returncode, stdout, stderr = 1, "", "gh: nope"

    assert w._pr_state("900", runner=lambda *a, **k: _Bad()) is None
