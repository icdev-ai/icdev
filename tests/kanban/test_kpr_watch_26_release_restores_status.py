# CUI // SP-CTI
"""kpr-watch-26: ``--release`` puts the task back where ``--claim`` took it from.

MEASURED 2026-10-11: ``--claim omx-vllm-04`` moved it pr_opened -> in_progress
(the row said "--set-status"); ``--release`` dropped the lease and left it
in_progress -- the shape the stale-reaper and startup-recovery read as a dead
dispatch. A human had to move it back before the runner rebuilt an open PR.

Real SQLite tables, real claim/release SQL; only the lease layer is stubbed.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.kanban import cli

TASK = "omx-x-03"


class _Conn:
    """sqlite3 behind the `%s` paramstyle cli.py writes."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        return self.raw.execute(sql.replace("%s", "?"), params)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.raw.commit()
        return False


@pytest.fixture
def db(tmp_path, monkeypatch):
    raw = sqlite3.connect(str(tmp_path / "k.db"))
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE kanban_tasks (id TEXT PRIMARY KEY, status TEXT, "
                "branch_name TEXT, commit_summary TEXT, updated_at TEXT)")
    raw.execute("CREATE TABLE kanban_status_transitions (id TEXT, task_id TEXT, "
                "from_status TEXT, to_status TEXT, actor TEXT, reason TEXT, recorded_at TEXT)")
    raw.commit()
    monkeypatch.setattr(cli, "get_connection", lambda: _Conn(raw))
    # The lease layer: a claim always succeeds; a release always lets go.
    monkeypatch.setattr(cli, "_task_lease_resource", lambda t: f"kanban:task:{t}")
    from tools.kanban import interactive_claim
    monkeypatch.setattr(interactive_claim, "claim", lambda *a, **k: {"claimed": True})
    from tools.coordination import leases
    monkeypatch.setattr(leases, "holder", lambda r: {})
    monkeypatch.setattr(cli, "_release_task_lease", lambda t: {
        "released": True, "reclaimed": False, "prior": {}, "still": None,
        "worker_heartbeating": False, "why": "", "verdict_state": "released",
        "interactive": True})
    return raw


def _seed(db, status):
    db.execute("INSERT INTO kanban_tasks (id, status) VALUES (?, ?)", (TASK, status))
    db.commit()


def _status(db):
    return db.execute("SELECT status FROM kanban_tasks WHERE id = ?", (TASK,)).fetchone()[0]


def test_release_restores_the_status_claim_took_it_from(db):
    _seed(db, "pr_opened")
    assert cli.cmd_claim(TASK, json_out=True) == 0
    assert _status(db) == "in_progress"
    reason = db.execute("SELECT reason FROM kanban_status_transitions "
                        "WHERE to_status = 'in_progress'").fetchone()[0]
    assert reason == cli.CLAIM_TRANSITION_REASON

    assert cli.cmd_release(TASK, json_out=True) == 0
    assert _status(db) == "pr_opened", "an in_progress task with no lease reads as a dead dispatch"
    last = db.execute("SELECT from_status, to_status FROM kanban_status_transitions "
                      "ORDER BY rowid DESC LIMIT 1").fetchone()
    assert tuple(last) == ("in_progress", "pr_opened")


def test_a_move_after_the_claim_wins(db):
    _seed(db, "pr_opened")
    cli.cmd_claim(TASK, json_out=True)
    # Something else moved it on and back (e.g. a hand move) -- the claim is no
    # longer the last word.
    db.execute("INSERT INTO kanban_status_transitions VALUES "
               "('x', ?, 'in_progress', 'in_progress', 'mcp', 'hand move', '2999-01-01T00:00:00+00:00')",
               (TASK,))
    db.commit()
    cli.cmd_release(TASK, json_out=True)
    assert _status(db) == "in_progress"


def test_a_task_that_left_in_progress_is_not_touched(db):
    _seed(db, "pr_opened")
    cli.cmd_claim(TASK, json_out=True)
    db.execute("UPDATE kanban_tasks SET status = 'done' WHERE id = ?", (TASK,))
    db.commit()
    cli.cmd_release(TASK, json_out=True)
    assert _status(db) == "done"


def test_done_is_never_restored(db):
    _seed(db, "done")
    cli.cmd_claim(TASK, json_out=True)
    cli.cmd_release(TASK, json_out=True)
    assert _status(db) == "in_progress", "done has its own merge-verified door"


def test_claiming_an_in_progress_task_changes_nothing_on_release(db):
    _seed(db, "in_progress")
    cli.cmd_claim(TASK, json_out=True)
    cli.cmd_release(TASK, json_out=True)
    assert _status(db) == "in_progress"
    assert db.execute("SELECT COUNT(*) FROM kanban_status_transitions").fetchone()[0] == 0
