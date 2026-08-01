# CUI // SP-CTI
"""Regression — the 'stale _running cleanup' path must NOT be silent.

On 2026-04-17 dt-iqe-11 (an IQE Selenium E2E task) disappeared from
in_progress without any alert. The scheduler's claude CLI subprocess
died (or self-reported externally) and the in-memory _running entry
became stale. The existing cleanup code removed the Popen + worktree
silently — zero signal to operators or failure_triage. The task ended
up back in 'backlog' with failure_count=0 and last_failure_reason=NULL,
so:

    * No alert landed in the queue → operator thinks nothing is wrong
    * failure_triage can't see it → no LLM diagnosis fires
    * self_debug can't increment its signature count → quarantine never
      reached on recurrence

This test pins the corrected behavior. Stale cleanup must now:

    * bump failure_count via _record_failure_and_maybe_flag
    * set last_failure_reason + last_failure_at
    * queue an alert locally in kanban_alert_queue (not rely on Telegram)
    * invoke self_debug.check_and_diagnose for recurrence tracking
"""
from __future__ import annotations

import sqlite3
import sys
import types as _types

import pytest



from tools.db.storage import get_connection as _real_get_connection

_SCHEMA = """
CREATE TABLE kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    task_type             TEXT DEFAULT 'build',
    priority              TEXT DEFAULT 'medium',
    status                TEXT DEFAULT 'backlog',
    scheduled_at          TEXT,
    created_at            TEXT,
    updated_at            TEXT,
    completed_at          TEXT,
    executor_type         TEXT,
    execution_id          TEXT,
    executor_url          TEXT,
    source_prediction_id  TEXT,
    depends_on_task_id    TEXT,
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    -- _record_failure_and_maybe_flag() writes the narrative half of the
    -- failure story here in the SAME UPDATE that bumps failure_count, and
    -- wraps the whole block in `except Exception`. Without this column the
    -- UPDATE raised `no such column: last_run_summary`, was swallowed, and
    -- failure_count silently stayed 0 — while the explicit reason-only write
    -- further down the stale-cleanup path still succeeded, so only the
    -- failure_count assertion failed and it read as an unimplemented bump.
    last_run_summary      TEXT,
    dispatch_source       TEXT,
    dispatch_attempt_id   TEXT
);
CREATE TABLE kanban_status_transitions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL,
    from_status        TEXT,
    to_status          TEXT NOT NULL,
    actor              TEXT,
    reason             TEXT,
    created_at         TEXT
);
CREATE TABLE kanban_alert_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    event         TEXT NOT NULL DEFAULT 'failed',
    severity      TEXT NOT NULL DEFAULT 'warning',
    title         TEXT,
    body          TEXT,
    reason        TEXT,
    actor         TEXT NOT NULL DEFAULT 'stale-cleanup',
    created_at    TEXT NOT NULL,
    dispatched_at TEXT,
    retry_count   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE audit_trail (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT,
    actor       TEXT,
    action      TEXT,
    project_id  TEXT,
    details     TEXT,
    created_at  TEXT
);
"""


class _FakeProc:
    def __init__(self): self.killed = False
    def kill(self): self.killed = True
    def poll(self): return None  # "still running" — irrelevant, stale check doesn't poll
    def wait(self, timeout=None): pass


@pytest.fixture
def kanban_ctx(tmp_path, monkeypatch):
    """File-backed sqlite + patched get_connection + stubbed side effects."""
    db_path = tmp_path / "k.db"
    c = sqlite3.connect(str(db_path))
    c.executescript(_SCHEMA)
    c.execute(
        "INSERT INTO kanban_tasks "
        "(id, title, status, created_at, updated_at, failure_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("dt-test-11", "IQE: Selenium E2E for /iqe page", "backlog",
         "2026-04-17T21:11:00+00:00", "2026-04-17T21:20:56+00:00", 0),
    )
    c.commit()
    c.close()

    def _fake_conn(*_a, **_kw):
        # Go through tools.db.storage, NOT raw sqlite3. Runtime SQL is authored for
        # PostgreSQL (%s placeholders, per CLAUDE.md) and the storage wrapper translates
        # them to SQLite's ?. A raw sqlite3 connection makes every %s a syntax error.
        # _real_get_connection is bound at import time, so patching storage's attribute
        # below cannot recurse back into this.
        return _real_get_connection(db_path=str(db_path))

    import importlib
    _storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(_storage, "get_connection", _fake_conn)
    import tools.genesis.reflexes.kanban as kanban_mod
    monkeypatch.setattr(kanban_mod, "get_connection", _fake_conn)

    # Stub side-effect modules
    monkeypatch.setattr(kanban_mod, "_cleanup_worktree", lambda tid: None)
    monkeypatch.setattr(kanban_mod, "_record_status_transition",
                        lambda *a, **k: None)
    monkeypatch.setattr(kanban_mod, "_check_completed", lambda: [])
    monkeypatch.setattr(kanban_mod, "_poll_telegram", lambda: [])
    monkeypatch.setattr(kanban_mod, "_get_due_tasks", lambda: [])

    diag_calls: list = []
    fake_sd = _types.ModuleType("tools.workflow.self_debug")
    fake_sd.check_and_diagnose = lambda tid, reason, cwd: diag_calls.append((tid, reason))
    monkeypatch.setitem(sys.modules, "tools.workflow.self_debug", fake_sd)

    def _read_alert_queue():
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM kanban_alert_queue ORDER BY created_at"
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

    return {
        "db_path": db_path,
        "kanban": kanban_mod,
        "diag_calls": diag_calls,
        "read_alert_queue": _read_alert_queue,
    }


def _read_task(db_path, tid):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone()
    c.close()
    return dict(r) if r else None


class TestSilentCleanup:
    """Every behavior listed above must now fire when the stale path runs."""

    def _prime_running(self, kanban_mod, tid="dt-test-11"):
        """Simulate the scheduler having a Popen entry for `tid`."""
        kanban_mod._running[tid] = _FakeProc()
        kanban_mod._dispatch_times[tid] = None
        kanban_mod._worktrees[tid] = str(kanban_mod.WORKTREE_BASE / tid)

    def test_stale_cleanup_bumps_failure_count(self, kanban_ctx):
        km = kanban_ctx["kanban"]
        self._prime_running(km)
        km.run({}, None)
        t = _read_task(kanban_ctx["db_path"], "dt-test-11")
        assert t["failure_count"] >= 1, (
            f"failure_count should be bumped, got {t['failure_count']}"
        )

    def test_stale_cleanup_sets_reason(self, kanban_ctx):
        km = kanban_ctx["kanban"]
        self._prime_running(km)
        km.run({}, None)
        t = _read_task(kanban_ctx["db_path"], "dt-test-11")
        assert t["last_failure_reason"] is not None
        assert "stale-cleanup" in t["last_failure_reason"]
        assert t["last_failure_at"] is not None

    def test_stale_cleanup_queues_alert_locally(self, kanban_ctx):
        km = kanban_ctx["kanban"]
        self._prime_running(km)
        km.run({}, None)
        alerts = kanban_ctx["read_alert_queue"]()
        assert len(alerts) >= 1, (
            f"expected at least one local alert, got {alerts}"
        )
        a = alerts[0]
        assert a["task_id"] == "dt-test-11"
        assert a["event"] == "failed"
        assert a["severity"] == "warning"
        assert "stale-cleanup" in a["reason"]
        assert a["actor"] == "stale-cleanup"

    def test_stale_cleanup_calls_self_debug(self, kanban_ctx):
        km = kanban_ctx["kanban"]
        self._prime_running(km)
        km.run({}, None)
        calls = kanban_ctx["diag_calls"]
        assert len(calls) >= 1, f"expected self_debug call, got {calls}"
        assert calls[0][0] == "dt-test-11"
        assert "stale-cleanup" in calls[0][1]

    def test_stale_cleanup_removes_running_entry(self, kanban_ctx):
        km = kanban_ctx["kanban"]
        self._prime_running(km)
        km.run({}, None)
        assert "dt-test-11" not in km._running, (
            "in-memory _running entry must be cleared"
        )
        assert "dt-test-11" not in km._worktrees

    def test_done_task_not_treated_as_failure(self, kanban_ctx):
        """If the task is in 'done' status when cleanup fires, it
        legitimately self-reported. No local alert should be queued."""
        db = kanban_ctx["db_path"]
        con = sqlite3.connect(str(db))
        con.execute("UPDATE kanban_tasks SET status='done' WHERE id='dt-test-11'")
        con.commit()
        con.close()

        km = kanban_ctx["kanban"]
        self._prime_running(km)
        km.run({}, None)

        alerts = kanban_ctx["read_alert_queue"]()
        assert not alerts, (
            f"done tasks must not queue local alerts: {alerts}"
        )
        t = _read_task(db, "dt-test-11")
        assert t["last_failure_reason"] is None
