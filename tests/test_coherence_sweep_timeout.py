# CUI // SP-CTI
"""A pathological check must not be able to run past the sweep budget.

gpx-perf-01: ``run_checks()`` had no kill. A check that hangs blocks the whole
sweep, and there is no way out from the caller — a thread cannot be killed, and
``ThreadPoolExecutor`` joins its non-daemon workers at interpreter exit, so the
process hangs on the way out regardless of what is done with the futures.

``timeout_sec`` runs the checks in *processes*, which can be terminated. Anything
still outstanding when the budget expires is reported as ``warn: timed out``
rather than dropped, so the sweep still returns a verdict for every other check.

It is opt-in: the gate runs on every commit, and switching its default execution
model changes DB connection fan-out and Windows spawn behaviour for every
caller. These tests pin both that the ceiling works *and* that the default path
is untouched.
"""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


# --------------------------------------------------------------------------
# The timed-out placeholder
# --------------------------------------------------------------------------

def test_timed_out_check_is_a_warn_not_a_fail():
    """A timeout is missing information, not a violation — it must not gate."""
    r = cc._timed_out_check("some_check", 30.0)
    assert r.status == "warn"
    assert r.check_id == "some_check"
    assert "timed out" in r.message
    assert "30s" in r.message


def test_timed_out_check_names_the_budget():
    assert "2.5s" in cc._timed_out_check("x", 2.5).message


# --------------------------------------------------------------------------
# The default path must not change
# --------------------------------------------------------------------------

def _pool_used(monkeypatch, **kwargs):
    """Run run_checks against a trivial registry and record the pool class."""
    seen = {}
    real_thread = concurrent.futures.ThreadPoolExecutor
    real_proc = concurrent.futures.ProcessPoolExecutor

    class Thread(real_thread):
        def __init__(self, *a, **kw):
            seen["cls"] = "thread"
            super().__init__(*a, **kw)

    class Proc(real_proc):
        def __init__(self, *a, **kw):
            seen["cls"] = "process"
            super().__init__(*a, **kw)

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", Thread)
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Proc)
    monkeypatch.setattr(cc, "_check_workers", lambda: 4)
    cc.run_checks(selected=["manifest", "append_only"], **kwargs)
    return seen.get("cls")


def test_default_still_uses_threads(monkeypatch):
    assert _pool_used(monkeypatch) == "thread"


def test_no_timeout_uses_threads(monkeypatch):
    assert _pool_used(monkeypatch, timeout_sec=None) == "thread"


def test_zero_timeout_uses_threads(monkeypatch):
    """0 means 'no ceiling', not 'expire immediately'."""
    assert _pool_used(monkeypatch, timeout_sec=0) == "thread"


def test_timeout_switches_to_processes(monkeypatch):
    assert _pool_used(monkeypatch, timeout_sec=300) == "process"


def test_autofix_ignores_the_timeout(monkeypatch):
    """Autofix is serial by design — fixers mutate the tree.

    It must not be pushed into worker processes, where the fixes would land in a
    child and be lost.
    """
    monkeypatch.setattr(cc, "_check_workers", lambda: 4)
    called = {"proc": False}
    real = concurrent.futures.ProcessPoolExecutor

    class Proc(real):
        def __init__(self, *a, **kw):
            called["proc"] = True
            super().__init__(*a, **kw)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", Proc)
    cc.run_checks(selected=["manifest"], autofix=True, timeout_sec=1)
    assert called["proc"] is False


# --------------------------------------------------------------------------
# The ceiling itself — end to end, against the real registry
# --------------------------------------------------------------------------

@pytest.mark.timeout(300)
def test_tiny_budget_returns_fast_and_reports_every_check(tmp_path):
    """The regression: one slow check must not consume the sweep.

    Run through the CLI because the process path re-imports the module in each
    worker, so an in-process monkeypatched registry would not reach them.
    """
    def run(extra):
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "tools/workflow/coherence_checker.py",
             "--tier", "fast", "--gate", "--changed-files", "tools/rag/toggle_harness.py",
             *extra],
            cwd=str(ROOT), capture_output=True, text=True, timeout=280,
        )
        return json.loads(proc.stdout), time.monotonic() - started

    baseline, baseline_sec = run([])
    capped, capped_sec = run(["--timeout", "2"])

    # Nothing is dropped: a timeout downgrades a check, it does not remove it.
    assert capped["total_checks"] == baseline["total_checks"]
    assert {c["check_id"] for c in capped["checks"]} == {
        c["check_id"] for c in baseline["checks"]
    }

    timed_out = [c for c in capped["checks"] if "timed out" in (c.get("message") or "")]
    assert timed_out, "a 2s budget should not have been enough for every check"
    assert all(c["status"] == "warn" for c in timed_out)

    # And the ceiling actually bounded the run.
    assert capped_sec < max(baseline_sec, 5.0), (
        f"capped run took {capped_sec:.1f}s vs baseline {baseline_sec:.1f}s — "
        "the budget did not bound the sweep"
    )


@pytest.mark.timeout(600)
def test_process_path_agrees_with_the_thread_path():
    """Same verdicts either way — the ceiling must not change the answer."""
    def run(extra):
        proc = subprocess.run(
            [sys.executable, "tools/workflow/coherence_checker.py",
             "--tier", "fast", "--gate", "--changed-files", "tools/rag/toggle_harness.py",
             *extra],
            cwd=str(ROOT), capture_output=True, text=True, timeout=560,
        )
        d = json.loads(proc.stdout)
        return {c["check_id"]: c["status"] for c in d["checks"]}

    assert run([]) == run(["--timeout", "300"])
