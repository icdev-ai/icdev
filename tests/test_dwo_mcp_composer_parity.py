"""dwo-mcp-03-d2 — `node_type: mcp` parity between the composer and Studio.

The headless `workflow_composer` and Studio's `workflow_runner` must turn the
same mcp step into the same `mcp_executor.py` invocation; a template that runs
in the UI has to run identically from a cron job or an air-gapped shell.

These tests pin that equality directly — every case builds the command with
BOTH engines and asserts they match, so a change to one that is not mirrored
into the other fails here rather than at runtime.
"""
# CUI // SP-CTI
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.orchestration import workflow_composer as wc  # noqa: E402
from tools.studio import workflow_runner as wr  # noqa: E402


def _flags(cmd: list) -> dict:
    """Map --flag → value (True for valueless flags) from an argv list."""
    out: dict = {}
    i = 0
    while i < len(cmd):
        token = cmd[i]
        if isinstance(token, str) and token.startswith("--"):
            nxt = cmd[i + 1] if i + 1 < len(cmd) else None
            if nxt is not None and not str(nxt).startswith("--"):
                out[token] = nxt
                i += 2
                continue
            out[token] = True
        i += 1
    return out


def _step(**overrides) -> dict:
    step = {
        "id": "scan",
        "name": "Scan Dependencies",
        "node_type": "mcp",
        "mcp_tool": "scan_dependencies",
        "mcp_params": {"path": "tools/"},
    }
    step.update(overrides)
    return step


# ── The parity contract ────────────────────────────────────────────────────

# Each case is a step that exercises one axis of the mcp command builder.
PARITY_CASES = [
    pytest.param(_step(), "", id="baseline-no-run-id"),
    pytest.param(_step(), "run-1", id="with-run-id"),
    pytest.param(_step(mcp_params=None), "run-1", id="params-omitted"),
    pytest.param(_step(mcp_params={}), "run-1", id="params-empty-mapping"),
    pytest.param(_step(mcp_params='{"path": "icdev/"}'), "run-1", id="params-hand-authored-json"),
    pytest.param(_step(mcp_params="   "), "run-1", id="params-blank-string"),
    pytest.param(_step(mcp_params={"a": 1, "b": [1, 2], "c": None}), "run-1", id="params-nested"),
    pytest.param(_step(tool="tools/some/other_script.py"), "run-1", id="tool-field-present"),
    pytest.param(_step(args={"scope": "all"}), "run-1", id="args-present"),
    pytest.param(_step(inject_project_id=False), "run-1", id="no-project-id"),
    pytest.param(_step(inject_run_id=False), "run-1", id="no-run-id-injection"),
    pytest.param(_step(json_output=False), "run-1", id="no-json"),
    pytest.param(_step(id=""), "run-1", id="no-step-id"),
    pytest.param(_step(mcp_tool="  scan_dependencies  "), "run-1", id="tool-name-padded"),
    pytest.param(_step(mcp_tool=""), "run-1", id="no-mcp-tool"),
    pytest.param(_step(mcp_tool=None), "run-1", id="null-mcp-tool"),
]


@pytest.mark.parametrize("step,run_id", PARITY_CASES)
def test_composer_builds_the_same_command_as_studio(step, run_id):
    composed = wc._build_command(step, "proj-1", None, run_id)
    studio = wr._build_command(step, "proj-1", run_id)
    assert composed == studio


@pytest.mark.parametrize("step,run_id", PARITY_CASES)
def test_step_tool_path_agrees(step, run_id):
    assert wc._step_tool_path(step) == wr._step_tool_path(step)


def test_both_engines_name_the_same_executor():
    assert wc.MCP_EXECUTOR == wr.MCP_EXECUTOR


# ── Composer-side behaviour ────────────────────────────────────────────────

def test_composer_dispatches_an_mcp_step_to_the_executor():
    cmd = wc._build_command(_step(), "proj-1", None, "run-1")

    assert cmd[0] == sys.executable
    assert Path(cmd[1]) == _ROOT / "tools/studio/executors/mcp_executor.py"

    flags = _flags(cmd)
    assert flags["--tool"] == "scan_dependencies"
    assert json.loads(flags["--params"]) == {"path": "tools/"}
    assert flags["--step-id"] == "scan"
    assert flags["--project-id"] == "proj-1"
    assert flags["--run-id"] == "run-1"
    assert flags["--json"] is True


def test_step_args_and_overrides_are_not_forwarded_to_an_mcp_step():
    """An MCP tool takes its arguments from mcp_params only (schema README)."""
    cmd = wc._build_command(_step(args={"scope": "all"}), "proj-1", {"scope": "none"}, "run-1")
    joined = " ".join(str(c) for c in cmd)
    assert "--scope" not in joined
    assert json.loads(_flags(cmd)["--params"]) == {"path": "tools/"}


def test_missing_mcp_tool_yields_no_command():
    assert wc._build_command(_step(mcp_tool=""), "proj-1") == []


def test_tool_steps_are_unchanged_by_the_mcp_branch():
    cmd = wc._build_command(
        {"id": "s1", "tool": "tools/testing/health_check.py", "args": {"scope": "all"}},
        "proj-1",
    )
    assert Path(cmd[1]) == _ROOT / "tools/testing/health_check.py"
    flags = _flags(cmd)
    assert flags["--scope"] == "all"
    assert flags["--project-id"] == "proj-1"
    assert "--run-id" not in flags


def test_a_tool_step_takes_the_run_id_when_one_is_given():
    cmd = wc._build_command({"id": "s1", "tool": "tools/testing/health_check.py"}, "proj-1", None, "run-1")
    assert _flags(cmd)["--run-id"] == "run-1"


# ── Plan composition ───────────────────────────────────────────────────────

def _write_template(tmp_path, monkeypatch, steps: list, name: str = "mcp_parity") -> str:
    import yaml

    monkeypatch.setattr(wc, "TEMPLATE_DIR", tmp_path)
    (tmp_path / f"{name}.yaml").write_text(
        yaml.safe_dump({"description": "parity fixture", "steps": steps}),
        encoding="utf-8",
    )
    return name


def test_composed_plan_points_an_mcp_step_at_the_executor(tmp_path, monkeypatch):
    name = _write_template(tmp_path, monkeypatch, [_step()])
    plan = wc.compose_workflow(name, "proj-1", run_id="run-1")
    step = plan["steps"][0]

    assert step["tool"] == wc.MCP_EXECUTOR
    assert step["node_type"] == "mcp"
    assert step["command"] == wr._build_command(_step(), "proj-1", "run-1")
    assert plan["run_id"] == "run-1"


def test_an_mcp_step_without_a_tool_name_is_skipped_with_a_specific_reason(tmp_path, monkeypatch):
    name = _write_template(tmp_path, monkeypatch, [_step(mcp_tool="")])
    plan = wc.compose_workflow(name, "proj-1")
    results = wc.execute_workflow(plan)

    step = results["steps"][0]
    assert step["status"] == "skip"
    assert "mcp_tool" in step["error"]
