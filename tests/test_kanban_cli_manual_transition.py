# CUI // SP-CTI
"""Regression — tools/kanban/cli.py --set-status must record a manual-actor
transition, so the scheduler's stale-reaper doesn't fast-reap externally-
managed in_progress tasks (root cause of the "Autonomous Recovery" panel
repeatedly bouncing prop-cap-11/12/13/14 + prop-fix-10/11 between backlog
and in_progress on 2026-07-08 — see test_kanban_stale_reaper_manual_actor.py
for the reaper-side half of this fix).

Before this fix, cmd_set_status did a bare UPDATE kanban_tasks with no
audit trail at all, so the reaper's silent-dispatch fast-path (1 min) could
not distinguish "scheduler dispatch died silently" from "an external
session set this in_progress and is genuinely working it."
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.db.storage import translate_sql


class _TranslatingConn:
    """Wraps a raw sqlite3 connection, translating %s -> ? before executing.

    tools/kanban/cli.py's SQL is authored in Postgres-style %s syntax
    (correct for the real backend via get_connection()'s own translation);
    a bare sqlite3.connect() mock doesn't understand %s. Same root cause
    fixed repeatedly this session (prop-fix-10/11, prop-cap-11/12/13).
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(translate_sql(sql, backend="sqlite"), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(translate_sql(sql, backend="sqlite"), seq)

    def commit(self):
        return self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._conn.commit()
        self._conn.close()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


_SCHEMA = """
CREATE TABLE kanban_tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT DEFAULT 'backlog',
    priority      TEXT DEFAULT 'medium',
    task_type     TEXT DEFAULT 'build',
    created_at    TEXT,
    updated_at    TEXT,
    completed_at  TEXT,
    failure_count INTEGER DEFAULT 0,
    last_failure_reason TEXT
);
CREATE TABLE kanban_status_transitions (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    actor        TEXT NOT NULL DEFAULT 'unknown',
    reason       TEXT,
    recorded_at  TEXT NOT NULL
);
"""


@pytest.fixture()
def cli_ctx(tmp_path, monkeypatch):
    db_path = tmp_path / "k.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
        ("prop-cap-99", "Test task", "backlog"),
    )
    conn.commit()
    conn.close()

    def _fake_conn(*_a, **_kw):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return _TranslatingConn(c)

    import tools.kanban.cli as cli_mod
    monkeypatch.setattr(cli_mod, "get_connection", _fake_conn)

    return {"cli": cli_mod, "db": db_path}


def _transitions(db_path, task_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM kanban_status_transitions WHERE task_id = ? ORDER BY recorded_at",
        (task_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class TestSetStatusRecordsManualTransition:
    def test_set_status_to_in_progress_records_manual_actor(self, cli_ctx):
        rc = cli_ctx["cli"].cmd_set_status(["prop-cap-99"], "in_progress", json_out=False)
        assert rc == 0
        rows = _transitions(cli_ctx["db"], "prop-cap-99")
        assert len(rows) == 1
        assert rows[0]["actor"] == "manual"
        assert rows[0]["from_status"] == "backlog"
        assert rows[0]["to_status"] == "in_progress"

    def test_set_status_records_correct_from_status(self, cli_ctx):
        cli_ctx["cli"].cmd_set_status(["prop-cap-99"], "in_progress", json_out=False)
        cli_ctx["cli"].cmd_set_status(["prop-cap-99"], "done", json_out=False)
        rows = _transitions(cli_ctx["db"], "prop-cap-99")
        assert len(rows) == 2
        assert rows[1]["from_status"] == "in_progress"
        assert rows[1]["to_status"] == "done"

    def test_multiple_task_ids_each_get_own_transition_row(self, cli_ctx):
        conn = sqlite3.connect(str(cli_ctx["db"]))
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
            ("prop-cap-98", "Second task", "backlog"),
        )
        conn.commit()
        conn.close()

        cli_ctx["cli"].cmd_set_status(["prop-cap-99", "prop-cap-98"], "in_progress", json_out=False)
        assert len(_transitions(cli_ctx["db"], "prop-cap-99")) == 1
        assert len(_transitions(cli_ctx["db"], "prop-cap-98")) == 1

    def test_unknown_task_id_does_not_record_transition(self, cli_ctx):
        rc = cli_ctx["cli"].cmd_set_status(["nonexistent-task"], "in_progress", json_out=False)
        assert rc == 0  # still succeeds (matches existing "NOT FOUND" reporting behavior)
        assert _transitions(cli_ctx["db"], "nonexistent-task") == []

    def test_missing_transitions_table_does_not_break_status_update(self, tmp_path, monkeypatch):
        """If migration 025 hasn't run yet, the primary UPDATE must still succeed."""
        db_path = tmp_path / "no_audit_table.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            # last_failure_reason is in the real schema and the CLI's status
            # UPDATE clears it; omitting it here failed the UPDATE for a reason
            # that had nothing to do with the missing-transitions-table case
            # this test is actually about.
            "CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, "
            "updated_at TEXT, completed_at TEXT, last_failure_reason TEXT)"
        )
        conn.execute("INSERT INTO kanban_tasks (id, title, status) VALUES ('t1', 'T', 'backlog')")
        conn.commit()
        conn.close()

        def _fake_conn(*_a, **_kw):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return _TranslatingConn(c)

        import tools.kanban.cli as cli_mod
        monkeypatch.setattr(cli_mod, "get_connection", _fake_conn)

        rc = cli_mod.cmd_set_status(["t1"], "in_progress", json_out=False)
        assert rc == 0
        conn = sqlite3.connect(str(db_path))
        status = conn.execute("SELECT status FROM kanban_tasks WHERE id = 't1'").fetchone()[0]
        conn.close()
        assert status == "in_progress"
