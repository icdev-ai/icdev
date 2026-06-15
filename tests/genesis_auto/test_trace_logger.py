#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for NOVA ECHO — tools/workflow/trace_logger.py

Validates: import, start_trace, log_event, close_trace,
get_traces_for_task_type, get_recent_traces.
All tests run on the SQLite test backend (forced by conftest).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))



# ---------------------------------------------------------------------------
# Import check
# ---------------------------------------------------------------------------

def test_trace_logger_imports():
    """Module imports without error."""
    import tools.workflow.trace_logger  # noqa: F401


def test_trace_logger_functions_exist():
    """All public functions are present and callable."""
    from tools.workflow.trace_logger import (
        start_trace, log_event, close_trace,
        get_traces_for_task_type, get_recent_traces,
    )
    for fn in (start_trace, log_event, close_trace,
               get_traces_for_task_type, get_recent_traces):
        assert callable(fn), f"{fn} is not callable"


# ---------------------------------------------------------------------------
# start_trace
# ---------------------------------------------------------------------------

def test_start_trace_returns_trace_id():
    """start_trace returns a non-empty string trace_id."""
    from tools.workflow.trace_logger import start_trace
    tid = start_trace("task-test-001", "build", "icdev-build")
    assert isinstance(tid, str)
    assert tid.startswith("trace-")
    assert len(tid) > 6


def test_start_trace_writes_db_row():
    """start_trace inserts a row into agent_execution_traces."""
    from tools.workflow.trace_logger import start_trace
    from tools.db.storage import get_connection

    tid = start_trace("task-db-write-001", "test", "icdev-test")
    conn = get_connection()
    row = conn.execute(
        "SELECT trace_id, task_id, outcome FROM agent_execution_traces WHERE trace_id = ?",
        (tid,),
    ).fetchone()
    conn.close()

    assert row is not None, "start_trace did not write a DB row"
    outcome = row[2] if isinstance(row, (list, tuple)) else row["outcome"]
    assert outcome == "in_progress"


def test_start_trace_unique_ids():
    """Each start_trace call returns a unique trace_id."""
    from tools.workflow.trace_logger import start_trace
    ids = {start_trace(f"task-{i}", "build") for i in range(5)}
    assert len(ids) == 5, "trace_ids are not unique"


def test_start_trace_default_args():
    """start_trace works with only task_id provided."""
    from tools.workflow.trace_logger import start_trace
    tid = start_trace("task-defaults")
    assert tid.startswith("trace-")


# ---------------------------------------------------------------------------
# log_event
# ---------------------------------------------------------------------------

def test_log_event_appends_to_trace():
    """log_event appends an event to the events_json column."""
    import json
    from tools.workflow.trace_logger import start_trace, log_event
    from tools.db.storage import get_connection

    tid = start_trace("task-log-001", "build")
    log_event(tid, "tool_call", {"tool": "Read", "path": "/some/file.py"})
    log_event(tid, "llm_decision", {"decision": "proceed", "confidence": 0.9})

    conn = get_connection()
    row = conn.execute(
        "SELECT events_json FROM agent_execution_traces WHERE trace_id = ?",
        (tid,),
    ).fetchone()
    conn.close()

    raw = row[0] if isinstance(row, (list, tuple)) else row["events_json"]
    events = json.loads(raw)
    assert len(events) == 2
    assert events[0]["event_type"] == "tool_call"
    assert events[1]["event_type"] == "llm_decision"


def test_log_event_noop_on_bad_trace_id():
    """log_event on a nonexistent trace_id does not raise."""
    from tools.workflow.trace_logger import log_event
    log_event("trace-nonexistent-999", "milestone", {"msg": "should not crash"})


# ---------------------------------------------------------------------------
# close_trace
# ---------------------------------------------------------------------------

def test_close_trace_sets_outcome():
    """close_trace updates outcome and lesson_pattern."""
    from tools.workflow.trace_logger import start_trace, close_trace
    from tools.db.storage import get_connection

    tid = start_trace("task-close-001", "chore")
    close_trace(tid, "success", "success_first_try", "Completed in one pass")

    conn = get_connection()
    row = conn.execute(
        "SELECT outcome, lesson_pattern, improvement_notes, completed_at "
        "FROM agent_execution_traces WHERE trace_id = ?",
        (tid,),
    ).fetchone()
    conn.close()

    if isinstance(row, (list, tuple)):
        outcome, pattern, notes, completed = row
    else:
        outcome = row["outcome"]
        pattern = row["lesson_pattern"]
        notes = row["improvement_notes"]
        completed = row["completed_at"]

    assert outcome == "success"
    assert pattern == "success_first_try"
    assert "Completed" in notes
    assert completed != ""


def test_close_trace_valid_outcomes():
    """close_trace accepts all valid outcome values."""
    from tools.workflow.trace_logger import start_trace, close_trace
    for outcome in ("success", "failure", "timeout", "token_exhaustion", "partial"):
        tid = start_trace(f"task-outcome-{outcome}", "build")
        close_trace(tid, outcome)  # should not raise


def test_close_trace_noop_on_bad_trace_id():
    """close_trace on a nonexistent trace_id does not raise."""
    from tools.workflow.trace_logger import close_trace
    close_trace("trace-nonexistent-888", "success")


# ---------------------------------------------------------------------------
# get_traces_for_task_type
# ---------------------------------------------------------------------------

def test_get_traces_for_task_type_returns_list():
    """get_traces_for_task_type returns a list of dicts."""
    from tools.workflow.trace_logger import start_trace, close_trace, get_traces_for_task_type

    task_type = "nova-test-review"
    tid1 = start_trace("task-rev-001", task_type, "icdev-review")
    close_trace(tid1, "success")
    tid2 = start_trace("task-rev-002", task_type, "icdev-review")
    close_trace(tid2, "failure")

    traces = get_traces_for_task_type(task_type, limit=10)
    assert isinstance(traces, list)
    assert len(traces) >= 2

    for t in traces:
        assert "trace_id" in t
        assert "outcome" in t
        assert t["task_type"] == task_type


def test_get_traces_for_task_type_excludes_in_progress():
    """get_traces_for_task_type does not return in_progress traces."""
    from tools.workflow.trace_logger import start_trace, get_traces_for_task_type

    task_type = "nova-test-inprog"
    start_trace("task-inprog-001", task_type)  # NOT closed

    traces = get_traces_for_task_type(task_type, limit=10)
    in_prog = [t for t in traces if t.get("outcome") == "in_progress"]
    assert len(in_prog) == 0, "in_progress traces should be excluded"


def test_get_traces_for_task_type_empty_on_unknown():
    """Returns empty list for unknown task_type."""
    from tools.workflow.trace_logger import get_traces_for_task_type
    traces = get_traces_for_task_type("zzz-nonexistent-type-xyz", limit=10)
    assert traces == []


# ---------------------------------------------------------------------------
# get_recent_traces
# ---------------------------------------------------------------------------

def test_get_recent_traces_returns_list():
    """get_recent_traces returns a list."""
    from tools.workflow.trace_logger import get_recent_traces
    traces = get_recent_traces(limit=5)
    assert isinstance(traces, list)


def test_get_recent_traces_with_outcome_filter():
    """get_recent_traces with outcome_filter returns only matching rows."""
    from tools.workflow.trace_logger import start_trace, close_trace, get_recent_traces

    tid = start_trace("task-filter-001", "secure", "icdev-secure")
    close_trace(tid, "timeout")

    successes = get_recent_traces(limit=50, outcome_filter="timeout")
    assert all(t["outcome"] == "timeout" for t in successes), \
        "outcome_filter did not filter correctly"


def test_get_recent_traces_excludes_in_progress():
    """get_recent_traces (no filter) excludes in_progress traces."""
    from tools.workflow.trace_logger import start_trace, get_recent_traces

    start_trace("task-inprog-filter", "build")  # NOT closed

    traces = get_recent_traces(limit=100)
    in_prog = [t for t in traces if t["outcome"] == "in_progress"]
    assert len(in_prog) == 0


def test_get_recent_traces_dict_keys():
    """Each trace dict has the expected keys."""
    from tools.workflow.trace_logger import start_trace, close_trace, get_recent_traces

    tid = start_trace("task-keys-001", "docs", "icdev-docs")
    close_trace(tid, "success", "success_first_try")

    traces = get_recent_traces(limit=1, outcome_filter="success")
    assert len(traces) >= 1
    expected_keys = {"trace_id", "task_id", "task_type", "skill_used",
                     "outcome", "lesson_pattern", "started_at"}
    assert expected_keys.issubset(set(traces[0].keys()))
