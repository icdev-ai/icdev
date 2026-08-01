"""dwo-evt-04-d2 — start_run(inputs=...) persists the payload a run began with.

d1 added the nullable inputs_json column; this is the writer. A triggered run
computes its inputs from the event via the trigger's input_mapping_json, and
that payload has to survive on the run row — otherwise "what was this run
started with" is only answerable by re-reading the event and re-applying the
mapping, and is not answerable at all for a manual run.

Acceptance:
  - start_run(wf, project, inputs={...}) persists the inputs as JSON.
  - start_run(wf) — no inputs — still records NULL and is otherwise unchanged.
  - {} is recorded as an empty object, distinct from NULL.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tools.studio import workflow_runner
from tools.studio.init_db import STUDIO_TABLES

WORKFLOW = {"workflow_id": "wf-1", "name": "Demo Workflow", "nodes": [], "edges": []}


@pytest.fixture(autouse=True)
def _runs_db(tmp_path, monkeypatch):
    """Throwaway SQLite db holding only the runs table, plus an inert worker.

    The worker is stubbed so start_run's thread does no work: this exercises
    the run-record write, not workflow execution.
    """
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(STUDIO_TABLES["studio_workflow_runs"])
    conn.commit()
    conn.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    # start_run imports get_workflow inside the function body, so patch the
    # module it imports from, not a name bound on workflow_runner.
    editor = __import__("tools.studio.workflow_editor", fromlist=["get_workflow"])
    monkeypatch.setattr(editor, "get_workflow", lambda wid: dict(WORKFLOW, workflow_id=wid))
    monkeypatch.setattr(workflow_runner, "_worker", lambda *a, **kw: None)
    return db_path


def _inputs_json(db_path, run_id):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT inputs_json FROM studio_workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row is not None, f"no run row written for {run_id}"
        return row[0]
    finally:
        conn.close()


def test_inputs_are_persisted_as_json(_runs_db):
    payload = {"repo": "icdev", "pr": 969, "labels": ["studio", "dwo"]}
    run_id = workflow_runner.start_run("wf-1", "proj-a", inputs=payload)

    assert json.loads(_inputs_json(_runs_db, run_id)) == payload


def test_inputs_may_be_passed_positionally(_runs_db):
    # The documented signature is (workflow_id, project_id, inputs).
    run_id = workflow_runner.start_run("wf-1", "proj-a", {"k": "v"})

    assert json.loads(_inputs_json(_runs_db, run_id)) == {"k": "v"}


def test_no_inputs_still_works_and_records_null(_runs_db):
    """Backward compatibility: every caller that predates inputs is unaffected."""
    run_id = workflow_runner.start_run("wf-1")

    assert run_id.startswith("run-")
    assert _inputs_json(_runs_db, run_id) is None


def test_no_inputs_leaves_the_rest_of_the_run_row_unchanged(_runs_db):
    run_id = workflow_runner.start_run("wf-1", "proj-b")

    conn = sqlite3.connect(str(_runs_db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row["workflow_id"] == "wf-1"
    assert row["workflow_name"] == "Demo Workflow"
    assert row["project_id"] == "proj-b"
    assert row["status"] == "pending"
    assert row["started_at"]


def test_empty_inputs_is_recorded_and_stays_distinct_from_none(_runs_db):
    empty = workflow_runner.start_run("wf-1", inputs={})
    absent = workflow_runner.start_run("wf-1")

    assert _inputs_json(_runs_db, empty) == "{}"
    assert _inputs_json(_runs_db, absent) is None


def test_non_dict_inputs_are_rejected_without_creating_a_run(_runs_db):
    with pytest.raises(ValueError, match="dict"):
        workflow_runner.start_run("wf-1", "proj-a", inputs=["not", "a", "dict"])

    conn = sqlite3.connect(str(_runs_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM studio_workflow_runs").fetchone()[0] == 0
    finally:
        conn.close()


def test_unknown_workflow_still_raises(_runs_db, monkeypatch):
    editor = __import__("tools.studio.workflow_editor", fromlist=["get_workflow"])
    monkeypatch.setattr(editor, "get_workflow", lambda wid: None)

    with pytest.raises(ValueError, match="Workflow not found"):
        workflow_runner.start_run("nope", inputs={"a": 1})
