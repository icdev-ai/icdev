# CUI // SP-CTI
"""Test: acw-nova-02 — reflexion_loop fires on ACE instance completion.

Verifies that when an ACE instance transitions to 'complete' state:
1. A task.completed event is emitted to ace_events
2. ACEEventDispatcher routes it to the reflexion_loop role
3. An agent_execution_trace row is written within the dispatch cycle

All assertions run synchronously (no background threads); the DB is the
SQLite test backend forced by conftest.py.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

# Repo root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Disable co-learning so reflexion_loop.run() is a fast no-op
os.environ.setdefault("ICDEV_HARNESS_COLEARN", "false")



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique(prefix: str = "inst") -> str:
    return f"ace-test-{prefix}-{uuid.uuid4().hex[:8]}"


_TRACES_DDL = """
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
)
"""


def _ensure_trace_tables(conn) -> None:
    """Ensure agent_execution_traces table exists (idempotent)."""
    conn.execute(_TRACES_DDL)
    conn.commit()


def _trace_count(conn, task_type_prefix: str) -> int:
    _ensure_trace_tables(conn)
    rows = conn.execute(
        "SELECT COUNT(*) FROM agent_execution_traces WHERE task_type LIKE ?",
        (f"{task_type_prefix}%",),
    ).fetchone()
    return int(rows[0]) if rows else 0


# ---------------------------------------------------------------------------
# Test 1: event_bus.emit writes a task.completed row to ace_events
# ---------------------------------------------------------------------------

def test_emit_completion_event_writes_ace_event():
    """_emit_completion_event inserts a task.completed row into ace_events."""
    from icdev.tools.ace.controller import ACEController
    from icdev.tools.ace.db.init_db import init as _init_ace
    from icdev.tools.db.storage import get_canvas_connection

    _init_ace()
    instance_id = _unique("emit")

    # Directly call the static method under test
    ACEController._emit_completion_event(instance_id)

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        row = conn.execute(
            "SELECT topic, source_canvas, source_id FROM ace_events "
            "WHERE source_id = ? AND topic = 'task.completed' LIMIT 1",
            (instance_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "task.completed event not found in ace_events"
    topic = row[0] if isinstance(row, (list, tuple)) else row["topic"]
    canvas = row[1] if isinstance(row, (list, tuple)) else row["source_canvas"]
    assert topic == "task.completed"
    assert canvas == "ace"


# ---------------------------------------------------------------------------
# Test 2: reflexion_loop role exists and declares task.completed in listen_topics
# ---------------------------------------------------------------------------

def test_reflexion_loop_role_loaded():
    """reflexion_loop role YAML is loadable and listens to task.completed."""
    from icdev.tools.ace.role_loader import RoleLoader

    loader = RoleLoader()
    role = loader.get_role("reflexion_loop")
    assert role is not None, "reflexion_loop role not found by RoleLoader"
    topics = role.communication.get("listen_topics") or []
    assert "task.completed" in topics, (
        f"reflexion_loop role does not listen to task.completed; got {topics}"
    )
    assert role.genesis_reflex == "reflexion_loop", (
        f"genesis_reflex should be 'reflexion_loop', got {role.genesis_reflex!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: dispatcher creates an agent_execution_trace when processing the event
# ---------------------------------------------------------------------------

def test_dispatcher_creates_trace_on_task_completed():
    """Processing a task.completed event for the reflexion_loop role creates a trace row."""
    from icdev.tools.ace.db.init_db import init as _init_ace
    from icdev.tools.db.storage import get_connection
    from icdev.tools.ace.event_bus import emit as ace_emit
    from icdev.tools.ace.event_dispatcher import ACEEventDispatcher

    _init_ace()
    trace_type_prefix = "ace.task.completed"

    # Baseline (fresh connection)
    conn_pre = get_connection()
    _ensure_trace_tables(conn_pre)
    count_before = _trace_count(conn_pre, trace_type_prefix)
    conn_pre.close()

    instance_id = _unique("disp")

    # Emit the event that would be fired on ACE instance completion
    ace_emit(
        "task.completed",
        {"trigger": "instance_complete", "instance_id": instance_id},
        source_canvas="ace",
        source_id=instance_id,
    )

    # Process synchronously (bypasses the 5s background poll)
    dispatcher = ACEEventDispatcher()
    dispatcher._process_pending()

    # Reopen to see committed trace rows
    conn_post = get_connection()
    count_after = _trace_count(conn_post, trace_type_prefix)
    conn_post.close()

    assert count_after > count_before, (
        f"Expected at least one new agent_execution_trace row with task_type "
        f"starting with '{trace_type_prefix}', but count went {count_before} → {count_after}"
    )


# ---------------------------------------------------------------------------
# Test 4: full round-trip via ACEController._emit_completion_event + dispatcher
# ---------------------------------------------------------------------------

def test_full_round_trip_emit_and_dispatch():
    """Complete flow: emit completion event → dispatch → trace created."""
    from icdev.tools.ace.db.init_db import init as _init_ace
    from icdev.tools.ace.controller import ACEController
    from icdev.tools.ace.event_dispatcher import ACEEventDispatcher
    from icdev.tools.db.storage import get_connection

    _init_ace()

    # Snapshot count before (fresh connection each time to avoid SQLite isolation)
    conn_before = get_connection()
    _ensure_trace_tables(conn_before)
    instance_id = _unique("rt")
    prefix = "ace.task.completed"
    before = _trace_count(conn_before, prefix)
    conn_before.close()

    ACEController._emit_completion_event(instance_id)

    dispatcher = ACEEventDispatcher()
    dispatcher._process_pending()

    # Reopen connection so we see the trace committed by _launch_genesis_reflex
    conn_after = get_connection()
    after = _trace_count(conn_after, prefix)
    conn_after.close()

    assert after > before, (
        f"Round-trip: no trace created after emit+dispatch "
        f"(before={before}, after={after})"
    )
