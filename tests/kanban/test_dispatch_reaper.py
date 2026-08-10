# CUI // SP-CTI
"""A reap that does not kill is not a reap.

task-e2e-ebf5ab21 (2026-08-10) went through three stale-reaper -> backlog
transitions and reached failure_count 3 while a Playwright tree it had spawned
was still alive — 1.7s CPU total, no browser, no test workers, its launcher
already dead. Each reap flipped the status and left the tree holding a worktree
and port 5090; the scheduler re-dispatched into the same worktree every time.

The safety property matters more than the cleanup: a stored pid may have been
reused by an unrelated process, and killing that is far worse than leaking the
one we meant to kill. Every unknown must therefore decline.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from tools.kanban import dispatch_reaper as dr


def _spawn():
    """A real child that sleeps — a live pid we own and can safely kill."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── identity: the guard that makes killing safe ─────────────────────────────
def test_a_live_pid_has_an_identity():
    p = _spawn()
    try:
        assert dr.process_start_time(p.pid)
    finally:
        p.kill()


def test_a_pid_matches_itself():
    p = _spawn()
    try:
        assert dr.is_same_process(p.pid, dr.process_start_time(p.pid)) is True
    finally:
        p.kill()


def test_a_MISMATCHED_start_time_is_refused():
    """The pid-reuse case. This is the assertion the whole module exists for."""
    p = _spawn()
    try:
        assert dr.is_same_process(p.pid, "Sat Jan  1 00:00:00 2000") is False
    finally:
        p.kill()


def test_no_recorded_start_time_is_refused():
    """Absent evidence is not permission. A task dispatched before this feature
    landed has a null start time, and must never be killed on the pid alone."""
    p = _spawn()
    try:
        assert dr.is_same_process(p.pid, None) is False
        assert dr.is_same_process(p.pid, "") is False
    finally:
        p.kill()


def test_a_dead_pid_is_refused():
    p = _spawn()
    pid = p.pid
    p.kill()
    p.wait(timeout=30)
    time.sleep(0.5)
    assert dr.is_same_process(pid, "anything") is False


@pytest.mark.parametrize("bad", [0, -1, None])
def test_nonsense_pids_are_refused(bad):
    assert dr.process_start_time(bad) is None
    assert dr.is_same_process(bad, "x") is False


# ── the kill itself ─────────────────────────────────────────────────────────
def test_it_kills_a_process_it_can_prove_is_ours():
    p = _spawn()
    started = dr.process_start_time(p.pid)
    out = dr.kill_tree(p.pid, started)
    assert out["killed"] is True, out
    deadline = time.time() + 30
    while time.time() < deadline and p.poll() is None:
        time.sleep(0.25)
    assert p.poll() is not None, "the process should be gone"


def test_it_REFUSES_to_kill_when_identity_does_not_match():
    p = _spawn()
    try:
        out = dr.kill_tree(p.pid, "Sat Jan  1 00:00:00 2000")
        assert out["killed"] is False
        assert "does not match" in out["reason"]
        time.sleep(0.5)
        assert p.poll() is None, "an unmatched pid must be left strictly alone"
    finally:
        p.kill()


def test_children_die_too():
    """The orphaned Playwright run held a dashboard subprocess on a port; killing
    only the parent leaves that port bound and the next dispatch unable to start.

    The child reports its own pid on stdout rather than being discovered by
    process enumeration — `wmic` is gone on Windows 11 26200, and a helper that
    silently returns [] would make this test pass while asserting nothing.
    """
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time; "
         "k=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); "
         "print(k.pid, flush=True); time.sleep(120)"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        child_pid = int(parent.stdout.readline().strip())
        assert dr.process_start_time(child_pid), "child should be alive to start with"
        started = dr.process_start_time(parent.pid)
        assert dr.kill_tree(parent.pid, started)["killed"] is True

        deadline = time.time() + 30
        while time.time() < deadline and dr.process_start_time(child_pid):
            time.sleep(0.25)
        assert dr.process_start_time(child_pid) is None,             f"child {child_pid} survived the tree kill"
    finally:
        try:
            parent.kill()
        except Exception:
            pass


# ── the DB seam ─────────────────────────────────────────────────────────────
class _Conn:
    def __init__(self, row=None, raises=False):
        self.row, self.raises, self.sql = row, raises, []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if self.raises:
            raise RuntimeError("no such column: dispatch_pid")
        return type("C", (), {"fetchone": lambda _s: self.row})()

    def commit(self):
        pass


def test_a_task_with_no_recorded_pid_is_a_no_op():
    assert dr.kill_recorded_dispatch(_Conn(row=(None, None)), "t-1")["killed"] is False


def test_a_premigration_database_does_not_raise():
    """The reaper runs on every cycle; a missing column must degrade, not throw."""
    out = dr.kill_recorded_dispatch(_Conn(raises=True), "t-1")
    assert out["killed"] is False and "dispatch_pid" in out["reason"]


def test_recording_a_dispatch_stores_pid_AND_start_time():
    """The start time is not decoration — without it every later kill declines."""
    p = _spawn()
    try:
        c = _Conn()
        dr.record_dispatch(c, "t-1", p.pid)
        assert any("dispatch_pid_started_at" in q for q in c.sql)
    finally:
        p.kill()
