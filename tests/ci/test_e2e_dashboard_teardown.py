#!/usr/bin/env python3
"""An E2E run must not leave a dashboard holding the port. CUI // SP-CTI

MEASURED 2026-08-15: three dashboards launched from
``.tmp/worktrees/task-e2e-27e596dc`` were still running six hours after their
runs, and one of them held **port 5050** — the main dashboard's port — serving a
commit eight hours stale. Restarting the real dashboard by hand did nothing
visible, because the orphan owned the socket and the replacement could not bind
it. That presents to a user as "Flask does not auto-reload", which is a different
bug entirely and was fixed separately.

Two independent leaks produced it:

  1. ``main`` stopped the dashboard in STRAIGHT-LINE code after the try/except,
     so any exception between starting it and reaching the cleanup skipped
     teardown. A kanban session that exhausts its token budget mid-E2E takes
     exactly that path — and token exhaustion is common enough on this board that
     402 events are on record.
  2. ``start_dashboard`` sent ONE ``terminate()`` on the slow-start path and
     returned ``None``. terminate is a request, not a guarantee, and returning
     None discards the handle — so anything still alive could never be stopped by
     this program again.

Deterministic: every process is a fake. Nothing is spawned, nothing is killed.
"""
from __future__ import annotations

import inspect
import pathlib
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.ci.workflows import icdev_e2e  # noqa: E402


class _FakeProc:
    """Records the teardown calls made against it."""

    def __init__(self, *, alive: bool = True, dies_on_terminate: bool = True):
        self.pid = 4242
        self._alive = alive
        self._dies_on_terminate = dies_on_terminate
        self.calls: list[str] = []

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.calls.append("terminate")
        if self._dies_on_terminate:
            self._alive = False

    def kill(self):
        self.calls.append("kill")
        self._alive = False

    def wait(self, timeout=None):
        self.calls.append(f"wait({timeout})")
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="dash", timeout=timeout or 0)
        return 0


# --------------------------------------------------------------------------- #
# stop_dashboard: terminate is a REQUEST
# --------------------------------------------------------------------------- #

def test_a_cooperative_process_is_terminated_and_reaped():
    proc = _FakeProc(dies_on_terminate=True)
    icdev_e2e.stop_dashboard(proc)
    assert proc.calls[0] == "terminate"
    assert any(c.startswith("wait") for c in proc.calls)
    assert "kill" not in proc.calls, "no need to escalate when terminate worked"


def test_a_stubborn_process_is_escalated_to_kill():
    """THE leak. A dashboard mid DB-init outlives terminate()."""
    proc = _FakeProc(dies_on_terminate=False)
    icdev_e2e.stop_dashboard(proc)
    assert "terminate" in proc.calls
    assert "kill" in proc.calls, (
        "terminate() returns without waiting; a process that ignores it must be "
        "killed or it keeps the port forever"
    )


def test_an_already_dead_process_is_left_alone():
    proc = _FakeProc(alive=False)
    icdev_e2e.stop_dashboard(proc)
    assert proc.calls == []


def test_none_is_accepted():
    icdev_e2e.stop_dashboard(None)  # must not raise


def test_a_teardown_error_does_not_propagate():
    """Teardown must never mask the E2E result it is cleaning up after."""
    class _Angry(_FakeProc):
        def terminate(self):
            raise OSError("access denied")

    icdev_e2e.stop_dashboard(_Angry())


# --------------------------------------------------------------------------- #
# The slow-start path — where the handle used to be thrown away
# --------------------------------------------------------------------------- #

def test_slow_start_routes_through_stop_dashboard_not_bare_terminate():
    src = inspect.getsource(icdev_e2e.start_dashboard)
    assert "stop_dashboard(proc)" in src
    assert "proc.terminate()" not in src, (
        "a single terminate() on the slow-start path is what orphaned the "
        "process the caller then had no handle for"
    )


# --------------------------------------------------------------------------- #
# main(): teardown must survive an exception
# --------------------------------------------------------------------------- #

def test_the_teardown_is_inside_a_finally():
    """Straight-line cleanup is skipped by any raise above it."""
    src = inspect.getsource(icdev_e2e)
    start = src.find("def main(")
    body = src[start:] if start != -1 else src
    fin = body.find("    finally:")
    stop = body.find("stop_dashboard(dashboard_proc)")
    assert fin != -1, "main must have a finally block"
    assert stop != -1 and stop > fin, "the stop call must sit inside the finally"


def test_the_e2e_run_is_inside_that_try():
    """The finally is worthless if the risky work sits outside it."""
    src = inspect.getsource(icdev_e2e)
    body = src[src.find("def main("):]
    try_at = body.find("    try:\n        # Step 3")
    run_at = body.find("run_e2e_tests(validate_screenshots=True)")
    fin_at = body.find("    finally:")
    assert try_at != -1
    assert try_at < run_at < fin_at


def test_atexit_covers_a_sys_exit_from_deeper_code():
    src = inspect.getsource(icdev_e2e)
    assert "atexit.register(stop_dashboard, dashboard_proc)" in src


def test_atexit_is_registered_only_when_we_own_the_process():
    """Registering unconditionally would try to stop a dashboard we did not start.

    The `else` branch — a dashboard already running — must NOT be torn down by
    this program; that is the operator's long-lived instance.
    """
    src = inspect.getsource(icdev_e2e)
    body = src[src.find("def main("):]
    reg = body.find("atexit.register")
    already = body.find("Dashboard already running")
    started = body.find("Dashboard started (PID")
    assert started < reg < already, "registration must sit in the we-started-it branch"


@pytest.mark.parametrize("marker", [
    "port 5050",
    "task-e2e-27e596dc",
])
def test_the_incident_is_recorded_in_the_source(marker):
    """The next reader needs to know this was observed, not theorised."""
    src = inspect.getsource(icdev_e2e)
    assert marker in src
