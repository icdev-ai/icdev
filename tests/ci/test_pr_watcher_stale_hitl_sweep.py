# CUI // SP-CTI
"""A HITL alert must not outlive the work it describes.

Every resolve path in the watcher runs inside the per-task loop, and that loop
iterates `list_pr_tasks`, which selects only live states. So the moment a task
reaches `done` it drops out of the query and its alert is never looked at again.

Measured 2026-08-10. agov-inbox-01 and agov-inbox-02 turned out to have zero
unlanded content — every file byte-identical to main via #1497 — so their PRs
were closed and the tasks force-done. Both alerts stayed FIRING and were cleared
by hand from a `python -c`. No code path existed that would ever have cleared
them, which is the same "list that can only grow" failure #1511 was written to
fix, arrived at from the opposite direction: #1511 covered recovery, this covers
work that is genuinely over.
"""
from __future__ import annotations

import pytest

pr_watcher = pytest.importorskip("tools.ci.pr_watcher")


class _Conn:
    """Enough of the storage connection for the sweep: alerts + kanban_tasks."""

    def __init__(self, alerts, tasks):
        self.alerts = list(alerts)          # [source, ...] all firing
        self.tasks = dict(tasks)            # {task_id: status}; absent = deleted
        self.resolved: list[str] = []
        self._pending = None

    def execute(self, sql, params=None):
        low = " ".join(sql.split()).lower()
        if low.startswith("select source from alerts"):
            self._pending = [(s,) for s in self.alerts]
        elif low.startswith("select status from kanban_tasks"):
            task_id = params[0]
            self._pending = None if task_id not in self.tasks else [(self.tasks[task_id],)]
        elif low.startswith("update alerts set status = 'resolved'"):
            # (timestamp, source)
            self.resolved.append(params[1])
            self._pending = []
        else:
            self._pending = []
        return self

    def fetchall(self):
        return self._pending or []

    def fetchone(self):
        return (self._pending or [None])[0] if self._pending is not None else None

    def commit(self):
        pass

    def close(self):
        pass


def _watcher(conn):
    w = pr_watcher.PRWatcher(dry_run=False)
    w._connection = lambda: (lambda: conn)
    return w


def _sources(resolved):
    return sorted(s.split("pr_watcher:hitl:")[-1] for s in resolved)


# ── the stranding ───────────────────────────────────────────────────────────
def test_an_alert_for_a_DONE_task_is_cleared():
    """The measured case. A done task is not in list_pr_tasks, so the per-task
    loop can never reach it — only a sweep outside the loop can."""
    conn = _Conn(["pr_watcher:hitl:agov-inbox-01"], {"agov-inbox-01": "done"})
    assert _watcher(conn)._sweep_stale_hitl_alerts() == 1
    assert _sources(conn.resolved) == ["agov-inbox-01"]


@pytest.mark.parametrize("status", ["done", "dismissed", "cancelled", "archived"])
def test_every_terminal_state_clears(status):
    conn = _Conn(["pr_watcher:hitl:t-1"], {"t-1": status})
    assert _watcher(conn)._sweep_stale_hitl_alerts() == 1


def test_a_deleted_task_clears_rather_than_asking_a_human_to_chase_a_missing_row():
    conn = _Conn(["pr_watcher:hitl:gone"], {})
    assert _watcher(conn)._sweep_stale_hitl_alerts() == 1
    assert _sources(conn.resolved) == ["gone"]


# ── and must NOT over-reach ─────────────────────────────────────────────────
@pytest.mark.parametrize(
    "status",
    ["in_progress", "scheduled", "pr_opened", "ci_failed", "merge_conflict",
     "changes_requested"],
)
def test_a_LIVE_task_keeps_its_alert(status):
    """These are exactly the states list_pr_tasks polls. Clearing one would
    silence a task the pipeline has genuinely given up on — the alert is the
    only thing telling anyone it needs a human."""
    conn = _Conn(["pr_watcher:hitl:t-1"], {"t-1": status})
    assert _watcher(conn)._sweep_stale_hitl_alerts() == 0
    assert conn.resolved == []


def test_the_sweep_is_case_and_whitespace_tolerant():
    """Status is free text in places; 'Done ' must not read as live."""
    conn = _Conn(["pr_watcher:hitl:t-1"], {"t-1": " Done "})
    assert _watcher(conn)._sweep_stale_hitl_alerts() == 1


def test_only_hitl_sources_are_swept():
    """The sweep's own query is scoped, but the task-id parse must not turn a
    foreign source into a task lookup that accidentally matches."""
    conn = _Conn(["pr_watcher:hitl:t-1"], {"t-1": "done"})
    conn.alerts.append("cpu_monitor:host-7")   # would parse to the whole string
    w = _watcher(conn)
    assert w._sweep_stale_hitl_alerts() == 1
    assert _sources(conn.resolved) == ["t-1"]


# ── never the thing that breaks the poll ────────────────────────────────────
def test_a_db_failure_is_silence_not_a_crash():
    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def close(self):
            pass

    w = pr_watcher.PRWatcher(dry_run=False)
    w._connection = lambda: (lambda: _Boom())
    assert w._sweep_stale_hitl_alerts() == 0


def test_an_unreachable_database_is_silence_not_a_crash():
    w = pr_watcher.PRWatcher(dry_run=False)

    def _explode():
        raise RuntimeError("no connection")

    w._connection = lambda: _explode
    assert w._sweep_stale_hitl_alerts() == 0


# ── wiring: the sweep must run OUTSIDE the per-task loop ────────────────────
def test_the_sweep_runs_after_the_loop_not_inside_it():
    """Called from inside the per-task loop it would only ever see tasks the
    loop already reached — which is the bug, not the fix."""
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    # Match the CALL, not any mention: a comment naming the function sits inside
    # the loop at a deeper indent, and matching that made this test fail against
    # correct code.
    call = src.index("self._sweep_stale_hitl_alerts()")
    loop = src.index("for task in tasks:")
    tail = src.index("report.finished_at")
    assert loop < call < tail, "the sweep belongs between the loop and the report"
    # ...and at the poll's own indent level, not the loop body's.
    line_start = src.rfind("\n", 0, call) + 1
    indent = len(src[line_start:call]) - len(src[line_start:call].lstrip())
    assert indent == 8, f"sweep is indented {indent}; inside the loop it would be deeper"


def test_a_closed_pr_clears_its_alert_in_the_loop():
    """The third door: a PR closed while its task is still live is still polled,
    so it never reaches the sweep. A CLOSED PR cannot be rebased, resumed or
    merged — the alert is spent with it."""
    import inspect
    src = inspect.getsource(pr_watcher.PRWatcher.poll_once)
    i = src.index('"CLOSED"')
    assert "_resolve_hitl_alert" in src[i:i + 400]
