# CUI // SP-CTI
"""Test: Wire reflexion_loop reflex to fire on ACE instance completion.

Acceptance: completing an ACE instance creates a new agent_execution_trace row.
Tests:
  1. run_for_instance() directly creates a trace row.
  2. Publishing task.completed on nova canvas fires the handler (full event bus flow).
  3. ACEController._emit_task_completed() triggers the full chain.
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BASE = Path(__file__).resolve().parents[1]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

# ---------------------------------------------------------------------------
# Minimal in-memory schema — all tables used by the event bus + trace logger
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id            TEXT PRIMARY KEY,
    source_canvas TEXT NOT NULL,
    target_canvas TEXT,
    event_type    TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    consumed_at   TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    event_type      TEXT,
    actor           TEXT,
    action          TEXT NOT NULL,
    details         TEXT,
    affected_files  TEXT,
    classification  TEXT DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS agent_execution_traces (
    trace_id          TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL,
    task_type         TEXT NOT NULL DEFAULT '',
    skill_used        TEXT NOT NULL DEFAULT '',
    outcome           TEXT NOT NULL DEFAULT 'unknown',
    events_json       TEXT NOT NULL DEFAULT '[]',
    lesson_pattern    TEXT NOT NULL DEFAULT '',
    improvement_notes TEXT NOT NULL DEFAULT '',
    started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class _NoClose:
    """Proxy that delegates to the real connection but ignores .close()."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def execute(self, *a, **kw):
        return self._c.execute(*a, **kw)

    def executescript(self, *a, **kw):
        return self._c.executescript(*a, **kw)

    def commit(self):
        return self._c.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture()
def mem_db():
    """In-memory SQLite with canvas_events, audit_trail, and agent_execution_traces."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def _clean_listeners():
    """Restore event_bus._LISTENERS after each test to avoid cross-test bleed."""
    event_bus_mod = importlib.import_module("icdev.tools.canvas.event_bus")
    saved = dict(event_bus_mod._LISTENERS)
    yield
    event_bus_mod._LISTENERS.clear()
    event_bus_mod._LISTENERS.update(saved)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_for_instance_creates_trace(mem_db):
    """reflexion_loop.run_for_instance() inserts an agent_execution_trace row."""
    proxy = _NoClose(mem_db)

    with patch("tools.db.storage.get_connection", return_value=proxy):
        from tools.nova.reflexion_loop import run_for_instance

        trace_id = run_for_instance("ace-testinstance001")

    row = mem_db.execute(
        "SELECT trace_id, task_type, outcome FROM agent_execution_traces WHERE trace_id = ?",
        (trace_id,),
    ).fetchone()

    assert row is not None, "Expected a trace row but found none"
    assert row["task_type"] == "ace_instance"
    assert row["outcome"] == "success"


def test_event_bus_dispatches_to_reflexion_handler(mem_db):
    """Publishing task.completed on nova canvas triggers a new trace row."""
    proxy = _NoClose(mem_db)
    event_bus_mod = importlib.import_module("icdev.tools.canvas.event_bus")
    event_bus_mod._LISTENERS.clear()

    with patch("icdev.tools.canvas.event_bus.get_connection", return_value=proxy), \
         patch("tools.db.storage.get_connection", return_value=proxy):

        from tools.nova.reflexion_loop import register
        register()

        count_before = mem_db.execute(
            "SELECT COUNT(*) FROM agent_execution_traces WHERE task_type = 'ace_instance'"
        ).fetchone()[0]

        event_bus_mod.publish(
            source_canvas="ace",
            event_type="task.completed",
            payload_dict={"trigger": "instance_complete", "instance_id": "ace-inttest001"},
            target_canvas="nova",
        )

        count_after = mem_db.execute(
            "SELECT COUNT(*) FROM agent_execution_traces WHERE task_type = 'ace_instance'"
        ).fetchone()[0]

    assert count_after > count_before, (
        f"Expected a new agent_execution_trace row after task.completed event; "
        f"got {count_after} (was {count_before})"
    )


def test_ace_controller_emit_task_completed_creates_trace(mem_db):
    """ACEController._emit_task_completed() triggers the full reflexion chain."""
    proxy = _NoClose(mem_db)
    event_bus_mod = importlib.import_module("icdev.tools.canvas.event_bus")
    event_bus_mod._LISTENERS.clear()

    with patch("icdev.tools.canvas.event_bus.get_connection", return_value=proxy), \
         patch("tools.db.storage.get_connection", return_value=proxy):

        from tools.nova.reflexion_loop import register
        register()

        count_before = mem_db.execute(
            "SELECT COUNT(*) FROM agent_execution_traces WHERE task_type = 'ace_instance'"
        ).fetchone()[0]

        from icdev.tools.ace.controller import ACEController
        ACEController._emit_task_completed("ace-ctrl-test001")

        count_after = mem_db.execute(
            "SELECT COUNT(*) FROM agent_execution_traces WHERE task_type = 'ace_instance'"
        ).fetchone()[0]

    assert count_after > count_before, (
        f"Expected a new trace row after ACEController._emit_task_completed(); "
        f"got {count_after} (was {count_before})"
    )
