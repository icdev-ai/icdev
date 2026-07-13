# CUI // SP-CTI
"""Regression — _move_task(tid, "scheduled") MUST populate scheduled_at.

The kanban scheduler's due-task query (`_get_due_tasks` in
tools/genesis/reflexes/kanban.py) is gated on:

    status = 'scheduled'
    AND scheduled_at IS NOT NULL
    AND scheduled_at <= NOW()

Any caller that moves a task to 'scheduled' without populating
scheduled_at produces an invisible row — the row never satisfies the
dispatch query and the task hangs forever, no alert fired.

This was the underlying cause of multiple silent-failure incidents
in 2026-04-17:

    1. Cascade rollback (kanban.py _move_task itself when called as
       _move_task(child, "scheduled") from the cascade path) — fixed
       earlier by routing descendants to 'backlog' instead.
    2. Orphan-sweep (kanban.py:1523) — orphan_sweep calls
       _move_task(o["id"], "scheduled"). dt-iqe-12 hit this exactly.
    3. Future callers — anyone adding a new code path that moves to
       'scheduled' would re-introduce the same invisible-row bug.

The root-cause fix sets scheduled_at = now whenever _move_task
transitions to 'scheduled'. This test pins the contract.
"""
from __future__ import annotations

import sqlite3

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
    recorded_at        TEXT
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


@pytest.fixture
def kanban_db(tmp_path, monkeypatch):
    db_path = tmp_path / "k.db"
    c = sqlite3.connect(str(db_path))
    c.executescript(_SCHEMA)
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
    monkeypatch.setattr(kanban_mod, "_record_status_transition",
                        lambda *a, **k: None)

    return db_path, kanban_mod


def _insert(db_path, tid, status="backlog", scheduled_at=None):
    c = sqlite3.connect(str(db_path))
    now = "2026-04-17T22:00:00+00:00"
    c.execute(
        "INSERT INTO kanban_tasks "
        "(id, title, status, scheduled_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tid, f"task {tid}", status, scheduled_at, now, now),
    )
    c.commit()
    c.close()


def _read(db_path, tid):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone()
    c.close()
    return dict(r) if r else None


class TestMoveToScheduled:
    """The core invariant — moving to 'scheduled' must populate
    scheduled_at so the dispatch query can see the row."""

    def test_move_to_scheduled_populates_scheduled_at(self, kanban_db):
        db, km = kanban_db
        _insert(db, "t1", status="in_progress")  # any non-scheduled status
        km._move_task("t1", "scheduled")
        t = _read(db, "t1")
        assert t["status"] == "scheduled"
        assert t["scheduled_at"] is not None, (
            "scheduled_at MUST be populated when moving to 'scheduled' "
            "— otherwise the row is invisible to _get_due_tasks"
        )

    def test_move_to_scheduled_from_done_via_orphan_sweep(self, kanban_db):
        """Repro of the dt-iqe-12 incident — orphan sweep moves a
        previously-done task back to scheduled because its parent isn't
        done. The fix must produce a dispatchable row, not an invisible
        one."""
        db, km = kanban_db
        _insert(db, "dt-iqe-12", status="done")
        km._move_task(
            "dt-iqe-12", "scheduled",
            actor="orphan_sweep",
            reason="parent dt-iqe-11 status='backlog' at sweep",
        )
        t = _read(db, "dt-iqe-12")
        assert t["status"] == "scheduled"
        assert t["scheduled_at"] is not None, (
            "the silent-failure bug — orphan_sweep moved task to "
            "'scheduled' without populating scheduled_at, making it "
            "invisible to the dispatcher"
        )
        # Verify scheduled_at is in the past (or right now), not future,
        # so the dispatcher's `<= NOW()` clause would match.
        from datetime import datetime, timezone
        sa = datetime.fromisoformat(t["scheduled_at"].replace("Z", "+00:00"))
        # Normalize to UTC if naive
        if sa.tzinfo is None:
            sa = sa.replace(tzinfo=timezone.utc)
        assert sa <= datetime.now(timezone.utc), (
            f"scheduled_at {sa} should be in the past, not future"
        )


class TestOtherTransitionsUnchanged:
    """The fix must NOT alter behavior of other transitions."""

    def test_move_to_done_still_sets_completed_at(self, kanban_db):
        db, km = kanban_db
        _insert(db, "t-done", status="in_progress")
        km._move_task("t-done", "done")
        t = _read(db, "t-done")
        assert t["status"] == "done"
        assert t["completed_at"] is not None

    def test_move_to_backlog_does_not_touch_scheduled_at(self, kanban_db):
        """Moving to 'backlog' should NOT populate scheduled_at, AND if
        scheduled_at was previously set (e.g. revert from scheduled →
        backlog) the value should not be cleared by this function — that
        is the responsibility of the cascade-rollback path which
        explicitly NULLs it."""
        db, km = kanban_db
        _insert(db, "t-back", status="scheduled",
                scheduled_at="2026-04-17T20:00:00+00:00")
        km._move_task("t-back", "backlog")
        t = _read(db, "t-back")
        assert t["status"] == "backlog"
        # scheduled_at preserved (this transition doesn't manage it)
        assert t["scheduled_at"] == "2026-04-17T20:00:00+00:00"

    def test_move_to_in_progress_does_not_set_scheduled_at(self, kanban_db):
        db, km = kanban_db
        _insert(db, "t-ip", status="scheduled",
                scheduled_at="2026-04-17T20:00:00+00:00")
        km._move_task("t-ip", "in_progress")
        t = _read(db, "t-ip")
        assert t["status"] == "in_progress"
        # No new scheduled_at write; preserves whatever was there
        assert t["scheduled_at"] == "2026-04-17T20:00:00+00:00"
