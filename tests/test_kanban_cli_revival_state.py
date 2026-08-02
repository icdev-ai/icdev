# CUI // SP-CTI
"""Reviving a task from the CLI must clear the state that says it is broken.

Found 2026-08-01 after reviving 25 tasks quarantined by the executor-resolution
bug (PR #1132). All 25 moved to `backlog` correctly, but 16 kept their
`last_failure_reason` — the obsolete
``no executor available: internet=False, gitlab=unreachable, ollama=unreachable``
— so the dashboard's Autonomous Recovery panel, which filters on
``last_failure_reason IS NOT NULL``, still listed them as broken.

`cmd_set_status` wrote only `status` and `updated_at`. The scheduler clears the
reason on re-dispatch, so the rows did eventually self-heal — but only once each
task reached `in_progress`, which at MAX_IN_PROGRESS=3 is hours away. Until then
triage reads a failure that is not real.

The move-to-`scheduled` case carries a second, latent defect: `_get_due_tasks`
requires `scheduled_at IS NOT NULL`, so a CLI-scheduled task with no stamp is
invisible to the dispatcher.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _schema(conn):
    conn.executescript(
        """
        CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            updated_at TEXT,
            completed_at TEXT,
            scheduled_at TEXT,
            failure_count INTEGER DEFAULT 0,
            last_failure_at TEXT,
            last_failure_reason TEXT
        );
        """
    )


class _Conn:
    """Minimal stand-in matching how cmd_set_status uses the connection."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        return self._raw.execute(sql.replace("%s", "?"), params)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._raw.commit()
        return False


@pytest.fixture()
def cli(monkeypatch, tmp_path):
    from tools.kanban import cli as mod

    raw = sqlite3.connect(str(tmp_path / "k.db"))
    raw.row_factory = sqlite3.Row
    _schema(raw)
    raw.execute(
        "INSERT INTO kanban_tasks (id, title, status, failure_count, "
        "last_failure_at, last_failure_reason) VALUES (?,?,?,?,?,?)",
        ("gpx-hyg-01", "Move init_db prints off stdout", "suggested", 2,
         "2026-08-01T21:11:51+00:00",
         "no executor available: internet=False, gitlab=unreachable, ollama=unreachable"),
    )
    raw.commit()

    monkeypatch.setattr(mod, "get_connection", lambda *a, **kw: _Conn(raw))
    monkeypatch.setattr(mod, "_record_manual_transition", lambda *a, **kw: None)
    return mod, raw


def _row(raw, tid="gpx-hyg-01"):
    return dict(raw.execute("SELECT * FROM kanban_tasks WHERE id = ?", (tid,)).fetchone())


# --------------------------------------------------------------------------
# The regression
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["backlog", "scheduled", "in_progress"])
def test_revival_clears_the_stale_failure_reason(cli, status):
    mod, raw = cli
    assert mod.cmd_set_status(["gpx-hyg-01"], status, json_out=False) == 0
    r = _row(raw)
    assert r["status"] == status
    assert r["last_failure_reason"] is None, (
        f"moving to {status!r} must clear the obsolete reason, or the task keeps "
        "showing as broken in the Autonomous Recovery panel"
    )


@pytest.mark.parametrize("status", ["backlog", "scheduled", "in_progress"])
def test_revival_preserves_real_history(cli, status):
    """failure_count feeds the circuit breaker — clearing it would hide a loop."""
    mod, raw = cli
    mod.cmd_set_status(["gpx-hyg-01"], status, json_out=False)
    r = _row(raw)
    assert r["failure_count"] == 2
    assert r["last_failure_at"] == "2026-08-01T21:11:51+00:00"


def test_done_keeps_the_reason_as_history(cli):
    """`done` is not a revival — the record of why it once failed is kept."""
    mod, raw = cli
    mod.cmd_set_status(["gpx-hyg-01"], "done", json_out=False, force_done=True,
                       reason="merged out of band")
    r = _row(raw)
    assert r["last_failure_reason"] is not None
    assert r["completed_at"] is not None


def test_failed_does_not_clear_the_reason(cli):
    mod, raw = cli
    mod.cmd_set_status(["gpx-hyg-01"], "failed", json_out=False)
    assert _row(raw)["last_failure_reason"] is not None


# --------------------------------------------------------------------------
# The latent dispatcher-visibility defect
# --------------------------------------------------------------------------

def test_scheduled_stamps_scheduled_at(cli):
    """_get_due_tasks needs scheduled_at; without it the row never dispatches."""
    mod, raw = cli
    assert _row(raw)["scheduled_at"] is None
    mod.cmd_set_status(["gpx-hyg-01"], "scheduled", json_out=False)
    assert _row(raw)["scheduled_at"] is not None, (
        "a CLI-scheduled task with no scheduled_at is invisible to the dispatcher"
    )


def test_scheduled_does_not_clobber_an_existing_stamp(cli):
    """Re-issuing the command must not push the task's due time forward."""
    mod, raw = cli
    raw.execute("UPDATE kanban_tasks SET scheduled_at = ? WHERE id = ?",
                ("2026-07-01T00:00:00+00:00", "gpx-hyg-01"))
    raw.commit()
    mod.cmd_set_status(["gpx-hyg-01"], "scheduled", json_out=False)
    assert _row(raw)["scheduled_at"] == "2026-07-01T00:00:00+00:00"


def test_non_scheduled_status_leaves_scheduled_at_alone(cli):
    mod, raw = cli
    mod.cmd_set_status(["gpx-hyg-01"], "backlog", json_out=False)
    assert _row(raw)["scheduled_at"] is None


def test_batch_revival_clears_every_row(cli):
    mod, raw = cli
    for tid in ("aca-trn-04", "swp-swallow-01"):
        raw.execute(
            "INSERT INTO kanban_tasks (id, title, status, failure_count, last_failure_reason) "
            "VALUES (?,?,?,?,?)",
            (tid, tid, "suggested", 1, "no executor available: internet=False"),
        )
    raw.commit()
    ids = ["gpx-hyg-01", "aca-trn-04", "swp-swallow-01"]
    assert mod.cmd_set_status(ids, "backlog", json_out=False) == 0
    for tid in ids:
        assert _row(raw, tid)["last_failure_reason"] is None, f"{tid} not cleared"
