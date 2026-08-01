# CUI // SP-CTI
"""Regression — the stale-reaper's silent-dispatch fast-path (default 1 min,
_SILENT_DISPATCH_THRESHOLD) must not fast-reap tasks whose most recent
in_progress transition was recorded with a non-scheduler actor (e.g.
'manual', via tools/kanban/cli.py --set-status).

Root cause (2026-07-08, "Autonomous Recovery" dashboard panel): 6 kanban
tasks (prop-cap-11/12/13/14, prop-fix-10/11) were being worked on directly
via isolated git worktrees, with status manually set to in_progress via
`python -m tools.kanban.cli --set-status <id> in_progress`. That path never
wrote a .tmp/kanban/<id>.log (no scheduler subprocess was ever spawned for
these tasks) and, before this fix, never recorded who made the change. The
stale-reaper's silent-dispatch check (_task_log_is_empty + not in _running)
could not distinguish that from a scheduler-dispatched subprocess that died
before writing any output, so it fast-reaped these tasks back to backlog
every ~1 minute, incrementing failure_count each time, even though the
underlying work was already complete (and, in most cases, already merged).

See tests/test_kanban_cli_manual_transition.py for the write-side half of
this fix (cli.py recording actor='manual').
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import translate_sql


class _TranslatingConn:
    """Wraps a raw sqlite3 connection, translating %s -> ? before executing.

    tools/genesis/reflexes/kanban.py's SQL is authored in Postgres-style %s
    syntax; a bare sqlite3.connect() mock doesn't understand %s. Same root
    cause fixed repeatedly this session.
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(translate_sql(sql, backend="sqlite"), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(translate_sql(sql, backend="sqlite"), seq)

    def commit(self):
        return self._conn.commit()

    def close(self):
        return self._conn.close()

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
    last_failure_reason TEXT,
    last_failure_at TEXT,
    max_retries   INTEGER DEFAULT 5
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
def reaper_ctx(tmp_path, monkeypatch):
    db_path = tmp_path / "k.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    def _fake_conn(*_a, **_kw):
        c = sqlite3.connect(str(db_path))
        c.row_factory = sqlite3.Row
        return _TranslatingConn(c)

    import tools.genesis.reflexes.kanban as km
    monkeypatch.setattr(km, "get_connection", _fake_conn)
    monkeypatch.setattr(km, "_running", {})

    return {"km": km, "db": db_path}


def _insert_task(db_path, task_id, status, updated_at, failure_count=0):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status, updated_at, failure_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (task_id, f"Title for {task_id}", status, updated_at, failure_count),
    )
    conn.commit()
    conn.close()


def _insert_transition(db_path, task_id, to_status, actor, recorded_at):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO kanban_status_transitions "
        "(id, task_id, from_status, to_status, actor, recorded_at) "
        "VALUES (?, ?, 'backlog', ?, ?, ?)",
        (f"kst-{task_id}-{recorded_at}", task_id, to_status, actor, recorded_at),
    )
    conn.commit()
    conn.close()


def _task_status(db_path, task_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT status, failure_count FROM kanban_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row


# ── _task_dispatched_by_scheduler ────────────────────────────────────────


class TestTaskDispatchedByScheduler:
    def test_no_transition_row_defaults_true(self, reaper_ctx):
        conn = _TranslatingConn(sqlite3.connect(str(reaper_ctx["db"])))
        conn._conn.row_factory = sqlite3.Row
        assert reaper_ctx["km"]._task_dispatched_by_scheduler("no-such-task", conn) is True

    def test_scheduler_actor_returns_true(self, reaper_ctx):
        _insert_transition(reaper_ctx["db"], "t1", "in_progress", "scheduler", "2026-01-01T00:00:00Z")
        conn = _TranslatingConn(sqlite3.connect(str(reaper_ctx["db"])))
        conn._conn.row_factory = sqlite3.Row
        assert reaper_ctx["km"]._task_dispatched_by_scheduler("t1", conn) is True

    def test_manual_actor_returns_false(self, reaper_ctx):
        _insert_transition(reaper_ctx["db"], "t1", "in_progress", "manual", "2026-01-01T00:00:00Z")
        conn = _TranslatingConn(sqlite3.connect(str(reaper_ctx["db"])))
        conn._conn.row_factory = sqlite3.Row
        assert reaper_ctx["km"]._task_dispatched_by_scheduler("t1", conn) is False

    def test_uses_most_recent_transition(self, reaper_ctx):
        _insert_transition(reaper_ctx["db"], "t1", "in_progress", "manual", "2026-01-01T00:00:00Z")
        _insert_transition(reaper_ctx["db"], "t1", "backlog", "scheduler", "2026-01-02T00:00:00Z")
        _insert_transition(reaper_ctx["db"], "t1", "in_progress", "scheduler", "2026-01-03T00:00:00Z")
        conn = _TranslatingConn(sqlite3.connect(str(reaper_ctx["db"])))
        conn._conn.row_factory = sqlite3.Row
        # Most recent in_progress transition (2026-01-03) is 'scheduler', not the earlier 'manual' one.
        assert reaper_ctx["km"]._task_dispatched_by_scheduler("t1", conn) is True

    def test_missing_table_defaults_true(self, tmp_path):
        db_path = tmp_path / "no_table.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE dummy (id TEXT)")
        conn.commit()
        wrapped = _TranslatingConn(conn)
        import tools.genesis.reflexes.kanban as km
        assert km._task_dispatched_by_scheduler("t1", wrapped) is True


# ── _reap_stale_in_progress integration ──────────────────────────────────


class TestReapStaleInProgressManualExemption:
    def test_manual_task_not_fast_reaped_past_the_silent_threshold(self, reaper_ctx, monkeypatch):
        """A manually-managed task must survive the fast-reap window; a
        scheduler-owned one with no log AND no heartbeat must not.

        The age moved from 90s to just past _SILENT_DISPATCH_THRESHOLD because
        that threshold went from 60s to 600s: at 60s the reaper was killing
        live agents (a file-redirected LLM dispatch prints nothing for minutes),
        which was the single largest source of task failures on this board.
        The manual-exemption behaviour under test is unchanged.
        """
        km = reaper_ctx["km"]
        monkeypatch.setattr(km, "_task_log_is_empty", lambda tid: True)
        monkeypatch.setattr(km, "_get_task_timeout", lambda tid: 900)  # avoid adaptive DB logic
        monkeypatch.setattr(km, "_detect_execution_anomaly", lambda age: (False, ""))
        # Deterministic: this process is the runner owner.
        monkeypatch.setattr(km, "_foreign_scheduler_pid", lambda: 0)

        now = datetime.now(timezone.utc)
        stale_ts = (now - timedelta(seconds=km._SILENT_DISPATCH_THRESHOLD + 30)).isoformat()

        _insert_task(reaper_ctx["db"], "prop-cap-manual", "in_progress", stale_ts)
        _insert_transition(reaper_ctx["db"], "prop-cap-manual", "in_progress", "manual", stale_ts)

        _insert_task(reaper_ctx["db"], "prop-cap-scheduler", "in_progress", stale_ts)
        _insert_transition(reaper_ctx["db"], "prop-cap-scheduler", "in_progress", "scheduler", stale_ts)

        km._reap_stale_in_progress()

        manual_row = _task_status(reaper_ctx["db"], "prop-cap-manual")
        scheduler_row = _task_status(reaper_ctx["db"], "prop-cap-scheduler")

        assert manual_row[0] == "in_progress", "manually-managed task must survive the fast-reap window"
        assert scheduler_row[0] == "backlog", (
            "scheduler-owned task with no log AND no heartbeat must still fast-reap"
        )
        assert scheduler_row[1] == 1

    def test_manual_task_with_no_transition_row_still_fast_reaped(self, reaper_ctx, monkeypatch):
        """Backward-compat: a task with NO transition history at all (e.g.
        pre-fix data, or set via a path other than the CLI) keeps the
        original aggressive behavior -- this fix only ever *relaxes* the
        threshold for explicitly-marked manual tasks, never loosens the
        default for anything else."""
        km = reaper_ctx["km"]
        monkeypatch.setattr(km, "_task_log_is_empty", lambda tid: True)
        monkeypatch.setattr(km, "_get_task_timeout", lambda tid: 900)
        monkeypatch.setattr(km, "_detect_execution_anomaly", lambda age: (False, ""))
        monkeypatch.setattr(km, "_foreign_scheduler_pid", lambda: 0)

        now = datetime.now(timezone.utc)
        # Past the (now 600s) silent-dispatch threshold; no heartbeat recorded.
        stale_ts = (now - timedelta(seconds=km._SILENT_DISPATCH_THRESHOLD + 30)).isoformat()
        _insert_task(reaper_ctx["db"], "prop-cap-untracked", "in_progress", stale_ts)

        km._reap_stale_in_progress()

        row = _task_status(reaper_ctx["db"], "prop-cap-untracked")
        assert row[0] == "backlog"

    def test_manual_task_eventually_reaped_past_normal_timeout(self, reaper_ctx, monkeypatch):
        """The manual-actor exemption only skips the FAST silent-dispatch
        path -- it still falls through to the normal 2x-timeout threshold,
        so genuinely-abandoned manual work is eventually reaped too."""
        km = reaper_ctx["km"]
        monkeypatch.setattr(km, "_task_log_is_empty", lambda tid: True)
        monkeypatch.setattr(km, "_get_task_timeout", lambda tid: 60)  # normal threshold = 120s
        monkeypatch.setattr(km, "_detect_execution_anomaly", lambda age: (False, ""))

        now = datetime.now(timezone.utc)
        very_stale_ts = (now - timedelta(seconds=300)).isoformat()
        _insert_task(reaper_ctx["db"], "prop-cap-abandoned", "in_progress", very_stale_ts)
        _insert_transition(reaper_ctx["db"], "prop-cap-abandoned", "in_progress", "manual", very_stale_ts)

        km._reap_stale_in_progress()

        row = _task_status(reaper_ctx["db"], "prop-cap-abandoned")
        assert row[0] == "backlog"
