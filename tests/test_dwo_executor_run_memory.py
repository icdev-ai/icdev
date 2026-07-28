# CUI // SP-CTI
"""DWO-MEM-02 — executors read canvas and artifacts from run memory.

Before this, every executor re-queried the 'Generate IaC' step row and
re-parsed its stdout on each call, so inter-step data lived only in one
step's captured output. The runner now publishes the canvas and each step's
artifacts into `studio_run_memory`, and `executors/_base.py` reads from
there; the stdout re-parse survives only as the fallback for runs that
predate this.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import run_memory, workflow_runner
from tools.studio.executors._base import (
    IAC_STEP_NAME,
    get_iac_artifacts,
    resolve_canvas,
)
from tools.studio.init_db import init_studio_tables

TF = {"name": "main.tf", "path": "data/studio_artifacts/ndc/main.tf", "type": "tf"}
PLAYBOOK = {"name": "site.yml", "path": "data/studio_artifacts/ndc/site.yml", "type": "yml"}
REPORT = {"name": "Plan Report", "path": "data/studio_artifacts/ndc/plan.md", "type": "md"}


@pytest.fixture(autouse=True)
def _studio_schema():
    init_studio_tables()


def _seed_run(*, template_yaml: str = "steps: []\n",
              workflow_name: str = "Test Workflow") -> str:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) "
            "VALUES (%s, %s, %s)",
            (workflow_id, workflow_name, template_yaml),
        )
        conn.execute(
            "INSERT INTO studio_workflow_runs (run_id, workflow_id, workflow_name, status) "
            "VALUES (%s, %s, %s, 'running')",
            (run_id, workflow_id, workflow_name),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def _seed_step(run_id: str, step_name: str, artifacts: list) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflow_run_steps "
            "(step_run_id, run_id, step_id, step_name, tool, status, stdout) "
            "VALUES (%s, %s, %s, %s, '', 'success', %s)",
            (
                f"sr-{uuid.uuid4().hex[:12]}",
                run_id,
                step_name.lower().replace(" ", "-"),
                step_name,
                json.dumps({"artifacts": artifacts}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── Artifacts: the read path ───────────────────────────────────────────────

def test_artifacts_come_from_run_memory_with_no_step_row():
    """Memory alone is enough — no 'Generate IaC' row is queried."""
    run_id = _seed_run()
    run_memory.set(run_id, run_memory.ARTIFACTS_KEY, {IAC_STEP_NAME: [TF]})
    assert get_iac_artifacts(run_id) == [TF]


def test_run_memory_wins_over_the_stdout_row():
    run_id = _seed_run()
    _seed_step(run_id, IAC_STEP_NAME, [REPORT])
    run_memory.set(run_id, run_memory.ARTIFACTS_KEY, {IAC_STEP_NAME: [TF]})
    assert get_iac_artifacts(run_id) == [TF]


def test_falls_back_to_stdout_for_a_run_predating_run_memory():
    run_id = _seed_run()
    _seed_step(run_id, IAC_STEP_NAME, [TF, PLAYBOOK])
    assert get_iac_artifacts(run_id) == [TF, PLAYBOOK]


def test_another_steps_artifacts_do_not_leak_in():
    """Memory is keyed by step name so a later step's .tf cannot be picked up."""
    run_id = _seed_run()
    run_memory.set(
        run_id,
        run_memory.ARTIFACTS_KEY,
        {IAC_STEP_NAME: [TF], "Terraform Apply": [{"name": "drift.tf",
                                                   "path": "x/drift.tf",
                                                   "type": "tf"}]},
    )
    assert get_iac_artifacts(run_id) == [TF]


def test_no_run_id_returns_empty():
    assert get_iac_artifacts("") == []


def test_unknown_run_returns_empty():
    assert get_iac_artifacts("run-does-not-exist") == []


# ── Canvas: the read path ──────────────────────────────────────────────────

def test_canvas_comes_from_run_memory():
    run_id = _seed_run(template_yaml="steps: []\n", workflow_name="Unnamed")
    run_memory.set(run_id, run_memory.CANVAS_KEY, "aadc")
    assert resolve_canvas(run_id) == "aadc"


def test_canvas_memory_wins_over_the_template_key():
    run_id = _seed_run(template_yaml="canvas: ddc\nsteps: []\n")
    run_memory.set(run_id, run_memory.CANVAS_KEY, "qdc")
    assert resolve_canvas(run_id) == "qdc"


def test_canvas_falls_back_to_template_when_memory_is_empty():
    run_id = _seed_run(template_yaml="canvas: ohc\nsteps: []\n")
    assert resolve_canvas(run_id) == "ohc"


def test_explicit_hint_still_wins_over_memory():
    run_id = _seed_run()
    run_memory.set(run_id, run_memory.CANVAS_KEY, "ddc")
    assert resolve_canvas(run_id, "MDC") == "mdc"


# ── The runner: the write path ─────────────────────────────────────────────

def test_runner_publishes_the_template_canvas():
    run_id = _seed_run()
    workflow_runner._remember_canvas(run_id, "aimc")
    assert run_memory.get(run_id, run_memory.CANVAS_KEY) == "aimc"


def test_runner_skips_an_undeclared_canvas():
    """No `canvas:` in the template must leave memory untouched, not blank it."""
    run_id = _seed_run()
    workflow_runner._remember_canvas(run_id, "")
    assert run_memory.get(run_id, run_memory.CANVAS_KEY) is None


def test_runner_accumulates_artifacts_across_steps():
    run_id = _seed_run()
    workflow_runner._remember_artifacts(run_id, IAC_STEP_NAME, [TF])
    workflow_runner._remember_artifacts(run_id, "Terraform Plan", [REPORT])
    assert run_memory.get(run_id, run_memory.ARTIFACTS_KEY) == {
        IAC_STEP_NAME: [TF],
        "Terraform Plan": [REPORT],
    }
    assert get_iac_artifacts(run_id) == [TF]


def test_runner_ignores_a_step_that_declares_nothing():
    run_id = _seed_run()
    workflow_runner._remember_artifacts(run_id, "Approve", [])
    assert run_memory.get(run_id, run_memory.ARTIFACTS_KEY) is None


def test_step_artifacts_tolerates_output_that_is_not_the_contract():
    assert workflow_runner._step_artifacts({"stdout": None}) == []
    assert workflow_runner._step_artifacts({"stdout": ""}) == []
    assert workflow_runner._step_artifacts({"stdout": "not json"}) == []
    assert workflow_runner._step_artifacts({"stdout": "[1, 2]"}) == []
    assert workflow_runner._step_artifacts({"stdout": '{"status": "ok"}'}) == []
    assert workflow_runner._step_artifacts(
        {"stdout": json.dumps({"artifacts": [TF]})}
    ) == [TF]
