# CUI // SP-CTI
"""Tests for the decomposed-parent auto-close and decomposer
placeholder-filter fixes (2026-04-19 follow-ups).

Regression targets:
  1. _auto_close_decomposed_parent — when the last open child completes,
     the parent row flips from 'decomposed' to 'done' with a bypass
     verification audit row.
  2. _decompose_batch_tasks — lines like "All subjects share the same
     rule. A single fix may resolve all." and "Source prediction IDs:
     ..." are stripped from the Subjects block (no orphan child spawn).

The tests mutate the live kanban_tasks/verifications tables using
ephemeral rows with a dedicated source_prediction_id so they can clean
up after themselves. They require the production kg/db backend (same as
the /ask E2E tests).
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def ephemeral_parent_and_children():
    """Insert one decomposed parent + two open children, yield IDs, then clean up."""
    if get_connection is None:
        pytest.skip("get_connection unavailable")
    sp = f"op-test-auto-close-{uuid.uuid4().hex[:10]}"
    parent_id = f"task-test-parent-{uuid.uuid4().hex[:8]}"
    child_a = f"task-test-child-a-{uuid.uuid4().hex[:6]}"
    child_b = f"task-test-child-b-{uuid.uuid4().hex[:6]}"
    now = _now()

    conn = get_connection()
    cur = conn.cursor()
    for tid, title, status in [
        (parent_id, "[Batch] test_rule: 2 gap findings to address", "decomposed"),
        (child_a, "test_rule gap: subject-a", "backlog"),
        (child_b, "test_rule gap: subject-b", "backlog"),
    ]:
        cur.execute(
            "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
            "status, executor_type, source_prediction_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, title, "test fixture", "chore", "low", status, "claude_cli", sp, now, now),
        )
    conn.commit()
    conn.close()

    yield {"sp": sp, "parent": parent_id, "child_a": child_a, "child_b": child_b}

    # Cleanup
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM kanban_verifications WHERE task_id IN (?, ?, ?)",
                (parent_id, child_a, child_b))
    cur.execute("DELETE FROM kanban_tasks WHERE source_prediction_id = ?", (sp,))
    conn.commit()
    conn.close()


def test_auto_close_waits_while_siblings_open(ephemeral_parent_and_children):
    """Parent must NOT close when some siblings are still open."""
    from tools.genesis.reflexes.kanban import _auto_close_decomposed_parent

    ids = ephemeral_parent_and_children
    conn = get_connection()
    cur = conn.cursor()
    # Mark only child_a done; child_b still backlog.
    cur.execute(
        "UPDATE kanban_tasks SET status='done', completed_at=?, updated_at=? WHERE id=?",
        (_now(), _now(), ids["child_a"]),
    )
    conn.commit()
    conn.close()

    closed = _auto_close_decomposed_parent(ids["child_a"], actor="test")
    assert closed is None, "parent must stay decomposed while siblings are open"

    conn = get_connection()
    row = conn.cursor().execute(
        "SELECT status FROM kanban_tasks WHERE id=?", (ids["parent"],)
    ).fetchone()
    conn.close()
    assert dict(row)["status"] == "decomposed"


def test_auto_close_fires_when_last_child_completes(ephemeral_parent_and_children):
    """When the LAST open child completes, parent auto-closes to done."""
    from tools.genesis.reflexes.kanban import _auto_close_decomposed_parent

    ids = ephemeral_parent_and_children
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE kanban_tasks SET status='done', completed_at=?, updated_at=? WHERE id=?",
                (_now(), _now(), ids["child_a"]))
    cur.execute("UPDATE kanban_tasks SET status='done', completed_at=?, updated_at=? WHERE id=?",
                (_now(), _now(), ids["child_b"]))
    conn.commit()
    conn.close()

    closed = _auto_close_decomposed_parent(ids["child_b"], actor="test")
    assert closed == ids["parent"], f"expected parent {ids['parent']} closed, got {closed}"

    conn = get_connection()
    cur = conn.cursor()
    row = cur.execute("SELECT status, completed_at FROM kanban_tasks WHERE id=?",
                      (ids["parent"],)).fetchone()
    d = dict(row)
    assert d["status"] == "done"
    assert d["completed_at"] is not None

    # Bypass verification row must be present so guard-22 stays consistent.
    vrow = cur.execute(
        "SELECT result, reason FROM kanban_verifications WHERE task_id=? "
        "ORDER BY verified_at DESC LIMIT 1",
        (ids["parent"],),
    ).fetchone()
    conn.close()
    assert vrow is not None
    v = dict(vrow)
    assert v["result"] == "bypassed"
    assert "auto_close" in v["reason"].lower() or "last child" in v["reason"].lower()


def test_auto_close_returns_none_for_non_child():
    """Tasks with no source_prediction_id must no-op safely."""
    from tools.genesis.reflexes.kanban import _auto_close_decomposed_parent
    assert _auto_close_decomposed_parent("task-does-not-exist", actor="test") is None


def test_decomposer_skips_placeholder_subjects():
    """Meta-subjects like 'All subjects share the same rule' must be filtered."""
    from tools.genesis.reflexes.kanban import _decompose_batch_tasks

    if get_connection is None:
        pytest.skip("get_connection unavailable")
    sp = f"op-test-placeholder-{uuid.uuid4().hex[:10]}"
    parent_id = f"task-test-placeholder-{uuid.uuid4().hex[:8]}"
    now = _now()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, "
        "status, executor_type, source_prediction_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (parent_id, "[Batch] test_rule: placeholder-only subjects",
         "Subjects:\n- All subjects share the same rule. A single fix may resolve all.\n- Source prediction IDs: op-x, op-y\n- tools/real/one.py",
         "chore", "low", "backlog", "claude_cli", sp, now, now),
    )
    conn.commit()

    tasks_in = [{
        "id": parent_id,
        "title": "[Batch] test_rule: placeholder-only subjects",
        "description": ("Subjects:\n"
                        "- All subjects share the same rule. A single fix may resolve all.\n"
                        "- Source prediction IDs: op-x, op-y\n"
                        "- tools/real/one.py"),
        "priority": "low",
        "task_type": "chore",
        "source_prediction_id": sp,
    }]
    try:
        _decompose_batch_tasks(tasks_in, conn)
        # Only the real subject should have been materialized — 2 placeholder lines skipped.
        created = cur.execute(
            "SELECT id, title FROM kanban_tasks "
            "WHERE source_prediction_id = ? AND id <> ?",
            (sp, parent_id),
        ).fetchall()
        created_titles = [dict(r)["title"] for r in created]
        assert len(created) == 1, f"expected 1 child, got {len(created)}: {created_titles}"
        assert "tools/real/one.py" in created_titles[0]
        # No placeholder children
        for t in created_titles:
            assert "All subjects share" not in t
            assert "Source prediction IDs" not in t
    finally:
        cur.execute("DELETE FROM kanban_tasks WHERE source_prediction_id = ?", (sp,))
        conn.commit()
        conn.close()
# CUI // SP-CTI
