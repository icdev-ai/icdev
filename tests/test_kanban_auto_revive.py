# CUI // SP-CTI
"""Bounded auto-revive of failure-quarantined 'suggested' kanban tasks.

Regression coverage for the fix to the recurring "tasks stuck in SUGGESTED
after repeated failures" problem. The stale-reaper quarantines fc>=5 tasks to
'suggested'; without recovery they (and their dependency chains) rot forever.
`_revive_quarantined_suggested` revives them to 'backlog' when deps are
satisfied and they've cooled down, capped at MAX_AUTO_REVIVE, then holds for HITL.

Covers:
  * quarantined + deps satisfied + cooled down  -> revived to backlog (fc reset)
  * quarantined but dependency not done          -> stays suggested
  * quarantined but still within cooldown         -> stays suggested
  * genuine prediction card (fc=0, no reason)     -> never touched
  * revive cap reached                            -> held for HITL (one alert flag)
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.genesis.reflexes import kanban as K  # noqa: E402


def _iso(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    monkeypatch.setenv("ICDEV_SUPPRESS_NOTIFICATIONS", "1")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE kanban_tasks (
            id                 TEXT PRIMARY KEY,
            title              TEXT NOT NULL DEFAULT '',
            status             TEXT DEFAULT 'backlog',
            failure_count      INTEGER DEFAULT 0,
            last_failure_reason TEXT,
            depends_on_task_id TEXT,
            updated_at         TEXT
        );
        """
    )
    # Hand production code a StorageConnection, not the raw sqlite3 one. Runtime
    # SQL is authored for PostgreSQL (%s placeholders, per CLAUDE.md); the storage
    # wrapper is what translates them to SQLite's ?. Passing the raw connection
    # makes every %s a `near "%": syntax error`.
    from tools.db.storage import StorageConnection

    return StorageConnection(c, "sqlite")


def _add(conn, tid, *, status="suggested", fc=5, reason="stale-reaper ... HITL review",
         dep=None, updated_min_ago=120, title="t"):
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status, failure_count, last_failure_reason, "
        "depends_on_task_id, updated_at) VALUES (?,?,?,?,?,?,?)",
        (tid, title, status, fc, reason, dep, _iso(updated_min_ago)),
    )
    conn.commit()


def _status(conn, tid):
    return dict(conn.execute("SELECT status, failure_count FROM kanban_tasks WHERE id=?", (tid,)).fetchone())


def test_revives_quarantined_when_deps_satisfied_and_cooled(conn):
    _add(conn, "q1", fc=5, dep=None, updated_min_ago=120)
    K._revive_quarantined_suggested(conn)
    row = _status(conn, "q1")
    assert row["status"] == "backlog"
    assert row["failure_count"] == 0
    rc = conn.execute("SELECT revive_count FROM kanban_task_revivals WHERE task_id='q1'").fetchone()
    assert rc is not None and rc["revive_count"] == 1


def test_blocked_dependency_stays_quarantined(conn):
    _add(conn, "parent", status="in_progress", fc=0, reason=None, updated_min_ago=120)
    _add(conn, "q2", fc=5, dep="parent", updated_min_ago=120)
    K._revive_quarantined_suggested(conn)
    assert _status(conn, "q2")["status"] == "suggested"


def test_satisfied_dependency_allows_revive(conn):
    _add(conn, "parent", status="done", fc=0, reason=None, updated_min_ago=120)
    _add(conn, "q3", fc=5, dep="parent", updated_min_ago=120)
    K._revive_quarantined_suggested(conn)
    assert _status(conn, "q3")["status"] == "backlog"


def test_within_cooldown_not_revived(conn):
    _add(conn, "q4", fc=5, dep=None, updated_min_ago=5)  # < 30 min cooldown
    K._revive_quarantined_suggested(conn)
    assert _status(conn, "q4")["status"] == "suggested"


def test_genuine_prediction_card_untouched(conn):
    # fc=0, no failure reason -> an AI prediction, not a quarantined failure.
    _add(conn, "pred1", fc=0, reason=None, dep=None, updated_min_ago=240)
    K._revive_quarantined_suggested(conn)
    assert _status(conn, "pred1")["status"] == "suggested"


def test_cap_reached_holds_for_hitl(conn):
    _add(conn, "q5", fc=5, dep=None, updated_min_ago=120)
    # Pre-seed the revival counter at the cap.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kanban_task_revivals "
        "(task_id TEXT PRIMARY KEY, revive_count INTEGER NOT NULL DEFAULT 0, "
        " last_revived_at TEXT, hitl_alerted INTEGER NOT NULL DEFAULT 0, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO kanban_task_revivals (task_id, revive_count, hitl_alerted) VALUES (?,?,0)",
        ("q5", K.MAX_AUTO_REVIVE),
    )
    conn.commit()
    K._revive_quarantined_suggested(conn)
    # Stays quarantined; HITL alert flag flipped so it only fires once.
    assert _status(conn, "q5")["status"] == "suggested"
    alerted = conn.execute("SELECT hitl_alerted FROM kanban_task_revivals WHERE task_id='q5'").fetchone()
    assert alerted["hitl_alerted"] == 1
