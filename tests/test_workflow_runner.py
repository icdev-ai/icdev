# CUI // SP-CTI
"""dwo-evt-04-d3 — run inputs reach step processes as DWF_RUN_INPUTS_JSON.

dwo-evt-04-d1 put the payload a run was started with on the run row
(`studio_workflow_runs.inputs_json`), but nothing downstream could read it: a
step subprocess had no way to see what its run was started with, so a
triggered run's mapped event fields died at the run row.

The runner now hands every step the run's inputs in the environment. The
absent/present distinction carries meaning and is what these tests pin:

  - inputs recorded  -> DWF_RUN_INPUTS_JSON holds the stored JSON verbatim
  - inputs NULL      -> the variable is absent, including when this process's
                        own environment already carries a stale one
"""
from __future__ import annotations

import json
import uuid

import pytest

from tools.db.storage import get_connection
from tools.studio import workflow_runner
from tools.studio.init_db import init_studio_tables

ENV_VAR = workflow_runner.RUN_INPUTS_ENV_VAR


@pytest.fixture(autouse=True)
def _studio_schema():
    init_studio_tables()


def _seed_run(inputs_json: str | None) -> str:
    """A run row carrying `inputs_json` exactly as start_run would store it."""
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO studio_workflows (workflow_id, name, template_yaml) "
            "VALUES (%s, %s, %s)",
            (workflow_id, "Inputs Test Workflow", "steps: []\n"),
        )
        conn.execute(
            "INSERT INTO studio_workflow_runs "
            "(run_id, workflow_id, workflow_name, status, inputs_json) "
            "VALUES (%s, %s, %s, 'running', %s)",
            (run_id, workflow_id, "Inputs Test Workflow", inputs_json),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def test_env_var_carries_run_inputs_when_present():
    inputs = {"repo": "icdev-ai/ICDev", "pr_number": 989}
    run_id = _seed_run(json.dumps(inputs))

    env = workflow_runner._build_step_env(run_id)

    assert ENV_VAR in env, f"{ENV_VAR} must be set when the run has inputs"
    assert json.loads(env[ENV_VAR]) == inputs


def test_env_var_is_the_stored_text_not_a_reserialization():
    """A step must see exactly what start_run persisted.

    Re-serializing would silently reorder keys and restyle separators, so a
    step hashing or diffing its inputs would not match the run row.
    """
    stored = '{"b": 2, "a": 1}'
    run_id = _seed_run(stored)

    assert workflow_runner._build_step_env(run_id)[ENV_VAR] == stored


def test_empty_input_set_is_still_exposed():
    """'{}' means "started with an empty input set" — distinct from no inputs."""
    run_id = _seed_run("{}")

    env = workflow_runner._build_step_env(run_id)

    assert env[ENV_VAR] == "{}"


def test_no_env_var_when_inputs_are_null():
    run_id = _seed_run(None)

    assert ENV_VAR not in workflow_runner._build_step_env(run_id)


def test_stale_inherited_value_is_stripped_when_inputs_are_null(monkeypatch):
    """Absent must always mean absent.

    The runner copies os.environ, and a workflow step can itself start a
    workflow. Without an explicit strip, a nested run with no inputs would
    inherit the OUTER run's payload and act on inputs that are not its own.
    """
    monkeypatch.setenv(ENV_VAR, '{"leaked": true}')
    run_id = _seed_run(None)

    assert ENV_VAR not in workflow_runner._build_step_env(run_id)


def test_no_env_var_without_a_run_id():
    """Ad-hoc step execution outside a run has no inputs to expose."""
    assert ENV_VAR not in workflow_runner._build_step_env("")


def test_unknown_run_id_yields_no_env_var():
    assert ENV_VAR not in workflow_runner._build_step_env("run-does-not-exist")


def test_existing_step_env_contract_is_unchanged():
    """ICDEV_RUN_ID (dwo-mem-01) and PYTHONPATH still ride along."""
    run_id = _seed_run(json.dumps({"a": 1}))

    env = workflow_runner._build_step_env(run_id)

    assert env["ICDEV_RUN_ID"] == run_id
    assert str(workflow_runner._ROOT) in env["PYTHONPATH"]


def test_lookup_failure_degrades_instead_of_killing_the_run(monkeypatch):
    """One failed SELECT must not take the whole step down."""
    def _boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(workflow_runner, "get_connection", _boom)

    env = workflow_runner._build_step_env("run-anything")

    assert ENV_VAR not in env
    assert env["ICDEV_RUN_ID"] == "run-anything"
