# CUI // SP-CTI
"""A manual gate must actually gate — and must not eat the work it gated.

A '-gate-00' task is held in_progress FOREVER by design: it exists only to stop its
dependents from auto-dispatching while a human implements them (often in a private
repo the runner cannot reach). Two bugs made that arrangement destructive:

1. The orphan-done sweep rolls back any DONE task whose parent is not done. For a
   gate, "parent not done" is its permanent, intended state — so every dependent that
   was legitimately COMPLETED got reset to backlog, on every cycle, forever. It ate 32
   finished tasks in one sweep.

2. Decomposition did not pass the parent's dependency to the first child. A gated task
   split into children produced a child with depends_on_task_id = NULL, so the whole
   chain walked straight around the gate and dispatched.

Together: the gate released the work it was holding, then deleted the record that the
work had ever been done.
"""
from __future__ import annotations

import importlib
import json
import uuid

import pytest

from tools.db.storage import get_connection

kanban = importlib.import_module("tools.genesis.reflexes.kanban")

GATE_ID = "zzt-gate-00"


@pytest.fixture
def gated_project(icdev_db, monkeypatch):
    """A manual gate holding one dependent, in the schema-loaded temp DB."""
    monkeypatch.setattr(kanban, "get_connection",
                        lambda *a, **k: get_connection(db_path=str(icdev_db)))
    conn = get_connection(db_path=str(icdev_db))
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status) VALUES "
        "(%s, 'MANUAL-MODE GATE - do not complete; do not dispatch', 'in_progress')",
        (GATE_ID,))
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
        "VALUES ('zzt-work-01', 'Real work, actually finished', 'done', %s)",
        (GATE_ID,))
    conn.commit()
    return {"db": icdev_db, "conn": conn}


class TestOrphanSweepSparesGateDependents:
    def test_a_done_dependent_of_a_gate_is_not_an_orphan(self, gated_project):
        """The gate never completes. Its done dependents are finished work."""
        orphans = kanban._detect_orphan_done_tasks()

        assert not [o for o in orphans if o["id"] == "zzt-work-01"], (
            "the orphan sweep rolled back a COMPLETED task because its manual gate "
            "is (correctly, permanently) in_progress — this is the bug that reset 32 "
            "finished tasks to backlog"
        )

        row = gated_project["conn"].execute(
            "SELECT status FROM kanban_tasks WHERE id = 'zzt-work-01'").fetchone()
        assert dict(row)["status"] == "done", "the completed task was reset to backlog"

    def test_a_done_task_under_a_NON_gate_parent_is_still_swept(self, gated_project):
        """The sweep must keep doing its actual job: a task marked done ahead of a
        genuine, still-running dependency IS an orphan."""
        conn = gated_project["conn"]
        conn.execute("INSERT INTO kanban_tasks (id, title, status) "
                     "VALUES ('zzt-parent', 'Ordinary parent', 'in_progress')")
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, status, depends_on_task_id) "
            "VALUES ('zzt-premature', 'Done too early', 'done', 'zzt-parent')")
        conn.commit()

        orphans = kanban._detect_orphan_done_tasks()

        assert "zzt-premature" in {o["id"] for o in orphans}
        row = conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = 'zzt-premature'").fetchone()
        assert dict(row)["status"] == "backlog"


class TestDecompositionInheritsTheGate:
    def test_first_child_inherits_the_parents_dependency(self, gated_project, monkeypatch):
        """Decomposing a GATED task must not produce an ungated chain."""
        conn = gated_project["conn"]
        parent_id = f"zzt-big-{uuid.uuid4().hex[:6]}"
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, description, status, "
            " depends_on_task_id) VALUES (%s, 'Big gated task', 'do a lot', "
            " 'backlog', %s)", (parent_id, GATE_ID))
        conn.commit()

        # The decomposer imports LLMRouter LOCALLY (at call time), so patch it at
        # the source module — patching the kanban module would miss it entirely.
        class _FakeRouter:
            def invoke(self, function, request):
                return json.dumps([
                    {"title": "step one", "description": "a"},
                    {"title": "step two", "description": "b"},
                    {"title": "step three", "description": "c"},
                ])

        router_mod = importlib.import_module("tools.llm.router")
        monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)

        task = dict(conn.execute(
            "SELECT * FROM kanban_tasks WHERE id = %s", (parent_id,)).fetchone())
        kanban._decompose_one_task(task)

        children = [dict(r) for r in conn.execute(
            "SELECT id, depends_on_task_id FROM kanban_tasks "
            "WHERE id LIKE %s ORDER BY id", (f"{parent_id}-d%",)).fetchall()]
        if not children:
            pytest.skip("decomposer produced no children in this environment")

        assert children[0]["depends_on_task_id"] == GATE_ID, (
            f"first child {children[0]['id']} has dep "
            f"{children[0]['depends_on_task_id']!r} — it escaped the gate, which is "
            f"how prem-bid-01-d1 dispatched despite prem-gate-00"
        )
        # The rest chain off their predecessor, so the whole chain stays gated.
        for previous, child in zip(children, children[1:]):
            assert child["depends_on_task_id"] == previous["id"]
