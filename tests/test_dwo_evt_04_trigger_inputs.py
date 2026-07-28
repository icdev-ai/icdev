"""dwo-evt-04 — trigger payload as run input, and the editor's trigger panel.

Acceptance under test:
  * a run started by a trigger exposes the event payload to its steps;
  * a manually started run with no inputs behaves exactly as today;
  * the editor can create, test (via simulate) and disable a trigger.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import run_memory, workflow_editor


@pytest.fixture(autouse=True)
def _studio_db(tmp_path, monkeypatch):
    """Point storage at a throwaway SQLite db built from the shared schema."""
    import sqlite3

    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.delenv(run_memory.RUN_ID_ENV, raising=False)
    return db_path


@pytest.fixture()
def source_and_workflow():
    """A minimal event source + workflow row for triggers to point at."""
    workflow_id = f"wf-{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) VALUES (%s, %s, %s)",
            (workflow_id, "evt-04 test wf", "steps: []"),
        )
        conn.commit()
    finally:
        conn.close()
    source = workflow_editor.create_event_source("evt-04 source", "manual")
    return source["source_id"], workflow_id


# ── input mapping ──────────────────────────────────────────


def test_resolve_path_walks_dicts_and_list_indices():
    event = {"payload": {"files": [{"name": "a.tf"}, {"name": "b.tf"}]}}
    assert workflow_editor.resolve_path(event, "payload.files.1.name") == "b.tf"
    assert workflow_editor.resolve_path(event, "payload.missing") is None
    assert workflow_editor.resolve_path(event, "payload.files.9.name") is None


def test_apply_input_mapping_omits_unresolved_paths():
    event = {"repo": "icdev", "meta": {"branch": "main"}}
    mapped = workflow_editor.apply_input_mapping(
        {"repo_name": "repo", "branch": "meta.branch", "absent": "meta.nope"}, event
    )
    # An unresolved path is omitted, not passed through as None, so a step can
    # distinguish "field absent" from "field explicitly null".
    assert mapped == {"repo_name": "icdev", "branch": "main"}


def test_empty_mapping_yields_no_inputs():
    assert workflow_editor.apply_input_mapping({}, {"a": 1}) == {}
    assert workflow_editor.apply_input_mapping(None, {"a": 1}) == {}


# ── trigger CRUD + filter matching ─────────────────────────


def test_create_test_and_disable_a_trigger(source_and_workflow):
    source_id, workflow_id = source_and_workflow
    trigger = workflow_editor.create_trigger(
        source_id,
        workflow_id,
        event_type="push",
        conditions=[{"field": "branch", "operator": "equals", "value": "main"}],
        input_mapping={"branch": "branch"},
    )
    trigger_id = trigger["trigger_id"]

    assert trigger["enabled"] is True
    assert workflow_editor.get_trigger(trigger_id)["input_mapping"] == {"branch": "branch"}
    assert any(t["trigger_id"] == trigger_id for t in workflow_editor.list_triggers(workflow_id))

    # Test (simulate) — a matching payload resolves inputs but starts nothing.
    match = workflow_editor.simulate_trigger(
        trigger_id, {"event_type": "push", "branch": "main"}
    )
    assert match["matched"] is True
    assert match["inputs"] == {"branch": "main"}
    assert match["executed"] is False and match["run_id"] is None

    # A non-matching payload explains itself rather than failing silently.
    miss = workflow_editor.simulate_trigger(
        trigger_id, {"event_type": "push", "branch": "release"}
    )
    assert miss["matched"] is False
    assert "branch" in miss["reason"]

    # The wrong event type is rejected on type, before conditions run.
    wrong_type = workflow_editor.simulate_trigger(
        trigger_id, {"event_type": "tag", "branch": "main"}
    )
    assert wrong_type["matched"] is False and "event_type" in wrong_type["reason"]

    # Disable — the switch the panel flips.
    workflow_editor.set_trigger_enabled(trigger_id, False)
    assert workflow_editor.get_trigger(trigger_id)["enabled"] is False
    disabled = workflow_editor.simulate_trigger(
        trigger_id, {"event_type": "push", "branch": "main"}
    )
    assert disabled["matched"] is False and "disabled" in disabled["reason"]


def test_trigger_event_audit_row_is_written(source_and_workflow):
    source_id, workflow_id = source_and_workflow
    event_id = workflow_editor.record_trigger_event(
        source_id, None, "push", {"branch": "main"}, False, None, "no trigger matched"
    )
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_trigger_events WHERE event_id = %s", (event_id,)
        ).fetchone()
    finally:
        conn.close()
    row = dict(row)
    # Non-matches are recorded too — a trigger that never fires stays diagnosable.
    assert row["matched"] == 0
    assert row["run_id"] is None
    assert row["reason"] == "no trigger matched"
    assert json.loads(row["payload_json"]) == {"branch": "main"}


# ── inputs reach the run ───────────────────────────────────


def test_start_run_persists_inputs_and_exposes_them_to_steps(monkeypatch, source_and_workflow):
    """A triggered run's payload is readable by a step through run memory."""
    _source_id, workflow_id = source_and_workflow
    from tools.studio import workflow_runner

    # Don't execute the DAG — this test is about the input channel, not steps.
    monkeypatch.setattr(workflow_runner.threading, "Thread", _NoopThread)

    inputs = {"branch": "main", "repo": "icdev"}
    run_id = workflow_runner.start_run(workflow_id, inputs=inputs)

    run = workflow_runner.get_run(run_id)
    assert json.loads(run["inputs_json"]) == inputs

    # This is exactly what a step does: ICDEV_RUN_ID is in its environment.
    monkeypatch.setenv(run_memory.RUN_ID_ENV, run_id)
    assert run_memory.get_inputs() == inputs
    assert run_memory.get(run_id, run_memory.INPUTS_KEY) == inputs


def test_manual_run_without_inputs_is_unchanged(monkeypatch, source_and_workflow):
    """The regression guard: no inputs means no inputs_json and no memory row."""
    _source_id, workflow_id = source_and_workflow
    from tools.studio import workflow_runner

    monkeypatch.setattr(workflow_runner.threading, "Thread", _NoopThread)

    run_id = workflow_runner.start_run(workflow_id)  # positional call, as today

    run = workflow_runner.get_run(run_id)
    assert run["inputs_json"] is None
    assert run["trigger_event_id"] is None
    # No stray "_inputs" row is written for a manual run.
    assert run_memory.get(run_id, run_memory.INPUTS_KEY) is None
    assert run_memory.get_inputs(run_id) == {}


def test_run_detail_badge_resolves_the_triggering_event(monkeypatch, source_and_workflow):
    source_id, workflow_id = source_and_workflow
    from tools.studio import workflow_runner

    monkeypatch.setattr(workflow_runner.threading, "Thread", _NoopThread)

    event_id = workflow_editor.record_trigger_event(
        source_id, "trg-x", "push", {"branch": "main"}, True, None, "matched"
    )
    run_id = workflow_runner.start_run(
        workflow_id, inputs={"branch": "main"}, trigger_event_id=event_id
    )

    badge = workflow_editor.get_run_trigger_event(run_id)
    assert badge is not None
    assert badge["event_id"] == event_id
    assert badge["payload"] == {"branch": "main"}
    assert badge["matched"] is True


def test_run_detail_badge_is_absent_for_a_manual_run(monkeypatch, source_and_workflow):
    _source_id, workflow_id = source_and_workflow
    from tools.studio import workflow_runner

    monkeypatch.setattr(workflow_runner.threading, "Thread", _NoopThread)
    run_id = workflow_runner.start_run(workflow_id)
    # None is what the badge tests to decide whether to render at all.
    assert workflow_editor.get_run_trigger_event(run_id) is None


class _NoopThread:
    """Stands in for threading.Thread so start_run does not execute the DAG."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass
