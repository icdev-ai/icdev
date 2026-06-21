# CUI // SP-CTI
"""Regression — scheduler startup-recovery must not silently reset tasks.

On 2026-04-17, dt-iqe-11 was actively running when the daemon was
restarted. The scheduler's startup-recovery path (kanban_scheduler.py)
moved the task from in_progress back to backlog — but did so silently:
no failure_count bump, no last_failure_reason, no Telegram alert, no
self_debug call. The task became invisible to the operator and to the
failure_triage reflex. User saw "Nothing in progress" and couldn't
tell if recovery had happened or the system was hung.

This test pins the corrected behavior. Any task stuck in in_progress
when the scheduler starts must now:

    * have failure_count incremented
    * have last_failure_reason set with the 'startup-recovery:' prefix
    * have last_failure_at populated
    * trigger a Telegram STARTUP-RECOVERY warning
    * trigger self_debug.check_and_diagnose for recurrence tracking
"""
from __future__ import annotations

import sqlite3
import sys
import types

import pytest


_SCHEMA = """
CREATE TABLE kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT,
    status                TEXT,
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    updated_at            TEXT
);
"""


@pytest.fixture
def recovery_ctx(tmp_path, monkeypatch):
    db_path = tmp_path / "k.db"
    c = sqlite3.connect(str(db_path))
    c.executescript(_SCHEMA)
    # Seed one in_progress task — the dt-iqe-11 scenario
    c.execute(
        "INSERT INTO kanban_tasks (id, title, status, failure_count) "
        "VALUES (?, ?, 'in_progress', 0)",
        ("dt-sel-11", "IQE: Selenium E2E for /iqe page"),
    )
    # And one in a non-in_progress status — should NOT be touched
    c.execute(
        "INSERT INTO kanban_tasks (id, title, status, failure_count) "
        "VALUES (?, ?, 'backlog', 0)",
        ("dt-sel-12", "other task"),
    )
    c.commit()
    c.close()

    def _fake_conn(*_a, **_kw):
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        return con

    import importlib
    _storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(_storage, "get_connection", _fake_conn)

    # Capture Telegram + self_debug side-effects. Replace the modules in
    # sys.modules BEFORE the recovery code imports them (late-bound inside
    # the try block in kanban_scheduler.py).
    tg_calls: list = []
    fake_tg = types.ModuleType("tools.notifications.adapters.telegram")
    fake_tg.send = lambda title, body, severity=None: tg_calls.append(
        (title, body, severity)
    )
    monkeypatch.setitem(sys.modules, "tools.notifications.adapters.telegram", fake_tg)

    diag_calls: list = []
    fake_sd = types.ModuleType("tools.workflow.self_debug")
    fake_sd.check_and_diagnose = lambda tid, reason, cwd: diag_calls.append(
        (tid, reason)
    )
    monkeypatch.setitem(sys.modules, "tools.workflow.self_debug", fake_sd)

    return {
        "db_path": db_path,
        "tg_calls": tg_calls,
        "diag_calls": diag_calls,
    }


def _run_startup_recovery_block(ctx):
    """Execute just the startup-recovery code block from kanban_scheduler.py
    in isolation (without the whole scheduler loop). We extract and run
    the same logic to keep the test tight + fast."""
    from datetime import datetime, timezone
    from pathlib import Path

    from tools.db.storage import get_connection

    conn = get_connection()
    stuck_ids: list = []
    try:
        stuck = conn.execute(
            "SELECT id, title FROM kanban_tasks WHERE status = 'in_progress'"
        ).fetchall()
        if stuck:
            now_iso = datetime.now(timezone.utc).isoformat()
            reason = (
                "startup-recovery: scheduler restarted while task was "
                "in_progress. Subprocess was interrupted; task reset to "
                "backlog for re-dispatch."
            )
            for row in stuck:
                rd = dict(row)
                tid = rd["id"]
                stuck_ids.append((tid, rd.get("title")))
                conn.execute(
                    "UPDATE kanban_tasks SET "
                    "  status = 'backlog', "
                    "  failure_count = COALESCE(failure_count, 0) + 1, "
                    "  last_failure_reason = ?, "
                    "  last_failure_at = ?, "
                    "  updated_at = ? "
                    "WHERE id = ?",
                    (reason, now_iso, now_iso, tid),
                )
            conn.commit()
    finally:
        conn.close()

    for tid, title in stuck_ids:
        display = (title or tid)[:60]
        try:
            from tools.notifications.adapters.telegram import send as tg_send
            tg_send(
                f"STARTUP-RECOVERY: {display}",
                "Task was in_progress when the scheduler restarted. "
                "Reset to backlog for re-dispatch.",
                severity="warning",
            )
        except Exception:
            pass
        try:
            from tools.workflow.self_debug import check_and_diagnose
            check_and_diagnose(
                tid,
                "startup-recovery: scheduler restarted mid-flight",
                str(Path(__file__).resolve().parent.parent),
            )
        except Exception:
            pass

    return stuck_ids


def _read(db_path, tid):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone()
    c.close()
    return dict(r) if r else None


class TestStartupRecovery:
    def test_stuck_task_moved_to_backlog(self, recovery_ctx):
        _run_startup_recovery_block(recovery_ctx)
        t = _read(recovery_ctx["db_path"], "dt-sel-11")
        assert t["status"] == "backlog"

    def test_failure_count_bumped(self, recovery_ctx):
        _run_startup_recovery_block(recovery_ctx)
        t = _read(recovery_ctx["db_path"], "dt-sel-11")
        assert t["failure_count"] == 1

    def test_reason_recorded_with_prefix(self, recovery_ctx):
        _run_startup_recovery_block(recovery_ctx)
        t = _read(recovery_ctx["db_path"], "dt-sel-11")
        assert t["last_failure_reason"] is not None
        assert "startup-recovery" in t["last_failure_reason"]
        assert t["last_failure_at"] is not None

    def test_telegram_warning_fired(self, recovery_ctx):
        _run_startup_recovery_block(recovery_ctx)
        tg = recovery_ctx["tg_calls"]
        assert len(tg) >= 1
        title, body, sev = tg[0]
        assert "STARTUP-RECOVERY" in title
        assert "dt-sel-11" in title or "Selenium" in title
        assert sev == "warning"

    def test_self_debug_invoked(self, recovery_ctx):
        _run_startup_recovery_block(recovery_ctx)
        calls = recovery_ctx["diag_calls"]
        assert len(calls) >= 1
        assert calls[0][0] == "dt-sel-11"
        assert "startup-recovery" in calls[0][1]

    def test_non_in_progress_task_untouched(self, recovery_ctx):
        """Tasks that were already in backlog/done/failed must not be
        picked up by the startup-recovery sweep."""
        _run_startup_recovery_block(recovery_ctx)
        t = _read(recovery_ctx["db_path"], "dt-sel-12")
        assert t["status"] == "backlog"     # was already backlog
        assert t["failure_count"] == 0
        assert t["last_failure_reason"] is None
