# CUI // SP-CTI
"""Tests for kanban decomposed-parent auto-close (km-autoclose).

Covers all three paths:
  Path 1 — FK depends_on_task_id linkage (existing)
  Path 2 — source_prediction_id linkage (existing)
  Path 3 — ID naming-convention {parent}-d{N} (the bug fix)

Also covers backfill_auto_close_parents pass-2 sweep.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.kanban.state_machine import (
    KanbanState,
    _infer_decomposed_parent_id,
    auto_close_by_naming_convention,
    backfill_auto_close_parents,
    can_transition,
)

# ---------------------------------------------------------------------------
# In-memory SQLite DB fixture (avoids touching the real PostgreSQL)
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE kanban_tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT,
    status        TEXT NOT NULL DEFAULT 'backlog',
    depends_on_task_id TEXT,
    source_prediction_id TEXT,
    completed_at  TEXT,
    updated_at    TEXT
);
CREATE TABLE audit_trail (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    event_type TEXT,
    actor      TEXT,
    action     TEXT,
    details    TEXT
);
"""


class _FakeConn:
    """Thin SQLite wrapper that looks like the storage.py ConnectionWrapper."""

    def __init__(self):
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def execute(self, sql: str, params=None):
        # Translate %s → ? so SQLite accepts the same SQL as the real path
        sql = sql.replace("%s", "?")
        if params is None:
            return self._db.execute(sql)
        return self._db.execute(sql, params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass  # keep alive for test lifetime

    def insert(self, id_, status, depends_on=None, sp_id=None):
        self._db.execute(
            "INSERT INTO kanban_tasks (id, title, status, depends_on_task_id, source_prediction_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (id_, id_, status, depends_on, sp_id),
        )
        self._db.commit()


@pytest.fixture()
def db():
    conn = _FakeConn()
    yield conn
    # auto_close_by_naming_convention documents "Caller commits", so these tests
    # end with an open write transaction. Close the real connection at teardown
    # rather than leaving it for the conftest transaction-leak guard to find.
    conn._db.close()


# ---------------------------------------------------------------------------
# _infer_decomposed_parent_id
# ---------------------------------------------------------------------------

class TestInferDecomposedParentId:
    def test_standard_pattern(self):
        assert _infer_decomposed_parent_id("fd-floor-02-d3") == "fd-floor-02"

    def test_double_digit_suffix(self):
        assert _infer_decomposed_parent_id("my-task-d10") == "my-task"

    def test_no_match_plain_id(self):
        assert _infer_decomposed_parent_id("fd-floor-02") is None

    def test_no_match_no_suffix(self):
        assert _infer_decomposed_parent_id("simple") is None

    def test_no_match_d_not_followed_by_digits(self):
        assert _infer_decomposed_parent_id("task-design") is None


# ---------------------------------------------------------------------------
# DECOMPOSED state + transition
# ---------------------------------------------------------------------------

class TestDecomposedState:
    def test_decomposed_in_enum(self):
        assert KanbanState.DECOMPOSED == "decomposed"

    def test_decomposed_to_done_valid(self):
        assert can_transition(KanbanState.DECOMPOSED, KanbanState.DONE) is True

    def test_decomposed_to_backlog_invalid(self):
        assert can_transition(KanbanState.DECOMPOSED, KanbanState.BACKLOG) is False


# ---------------------------------------------------------------------------
# auto_close_by_naming_convention — Path 3
# ---------------------------------------------------------------------------

class TestAutoCloseByNamingConvention:
    def test_closes_parent_when_all_subtasks_done(self, db):
        db.insert("my-task", "decomposed")
        db.insert("my-task-d1", "done")
        db.insert("my-task-d2", "done")
        db.insert("my-task-d3", "done")

        result = auto_close_by_naming_convention("my-task-d3", db)

        assert result is not None
        assert result.applied is True
        assert result.task_id == "my-task"
        row = db.execute("SELECT status FROM kanban_tasks WHERE id = 'my-task'").fetchone()
        assert row["status"] == "done"

    def test_noop_when_not_all_subtasks_done(self, db):
        db.insert("my-task", "decomposed")
        db.insert("my-task-d1", "done")
        db.insert("my-task-d2", "in_progress")  # still open

        result = auto_close_by_naming_convention("my-task-d1", db)
        assert result is None

    def test_noop_when_parent_not_decomposed(self, db):
        db.insert("my-task", "in_progress")  # not decomposed
        db.insert("my-task-d1", "done")

        result = auto_close_by_naming_convention("my-task-d1", db)
        assert result is None

    def test_noop_when_parent_already_done(self, db):
        db.insert("my-task", "done")
        db.insert("my-task-d1", "done")

        result = auto_close_by_naming_convention("my-task-d1", db)
        assert result is None

    def test_noop_when_child_id_has_no_convention(self, db):
        result = auto_close_by_naming_convention("plain-task-id", db)
        assert result is None

    def test_noop_when_parent_not_in_db(self, db):
        db.insert("my-task-d1", "done")
        result = auto_close_by_naming_convention("my-task-d1", db)
        assert result is None

    def test_closes_only_when_last_subtask_completes(self, db):
        db.insert("proj-d1", "done")
        db.insert("proj-d2", "done")
        db.insert("proj-d3", "done")
        db.insert("proj", "decomposed")

        # Calling from d1 should also work (all siblings already done)
        result = auto_close_by_naming_convention("proj-d1", db)
        assert result is not None and result.applied

    def test_fd_floor_02_scenario(self, db):
        """Reproduces the exact fd-floor-02 bug."""
        db.insert("fd-floor-02", "decomposed", depends_on="fd-floor-01")
        db.insert("fd-floor-02-d1", "done", depends_on=None)
        db.insert("fd-floor-02-d2", "done", depends_on="fd-floor-02-d1")
        db.insert("fd-floor-02-d3", "done", depends_on="fd-floor-02-d2")
        db.insert("fd-floor-02-d4", "done", depends_on="fd-floor-02-d3")

        result = auto_close_by_naming_convention("fd-floor-02-d4", db)

        assert result is not None
        assert result.applied is True
        assert result.task_id == "fd-floor-02"
        row = db.execute("SELECT status FROM kanban_tasks WHERE id = 'fd-floor-02'").fetchone()
        assert row["status"] == "done"


# ---------------------------------------------------------------------------
# backfill_auto_close_parents — pass-2 naming-convention sweep
# ---------------------------------------------------------------------------

class TestBackfillAutoCloseParents:
    def test_pass2_closes_decomposed_parents_by_naming(self, db):
        db.insert("alpha", "decomposed")
        db.insert("alpha-d1", "done")
        db.insert("alpha-d2", "done")

        # beta still has open subtasks — should NOT be closed
        db.insert("beta", "decomposed")
        db.insert("beta-d1", "done")
        db.insert("beta-d2", "backlog")

        closed = backfill_auto_close_parents(db, actor="test")

        assert "alpha" in closed
        assert "beta" not in closed

        alpha_status = db.execute("SELECT status FROM kanban_tasks WHERE id = 'alpha'").fetchone()["status"]
        assert alpha_status == "done"

        beta_status = db.execute("SELECT status FROM kanban_tasks WHERE id = 'beta'").fetchone()["status"]
        assert beta_status == "decomposed"  # unchanged

    def test_pass2_no_double_close(self, db):
        db.insert("gamma", "decomposed")
        db.insert("gamma-d1", "done")

        closed = backfill_auto_close_parents(db, actor="test")
        assert closed.count("gamma") == 1  # closed exactly once

    def test_pass2_ignores_already_done_parents(self, db):
        db.insert("delta", "done")
        db.insert("delta-d1", "done")

        closed = backfill_auto_close_parents(db, actor="test")
        assert "delta" not in closed
