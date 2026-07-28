"""dwo-mcp-03 — `node_type: mcp` in the template schema and the runner.

An mcp step names a TOOL_REGISTRY tool in `mcp_tool` instead of a script path
in `tool`; the runner must turn that into an mcp_executor.py invocation.
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

from tools.studio import workflow_runner as wr  # noqa: E402
from tools.studio.template_linter import VALID_NODE_TYPES, analyze  # noqa: E402


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


# ── Command construction ───────────────────────────────────────────────────

def test_mcp_step_builds_an_mcp_executor_invocation():
    cmd = wr._build_command(_step(), "proj-1", "run-1")

    assert cmd[0] == sys.executable
    assert Path(cmd[1]) == _ROOT / "tools/studio/executors/mcp_executor.py"

    flags = _flags(cmd)
    assert flags["--tool"] == "scan_dependencies"
    assert json.loads(flags["--params"]) == {"path": "tools/"}
    assert flags["--step-id"] == "scan"
    assert flags["--project-id"] == "proj-1"
    assert flags["--run-id"] == "run-1"
    assert flags["--json"] is True


def test_mcp_step_ignores_the_tool_field():
    """`tool` is not how an mcp step names its action — it must not be run."""
    cmd = wr._build_command(_step(tool="tools/some/other_script.py"), "proj-1")
    assert "other_script.py" not in " ".join(str(c) for c in cmd)
    assert Path(cmd[1]).name == "mcp_executor.py"


def test_params_default_to_an_empty_object():
    cmd = wr._build_command(_step(mcp_params=None), "proj-1")
    assert _flags(cmd)["--params"] == "{}"


def test_hand_authored_params_json_is_passed_through():
    cmd = wr._build_command(_step(mcp_params='{"path": "icdev/"}'), "proj-1")
    assert json.loads(_flags(cmd)["--params"]) == {"path": "icdev/"}


def test_injection_opt_outs_are_honoured():
    cmd = wr._build_command(
        _step(inject_project_id=False, inject_run_id=False, json_output=False),
        "proj-1",
        "run-1",
    )
    flags = _flags(cmd)
    assert "--project-id" not in flags
    assert "--run-id" not in flags
    assert "--json" not in flags
    assert flags["--tool"] == "scan_dependencies"


def test_missing_mcp_tool_yields_no_command():
    assert wr._build_command(_step(mcp_tool=""), "proj-1") == []


def test_tool_steps_are_unchanged():
    cmd = wr._build_command(
        {"id": "s1", "tool": "tools/testing/health_check.py", "args": {"scope": "all"}},
        "proj-1",
        "run-1",
    )
    assert Path(cmd[1]) == _ROOT / "tools/testing/health_check.py"
    flags = _flags(cmd)
    assert flags["--scope"] == "all"
    assert flags["--project-id"] == "proj-1"


# ── Step path resolution ───────────────────────────────────────────────────

def test_step_tool_path_resolves_mcp_nodes_to_the_shared_executor():
    assert wr._step_tool_path(_step()) == wr.MCP_EXECUTOR
    assert wr._step_tool_path({"tool": "tools/x.py"}) == "tools/x.py"
    assert wr._step_tool_path({"node_type": "human", "tool": ""}) == ""


def test_a_step_without_mcp_tool_is_skipped_with_a_specific_reason():
    result = wr._exec_step(_step(mcp_tool=""), "proj-1")
    assert result["status"] == "skipped"
    assert "mcp_tool" in result["stderr"]


# ── Template schema ────────────────────────────────────────────────────────

def test_linter_accepts_mcp_as_a_node_type():
    assert "mcp" in VALID_NODE_TYPES
    info = analyze([
        {"id": "a", "node_type": "tool"},
        {"id": "b", "node_type": "mcp", "depends_on": ["a"]},
    ])
    assert info["bad_node_types"] == []


def test_linter_still_rejects_an_unknown_node_type():
    info = analyze([{"id": "a", "node_type": "mcp_tool"}])
    assert info["bad_node_types"] == [("a", "mcp_tool")]


@pytest.mark.parametrize("doc", ["args/workflow_templates/README.md"])
def test_schema_doc_documents_the_mcp_node(doc):
    text = (_ROOT / doc).read_text(encoding="utf-8")
    assert "node_type: tool | human | approval | mcp" in text
    assert "mcp_params" in text
    assert "mcp_executor.py" in text
