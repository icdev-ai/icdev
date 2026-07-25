# CUI // SP-CTI
"""MCP kanban_delete_task must hard-DELETE the row, not write an invalid status.

The bug: handle_kanban_delete_task did

    UPDATE kanban_tasks SET status = 'archived' WHERE id = %s

but 'archived' is NOT in the PostgreSQL kanban_tasks_status_check CHECK
constraint (valid: backlog, scheduled, in_progress, done, token_exhausted,
suggested, decomposed, validating, needs_decomposition, pr_opened, ci_failed,
merge_conflict, changes_requested, failed). In production (PG) that raised
CheckViolation and the delete silently failed — no row ever changed.

The fix matches the dashboard's canonical delete
(tools/dashboard/api/kanban.py::delete_task): a real hard DELETE, plus the
manual-gate guard that refuses to strand a sentinel's dependents.

These tests are behavioral: SQLite (the conftest backend) does not enforce the
exact PG CHECK, so we assert the row is actually removed. Under the OLD code the
row would still exist (merely status-mutated), so this test fails on the old path.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

from tools.mcp.kanban_server import handle_kanban_delete_task


class _FakeCursor:
    """Cursor that translates the PG %s placeholder to SQLite's ?."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        self._cur.execute(sql.replace("%s", "?"), params or ())
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _FakeConn:
    """Thin wrapper over a persistent in-memory SQLite conn.

    close() is a no-op so the test can still query the same DB after the handler
    (which calls conn.close()) returns.
    """

    def __init__(self, db):
        self._db = db

    def cursor(self):
        return _FakeCursor(self._db.cursor())

    def commit(self):
        self._db.commit()

    def close(self):
        pass  # keep the connection alive for post-call assertions


@pytest.fixture
def kanban_conn(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute(
        """
        CREATE TABLE kanban_tasks (
            id     TEXT PRIMARY KEY,
            title  TEXT,
            status TEXT NOT NULL DEFAULT 'backlog'
        )
        """
    )
    db.commit()
    fake = _FakeConn(db)
    # The handler does `from tools.db.storage import get_connection` at call time,
    # so patch get_connection on the exact module it resolves. The tools.* shim
    # aliases to icdev.tools.*, so use importlib.import_module + setattr rather
    # than `import tools.db.storage as storage` (which the shim rejects).
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: fake)
    return db


def _seed(db, task_id, title="A task", status="backlog"):
    db.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
        (task_id, title, status),
    )
    db.commit()


def _count(db, task_id):
    return db.execute(
        "SELECT COUNT(*) FROM kanban_tasks WHERE id = ?", (task_id,)
    ).fetchone()[0]


def test_delete_task_hard_removes_the_row(kanban_conn):
    _seed(kanban_conn, "tst-del-01")
    result = handle_kanban_delete_task({"task_id": "tst-del-01"})

    assert "error" not in result, result
    assert result.get("deleted") == "tst-del-01"
    # The row is GONE — not merely re-statused to an invalid 'archived'.
    assert _count(kanban_conn, "tst-del-01") == 0


def test_delete_task_accepts_id_alias(kanban_conn):
    _seed(kanban_conn, "tst-del-02")
    result = handle_kanban_delete_task({"id": "tst-del-02"})
    assert result.get("deleted") == "tst-del-02"
    assert _count(kanban_conn, "tst-del-02") == 0


def test_delete_task_never_writes_archived_status(kanban_conn):
    """Guard against a regression to the invalid-status UPDATE: no surviving row
    may carry the 'archived' status the CHECK constraint rejects."""
    _seed(kanban_conn, "tst-del-03")
    handle_kanban_delete_task({"task_id": "tst-del-03"})
    archived = kanban_conn.execute(
        "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'archived'"
    ).fetchone()[0]
    assert archived == 0


def test_delete_missing_task_returns_error(kanban_conn):
    result = handle_kanban_delete_task({"task_id": "does-not-exist"})
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_delete_manual_gate_is_refused(kanban_conn):
    """A manual-mode gate sentinel must not be deletable — deleting it strands
    its dependents. Matches tools/dashboard/api/kanban.py::delete_task."""
    _seed(kanban_conn, "prem-gate-00", title="MANUAL-MODE GATE: premium suite")
    result = handle_kanban_delete_task({"task_id": "prem-gate-00"})
    assert "error" in result
    assert "gate" in result["error"].lower()
    # The gate row survives.
    assert _count(kanban_conn, "prem-gate-00") == 1


def test_missing_task_id_returns_error(kanban_conn):
    result = handle_kanban_delete_task({})
    assert result.get("error") == "task_id is required"
