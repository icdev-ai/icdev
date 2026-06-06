# CUI // SP-CTI
"""Tests for _promote_unblocking_suggested — the suggested-lane deadlock breaker.

Standalone (no conftest): an in-memory SQLite connection is passed directly to
the function, so it runs without the full platform fixture set.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import kanban as k  # noqa: E402


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE kanban_tasks ("
        " id TEXT PRIMARY KEY, title TEXT, priority TEXT, status TEXT,"
        " failure_count INTEGER DEFAULT 0, last_failure_reason TEXT,"
        " depends_on_task_id TEXT, updated_at TEXT)"
    )
    return c


def _status(c, tid):
    return dict(c.execute("SELECT status FROM kanban_tasks WHERE id=?", (tid,)).fetchone())["status"]


def test_chain_blocker_promoted():
    # A backlog task depends on a suggested card -> the card must be promoted
    # so the chain doesn't deadlock.
    c = _conn()
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,failure_count) VALUES ('S','s','medium','suggested',0)")
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,depends_on_task_id) VALUES ('T','t','medium','backlog','S')")
    c.commit()
    k._promote_unblocking_suggested(c)
    assert _status(c, "S") == "backlog"


def test_critical_promoted_even_when_queue_busy():
    c = _conn()
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,failure_count) VALUES ('C','c','critical','suggested',0)")
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status) VALUES ('B','b','medium','in_progress')")
    c.commit()
    k._promote_unblocking_suggested(c)
    assert _status(c, "C") == "backlog"


def test_queue_idle_promotes_eligible():
    # No dispatchable work anywhere -> promote the eligible suggested card.
    c = _conn()
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,failure_count) VALUES ('Q','q','medium','suggested',0)")
    c.commit()
    k._promote_unblocking_suggested(c)
    assert _status(c, "Q") == "backlog"


def test_hard_hitl_blocker_not_promoted():
    # A human-held (hard-quarantine) card that blocks a chain is left alone.
    c = _conn()
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,failure_count,last_failure_reason) VALUES ('H','h','medium','suggested',6,'hard-quarantine')")
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,depends_on_task_id) VALUES ('T2','t','medium','backlog','H')")
    c.commit()
    k._promote_unblocking_suggested(c)
    assert _status(c, "H") == "suggested"


def test_idle_non_eligible_left_when_queue_busy():
    # A plain medium suggested card that blocks nothing stays put while other
    # work is dispatchable (no 48 h decay short-circuit here).
    c = _conn()
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,failure_count) VALUES ('N','n','medium','suggested',0)")
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status) VALUES ('B2','b','medium','backlog')")
    c.commit()
    k._promote_unblocking_suggested(c)
    assert _status(c, "N") == "suggested"


def test_blocker_with_done_dependent_not_promoted():
    # If the only dependent is already done, the card isn't a live blocker.
    c = _conn()
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,failure_count) VALUES ('S2','s','medium','suggested',0)")
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status,depends_on_task_id) VALUES ('TD','t','medium','done','S2')")
    c.execute("INSERT INTO kanban_tasks (id,title,priority,status) VALUES ('B3','b','medium','backlog')")
    c.commit()
    k._promote_unblocking_suggested(c)
    assert _status(c, "S2") == "suggested"
