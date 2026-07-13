# CUI // SP-CTI
"""Regression — cascade rollback must not produce invisible rows.

On 2026-04-17 an 82-task chain (the Digital Twin roadmap) silently
stopped progressing for ~2 hours. Root cause: when a parent task was
demoted from 'done' to 'not-done', its active descendants were moved to
status='scheduled' WITHOUT setting scheduled_at. The kanban scheduler's
due-task query requires BOTH `status='scheduled'` AND `scheduled_at IS
NOT NULL AND scheduled_at <= NOW()`, so the rolled-back row became
invisible to the dispatcher. Scheduler logged "idle (no due tasks)" on
every tick while the chain stayed frozen.

This test pins the corrected behavior:
  * cascade demotion moves active descendants to 'backlog', not
    'scheduled'
  * scheduled_at is explicitly NULL-ed (defensive — in case it was set
    earlier)
  * the 10-minute backlog cooldown (via updated_at) handles rapid-retry
    prevention
  * the dep_clause in _get_due_tasks prevents descendants from being
    dispatched before the parent reaches 'done' again
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
    created_at         TEXT
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
    """A file-backed sqlite DB with the minimal kanban schema + patched
    get_connection so _move_task writes to it."""
    db_path = tmp_path / "kanban.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

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

    # Stop side-effects
    monkeypatch.setattr(kanban_mod, "_record_status_transition",
                        lambda *a, **kw: None)

    return db_path, kanban_mod


def _insert_task(db_path, tid, status="backlog", depends_on=None,
                 scheduled_at=None):
    conn = sqlite3.connect(str(db_path))
    now = "2026-04-17T20:00:00+00:00"
    conn.execute(
        "INSERT INTO kanban_tasks "
        "(id, title, status, depends_on_task_id, scheduled_at, "
        " created_at, updated_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, f"task {tid}", status, depends_on, scheduled_at,
         now, now, now if status == "done" else None),
    )
    conn.commit()
    conn.close()


def _get_task(db_path, tid):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM kanban_tasks WHERE id = ?", (tid,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


class TestCascadeRollback:
    def test_demoted_parent_moves_descendants_to_backlog_not_scheduled(
        self, kanban_db,
    ):
        """Core regression — cascade must NOT leave descendants in
        'scheduled' status with a NULL scheduled_at (the invisible-row bug)."""
        db_path, kanban_mod = kanban_db

        _insert_task(db_path, "parent", status="done")
        _insert_task(db_path, "child-a", status="in_progress", depends_on="parent")
        _insert_task(db_path, "child-b", status="backlog",     depends_on="parent")
        _insert_task(db_path, "child-c", status="scheduled",   depends_on="parent",
                     scheduled_at="2026-04-17T21:00:00+00:00")

        # Parent demoted: done -> backlog
        kanban_mod._move_task("parent", "backlog")

        for cid in ("child-a", "child-b", "child-c"):
            t = _get_task(db_path, cid)
            assert t is not None, f"{cid} missing"
            assert t["status"] == "backlog", (
                f"{cid} should be 'backlog' after cascade, not {t['status']!r} — "
                "the scheduled-without-scheduled_at bug would reappear"
            )

    def test_scheduled_at_is_cleared_on_cascade(self, kanban_db):
        """If a child was already scheduled (had a scheduled_at), cascade
        must clear it — stale timestamps on a backlog row are confusing."""
        db_path, kanban_mod = kanban_db

        _insert_task(db_path, "parent", status="done")
        _insert_task(db_path, "child", status="scheduled", depends_on="parent",
                     scheduled_at="2026-04-17T21:00:00+00:00")

        kanban_mod._move_task("parent", "in_progress")

        t = _get_task(db_path, "child")
        assert t["status"] == "backlog"
        assert t["scheduled_at"] is None, (
            f"scheduled_at should be cleared, got {t['scheduled_at']!r}"
        )

    def test_cascade_records_reason_with_parent_id(self, kanban_db):
        """Descendants get a last_failure_reason naming the parent so
        operators can diagnose."""
        db_path, kanban_mod = kanban_db

        _insert_task(db_path, "dt-iqe-03", status="done")
        _insert_task(db_path, "dt-iqe-04", status="in_progress", depends_on="dt-iqe-03")

        kanban_mod._move_task("dt-iqe-03", "backlog")

        t = _get_task(db_path, "dt-iqe-04")
        assert t["last_failure_reason"] is not None
        assert "dt-iqe-03" in t["last_failure_reason"]
        assert "cascade" in t["last_failure_reason"].lower()

    def test_cascade_also_handles_scheduled_descendants(self, kanban_db):
        """The old code only cascaded in_progress + backlog descendants;
        scheduled descendants were missed. Fix now covers all three."""
        db_path, kanban_mod = kanban_db

        _insert_task(db_path, "parent", status="done")
        _insert_task(db_path, "sched-child", status="scheduled", depends_on="parent",
                     scheduled_at="2026-04-17T21:00:00+00:00")

        kanban_mod._move_task("parent", "backlog")

        t = _get_task(db_path, "sched-child")
        assert t["status"] == "backlog"

    def test_done_descendants_not_cascaded(self, kanban_db):
        """If a descendant already finished, the cascade must NOT
        demote it — its work is committed and independent of the parent's
        re-verification."""
        db_path, kanban_mod = kanban_db

        _insert_task(db_path, "parent", status="done")
        _insert_task(db_path, "done-child", status="done", depends_on="parent")

        kanban_mod._move_task("parent", "backlog")

        t = _get_task(db_path, "done-child")
        assert t["status"] == "done", (
            "a done descendant must not be demoted by cascade"
        )

    def test_no_cascade_when_parent_not_previously_done(self, kanban_db):
        """If parent goes backlog -> scheduled (never was done), no
        cascade should fire."""
        db_path, kanban_mod = kanban_db

        _insert_task(db_path, "parent", status="backlog")
        _insert_task(db_path, "child", status="backlog", depends_on="parent")

        # Give parent a real scheduled_at so _move_task to scheduled is valid
        kanban_mod._move_task("parent", "scheduled")

        t = _get_task(db_path, "child")
        # Should be unchanged
        assert t["status"] == "backlog"
        assert t["last_failure_reason"] is None
