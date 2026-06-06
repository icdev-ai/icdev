# CUI // SP-CTI
"""Tests for the limbo-reconcile sweep in tools/genesis/reflexes/kanban.py.

A task with status='scheduled' and scheduled_at IS NULL matches neither
dispatcher query and deadlocks its dependency chain. The reconcile sweep must
heal it to 'backlog' each cycle, while leaving genuinely time-deferred tasks
(non-NULL scheduled_at) untouched.
"""
import sqlite3

import pytest

from tools.db.storage import get_connection
from tools.genesis.reflexes.kanban import _reconcile_limbo_tasks

_DDL = """
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id TEXT PRIMARY KEY, title TEXT, description TEXT, task_type TEXT DEFAULT 'build',
    priority TEXT DEFAULT 'medium', status TEXT DEFAULT 'backlog', scheduled_at TEXT,
    created_at TEXT, updated_at TEXT, completed_at TEXT, depends_on_task_id TEXT,
    failure_count INTEGER DEFAULT 0, last_failure_reason TEXT, completed_via_bypass INTEGER DEFAULT 0,
    project_id TEXT, classification TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS kanban_task_deps (
    task_id TEXT NOT NULL, depends_on_id TEXT NOT NULL, created_at TEXT,
    PRIMARY KEY (task_id, depends_on_id)
);
CREATE TABLE IF NOT EXISTS kanban_status_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, from_status TEXT, to_status TEXT,
    actor TEXT, reason TEXT, created_at TEXT
);
"""


@pytest.fixture(autouse=True)
def _kanban_db(tmp_path, monkeypatch):
    db_path = tmp_path / "reconcile_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    yield


def _insert(tid, status, scheduled_at):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status, scheduled_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (tid, tid, status, scheduled_at),
        )
        conn.commit()


def _status(tid):
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM kanban_tasks WHERE id=?", (tid,)).fetchone()
    return dict(row)["status"] if row else None


def test_scheduled_null_healed_to_backlog():
    _insert("rec-a-01", "scheduled", None)
    healed = _reconcile_limbo_tasks()
    assert "rec-a-01" in healed
    assert _status("rec-a-01") == "backlog"


def test_scheduled_with_future_time_untouched():
    _insert("rec-b-01", "scheduled", "2030-01-01T00:00:00+00:00")
    healed = _reconcile_limbo_tasks()
    assert "rec-b-01" not in healed
    assert _status("rec-b-01") == "scheduled"


def test_backlog_untouched():
    _insert("rec-c-01", "backlog", None)
    healed = _reconcile_limbo_tasks()
    assert "rec-c-01" not in healed
    assert _status("rec-c-01") == "backlog"


def test_idempotent_second_run_noop():
    _insert("rec-d-01", "scheduled", None)
    _reconcile_limbo_tasks()
    healed2 = _reconcile_limbo_tasks()
    assert "rec-d-01" not in healed2
    assert _status("rec-d-01") == "backlog"
