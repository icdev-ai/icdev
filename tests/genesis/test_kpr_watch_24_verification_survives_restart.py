# CUI // SP-CTI
"""kpr-watch-24: every pr_opened task gets a REAL verification.

MEASURED 2026-10-10: 10 of 16 omx tasks needed a human `--reverify` before their
green PR could merge. The dispatch verifier never ran for them -- a scheduler
re-exec (or a timeout/reaper hand-off) moved the task to pr_opened by a door that
skips `_write_verification_log`. Two halves:

* drain: `restart_if_code_changed` defers while the caller is busy (bounded), and
  the reflex dispatches nothing new while draining;
* adoption: a pr_opened task with no verification row at all gets the same
  `_run_full_verification` the completion path runs.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from tools.genesis import code_reload as cr
from tools.genesis.reflexes import kanban

ROOT = Path(__file__).resolve().parents[2]


class _Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, exe, argv):
        self.calls.append((exe, argv))


@pytest.fixture(autouse=True)
def _reset_drain_state(monkeypatch):
    monkeypatch.setattr(cr, "_busy_since", None)
    yield


def _stale():
    # Every loaded file at mtime 0 reads as rewritten.
    return {path: 0.0 for path in cr.snapshot(ROOT)}


def _restart(ex, **kw):
    return cr.restart_if_code_changed(
        _stale(), started_at=time.time() - 10_000, execv=ex, root=ROOT,
        pull=False, **kw)


# --------------------------------------------------------------------- drain

def test_a_busy_caller_defers_the_restart_and_drains():
    ex = _Exec()
    changed = _restart(ex, busy=lambda: True, max_defer=3600)
    assert ex.calls == [], "re-exec while workers are in flight orphans them"
    assert changed, "it still reports what changed"
    assert cr.is_draining()


def test_an_idle_caller_restarts_and_stops_draining(monkeypatch):
    monkeypatch.setattr(cr, "_busy_since", time.time() - 5)
    ex = _Exec()
    _restart(ex, busy=lambda: False, max_defer=3600)
    assert ex.calls, "idle after a drain must re-exec"
    assert not cr.is_draining()


def test_the_drain_is_bounded_by_max_defer(monkeypatch):
    monkeypatch.setattr(cr, "_busy_since", time.time() - 7300)
    ex = _Exec()
    _restart(ex, busy=lambda: True, max_defer=7200)
    assert ex.calls, "a worker that never finishes must not pin stale code forever"


def test_a_broken_busy_probe_counts_as_idle():
    def boom():
        raise RuntimeError("probe broke")
    ex = _Exec()
    _restart(ex, busy=boom, max_defer=3600)
    assert ex.calls


def test_no_busy_probe_keeps_the_old_behaviour():
    ex = _Exec()
    _restart(ex)
    assert ex.calls
    assert not cr.is_draining()


def test_the_reflex_returns_before_dispatch_while_draining():
    # Structural: the cycle (_run_cycle, behind run()) is ~1,000 lines with a
    # dozen DB-backed sweeps, so the order is asserted on the source -- the
    # drain return must come before EVERY dispatch call (the token-retry
    # re-dispatch and the due-task one), and the adoption sweep before the
    # drain so it keeps verifying meanwhile.
    src = Path(kanban.__file__).read_text(encoding="utf-8")
    run_src = src[src.index("\ndef _run_cycle(config"):]
    end = run_src.find("\ndef ", 1)  # _run_cycle may be the last function
    run_src = run_src if end < 0 else run_src[:end]
    drain = run_src.index("_code_reload.is_draining()")
    dispatches = [m.start() for m in re.finditer(r"_dispatch_to_claude\(", run_src)]
    assert dispatches, "run() no longer dispatches through _dispatch_to_claude"
    assert drain < min(dispatches)
    assert run_src.index("_verify_adopted_pr_tasks()") < drain
    assert '"status": "draining_for_restart"' in run_src


def test_the_scheduler_passes_in_flight_workers_as_busy():
    src = (ROOT / "tools" / "genesis" / "kanban_scheduler.py").read_text(encoding="utf-8")
    call = src[src.index("code_reload.restart_if_code_changed("):]
    call = call[:call.index("\n\n")]
    assert "busy=lambda: bool(_kanban_mod._running)" in call
    assert "max_defer=" in call


# ------------------------------------------------------------------ adoption

class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        return _Rows(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def adoption(monkeypatch, tmp_path):
    calls = {"verify": [], "log": [], "metrics": []}
    monkeypatch.setattr(kanban, "_ADOPTION_ATTEMPTED", set())
    monkeypatch.setattr(kanban, "_running", {})
    monkeypatch.setattr(kanban, "BASE_DIR", tmp_path)
    (tmp_path / ".tmp" / "kanban").mkdir(parents=True)

    def verify(task_id, output):
        calls["verify"].append((task_id, output))
        return True, "Verified (git-first): 3 file(s)", {"codelens_passed": True}

    monkeypatch.setattr(kanban, "_run_full_verification", verify)
    monkeypatch.setattr(kanban, "_write_verification_log",
                        lambda t, v, r: calls["log"].append((t, v, r)))
    monkeypatch.setattr(kanban, "_update_verification_metrics",
                        lambda t, m: calls["metrics"].append((t, m)))
    return calls


def _rows(monkeypatch, ids):
    conn = _Conn([{"id": i} for i in ids])
    monkeypatch.setattr(kanban, "get_connection", lambda: conn)
    return conn


def test_an_unverified_pr_opened_task_gets_a_real_verification(adoption, monkeypatch, tmp_path):
    (tmp_path / ".tmp" / "kanban" / "omx-x-01.log").write_text("worker summary", encoding="utf-8")
    conn = _rows(monkeypatch, ["omx-x-01"])

    assert kanban._verify_adopted_pr_tasks(limit=1) == ["omx-x-01"]

    assert adoption["verify"] == [("omx-x-01", "worker summary")]
    task, verified, reason = adoption["log"][0]
    assert (task, verified) == ("omx-x-01", True)
    assert "adopted" in reason
    assert adoption["metrics"] == [("omx-x-01", {"codelens_passed": True})]
    sql = conn.sql[0]
    assert "status = 'pr_opened'" in sql
    assert "NOT EXISTS" in sql and "kanban_verifications" in sql


def test_a_worker_this_process_is_running_is_left_to_its_completion_path(adoption, monkeypatch):
    monkeypatch.setattr(kanban, "_running", {"omx-x-01": object()})
    _rows(monkeypatch, ["omx-x-01"])
    assert kanban._verify_adopted_pr_tasks(limit=5) == []
    assert adoption["verify"] == []


def test_the_sweep_is_bounded_and_attempts_a_task_once(adoption, monkeypatch):
    _rows(monkeypatch, ["a-1", "a-2", "a-3"])
    assert kanban._verify_adopted_pr_tasks(limit=2) == ["a-1", "a-2"]
    assert kanban._verify_adopted_pr_tasks(limit=2) == ["a-3"]
    assert kanban._verify_adopted_pr_tasks(limit=2) == []


def test_a_missing_worker_log_still_verifies(adoption, monkeypatch):
    _rows(monkeypatch, ["omx-x-02"])
    kanban._verify_adopted_pr_tasks(limit=1)
    assert adoption["verify"] == [("omx-x-02", "")]
