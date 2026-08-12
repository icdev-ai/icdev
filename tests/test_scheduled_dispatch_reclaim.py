# CUI // SP-CTI
"""A `scheduled` row holding a dead dispatch PID is invisible to every sweep.

`recover_interrupted_tasks` sweeps `WHERE status = 'in_progress'`. A dispatch
that dies BEFORE the row moves to in_progress leaves it in `scheduled` with a
dead `dispatch_pid`, which nothing looks at — so the row is neither running nor
reclaimable, and the slot it would have used is never used.

Measured 2026-08-12: exa-bench-10 sat in `scheduled` with pid 17016 dead and a
heartbeat 71 minutes stale, while two of three dispatch slots were free and it
was the only eligible task on the board. Nothing went red.
"""
from __future__ import annotations

import importlib

import pytest

recovery = importlib.import_module("tools.kanban.startup_recovery")


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []
        self.committed = False

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            return _FakeCursor(self._rows)
        self.updates.append((sql, params))
        return _FakeCursor([])

    def commit(self):
        self.committed = True

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def test_a_dead_pid_is_reclaimed(monkeypatch):
    monkeypatch.setattr(recovery, "dispatch_liveness", lambda pid, start=None: False)
    conn = _FakeConn([{"id": "exa-bench-10", "dispatch_pid": 17016, "dispatch_pid_started_at": "123"}])
    out = recovery.reclaim_stale_scheduled_dispatches(conn=conn)
    assert [r["id"] for r in out["reclaimed"]] == ["exa-bench-10"]
    assert conn.updates and conn.committed
    assert "dispatch_pid = NULL" in conn.updates[0][0]


def test_a_live_pid_is_left_alone(monkeypatch):
    """A dispatch genuinely in flight must not have its stamp cleared."""
    monkeypatch.setattr(recovery, "dispatch_liveness", lambda pid, start=None: True)
    conn = _FakeConn([{"id": "busy-01", "dispatch_pid": 4242, "dispatch_pid_started_at": "123"}])
    out = recovery.reclaim_stale_scheduled_dispatches(conn=conn)
    assert out["reclaimed"] == []
    assert conn.updates == []
    assert out["skipped"][0]["why"] == "still ours"


def test_undeterminable_liveness_is_treated_as_running(monkeypatch):
    """THE SAFETY PROPERTY.

    `None` means "cannot tell", not "dead". Clearing a stamp we cannot disprove
    would double-dispatch a task that is quietly working — strictly worse than
    the stall this fixes.
    """
    monkeypatch.setattr(recovery, "dispatch_liveness", lambda pid, start=None: None)
    conn = _FakeConn([{"id": "unknown-01", "dispatch_pid": 999, "dispatch_pid_started_at": None}])
    out = recovery.reclaim_stale_scheduled_dispatches(conn=conn)
    assert out["reclaimed"] == []
    assert conn.updates == []
    assert "undeterminable" in out["skipped"][0]["why"]


def test_dry_run_reports_without_writing(monkeypatch):
    monkeypatch.setattr(recovery, "dispatch_liveness", lambda pid, start=None: False)
    conn = _FakeConn([{"id": "exa-bench-10", "dispatch_pid": 17016, "dispatch_pid_started_at": "123"}])
    out = recovery.reclaim_stale_scheduled_dispatches(conn=conn, dry_run=True)
    assert [r["id"] for r in out["reclaimed"]] == ["exa-bench-10"]
    assert conn.updates == []
    assert not conn.committed


def test_a_db_error_never_wedges_dispatch(monkeypatch):
    """This runs inside the dispatch loop; it must degrade, not raise."""

    class _Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def close(self):
            pass

    out = recovery.reclaim_stale_scheduled_dispatches(conn=_Boom())
    assert out["reclaimed"] == []


@pytest.mark.parametrize("pid", [0, None, -1])
def test_liveness_is_undeterminable_for_a_nonsense_pid(pid):
    assert recovery.dispatch_liveness(pid, "123") is None


def test_a_free_pid_is_provably_gone():
    assert recovery.dispatch_liveness(999999, "123") is False


def test_a_recycled_pid_reads_as_GONE_not_alive():
    """THE WINDOWS CASE, and the one a bare pid_exists gets wrong.

    PIDs are recycled aggressively on Windows. A stale pid that now belongs to
    an unrelated process reads as "alive" to pid_exists, so the stalled row is
    skipped forever and its slot never reclaimed. Comparing the recorded start
    time is what distinguishes "our process is still running" from "the number
    was handed to somebody else".
    """
    import os

    from tools.kanban.dispatch_reaper import process_start_time

    me = os.getpid()
    real_start = process_start_time(me)
    if not real_start:
        pytest.skip("process start time unavailable on this host")
    assert recovery.dispatch_liveness(me, real_start) is True
    assert recovery.dispatch_liveness(me, "not-the-recorded-start") is False


def test_a_live_pid_with_no_recorded_start_is_undeterminable():
    """Without a start time there is nothing to disprove reuse with."""
    import os

    assert recovery.dispatch_liveness(os.getpid(), None) is None


def test_the_reclaim_runs_in_the_dispatch_path():
    """Otherwise it is a reclaimer nothing calls — the bug it exists to fix."""
    import inspect

    kanban = importlib.import_module("tools.genesis.reflexes.kanban")
    src = inspect.getsource(kanban)
    assert "reclaim_stale_scheduled_dispatches" in src
