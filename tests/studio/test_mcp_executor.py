"""Unit tests for tools.studio.executors.mcp_executor — no DB, no LLM, air-gap safe.

Dispatch is exercised against a stub module injected into sys.modules rather
than a real registry tool, so the tests stay fast and deterministic.
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools.studio.executors import mcp_executor

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tools" / "studio" / "executors" / "mcp_executor.py"


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def stub_tool(monkeypatch):
    """Register a fake tool backed by an in-memory module."""
    mod = types.ModuleType("tests_mcp_executor_stub")
    mod.handle_echo = lambda args: {"echoed": args}

    def _boom(args):
        raise RuntimeError("handler exploded")

    mod.handle_boom = _boom
    monkeypatch.setitem(sys.modules, "tests_mcp_executor_stub", mod)

    from tools.mcp.tool_registry import TOOL_REGISTRY

    monkeypatch.setitem(TOOL_REGISTRY, "stub_echo", {
        "category": "testing",
        "module": "tests_mcp_executor_stub",
        "handler": "handle_echo",
        "description": "Echo params back.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mode": {"type": "string", "enum": ["fast", "slow"]},
            },
            "required": ["name"],
        },
    })
    monkeypatch.setitem(TOOL_REGISTRY, "stub_boom", {
        "category": "testing",
        "module": "tests_mcp_executor_stub",
        "handler": "handle_boom",
        "description": "Always raises.",
        "input_schema": {"type": "object", "properties": {}},
    })
    return mod


# ── Registry lookup ────────────────────────────────────────────────────────

def test_resolve_entry_returns_registry_entry(stub_tool):
    entry = mcp_executor.resolve_entry("stub_echo")
    assert entry["handler"] == "handle_echo"


def test_unknown_tool_suggests_closest_matches():
    with pytest.raises(LookupError) as exc:
        mcp_executor.resolve_entry("health_chek")
    msg = str(exc.value)
    assert "Unknown MCP tool 'health_chek'" in msg
    assert "health_check" in msg


def test_unknown_tool_falls_back_to_substring_match():
    # No close ratio match, but 'terraform' appears inside real tool names.
    msg = mcp_executor._unknown_tool_message(
        "terraform", ["terraform_plan", "terraform_apply", "health_check"]
    )
    assert "terraform_plan" in msg


def test_resource_name_is_rejected_as_not_a_tool():
    from tools.mcp.tool_registry import RESOURCE_REGISTRY

    name = next(iter(RESOURCE_REGISTRY))
    with pytest.raises(LookupError, match="is an MCP resource, not a tool"):
        mcp_executor.resolve_entry(name)


# ── Param parsing / validation ─────────────────────────────────────────────

def test_parse_params_empty_is_empty_dict():
    assert mcp_executor.parse_params("") == {}
    assert mcp_executor.parse_params("  ") == {}
    assert mcp_executor.parse_params("{}") == {}


def test_parse_params_rejects_malformed_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        mcp_executor.parse_params("not-json")


def test_parse_params_rejects_non_object():
    with pytest.raises(ValueError, match="must be a JSON object"):
        mcp_executor.parse_params("[1, 2]")


def test_validate_params_reports_missing_required_field(stub_tool):
    schema = mcp_executor.resolve_entry("stub_echo")["input_schema"]
    errors = mcp_executor.validate_params({}, schema)
    assert len(errors) == 1
    assert "'name' is a required property" in errors[0]


def test_validate_params_reports_wrong_type_and_bad_enum(stub_tool):
    schema = mcp_executor.resolve_entry("stub_echo")["input_schema"]
    errors = mcp_executor.validate_params({"name": 123, "mode": "sideways"}, schema)
    joined = " | ".join(errors)
    assert "name:" in joined and "not of type 'string'" in joined
    assert "mode:" in joined and "not one of" in joined


def test_validate_params_accepts_valid_payload(stub_tool):
    schema = mcp_executor.resolve_entry("stub_echo")["input_schema"]
    assert mcp_executor.validate_params({"name": "x", "mode": "fast"}, schema) == []


def test_empty_schema_validates_anything():
    assert mcp_executor.validate_params({"whatever": 1}, {}) == []


# ── Dispatch ───────────────────────────────────────────────────────────────

def test_run_dispatches_handler_and_wraps_result(stub_tool):
    payload = mcp_executor.run("stub_echo", {"name": "hello"})
    assert payload["result"] == {"echoed": {"name": "hello"}}
    assert payload["tool"] == "stub_echo"
    assert payload["handler"] == "tests_mcp_executor_stub.handle_echo"
    assert payload["duration_ms"] >= 0


def test_run_rejects_invalid_params_before_dispatch(stub_tool):
    calls = []
    stub_tool.handle_echo = lambda args: calls.append(args)
    with pytest.raises(ValueError, match="Invalid params for 'stub_echo'"):
        mcp_executor.run("stub_echo", {})
    assert calls == [], "handler must not be called when params fail validation"


def test_run_propagates_handler_exception(stub_tool):
    with pytest.raises(RuntimeError, match="handler exploded"):
        mcp_executor.run("stub_boom", {})


def test_run_raises_on_unimportable_module(stub_tool, monkeypatch):
    from tools.mcp.tool_registry import TOOL_REGISTRY

    monkeypatch.setitem(TOOL_REGISTRY, "stub_missing", {
        "category": "testing",
        "module": "tests_module_that_does_not_exist",
        "handler": "handle_x",
        "input_schema": {"type": "object", "properties": {}},
    })
    with pytest.raises(RuntimeError, match="Cannot load handler"):
        mcp_executor.run("stub_missing", {})


def test_non_serializable_result_is_coerced(stub_tool):
    stub_tool.handle_echo = lambda args: {"obj": object()}
    payload = mcp_executor.run("stub_echo", {"name": "x"})
    assert "repr" in payload["result"]
    json.dumps(payload)  # must not raise


# ── Run memory (dwo-mem-01 soft dependency) ────────────────────────────────

def test_run_memory_skipped_without_run_id():
    written, reason = mcp_executor.write_run_memory("", "s1", {"a": 1})
    assert written is False
    assert "run-id" in reason


def test_run_memory_written_under_step_id(monkeypatch):
    stored = {}
    fake = types.ModuleType("tools.studio.run_memory")
    fake.set = lambda run_id, key, value: stored.update({(run_id, key): value})
    monkeypatch.setitem(sys.modules, "tools.studio.run_memory", fake)

    written, reason = mcp_executor.write_run_memory("run-1", "step-7", {"a": 1})
    assert written is True and reason == ""
    assert stored[("run-1", "step:step-7")] == {"a": 1}


def test_run_memory_failure_never_raises(monkeypatch):
    fake = types.ModuleType("tools.studio.run_memory")

    def _fail(run_id, key, value):
        raise OSError("db down")

    fake.set = _fail
    monkeypatch.setitem(sys.modules, "tools.studio.run_memory", fake)

    written, reason = mcp_executor.write_run_memory("run-1", "s1", {})
    assert written is False
    assert "db down" in reason


def test_step_id_defaults_to_tool_name(stub_tool):
    payload = mcp_executor.run("stub_echo", {"name": "x"})
    assert payload["step_id"] == "mcp-stub_echo"
    assert payload["memory_key"] == "step:mcp-stub_echo"
    assert payload["memory_written"] is False


# ── CLI contract (subprocess — this is what the runner actually does) ───────

def _cli(*args) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=180,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_cli_dispatches_health_check():
    rc, out = _cli("--tool", "health_check", "--params", "{}")
    assert rc == 0
    assert out["status"] == "success"
    assert out["tool"] == "health_check"
    assert "result" in out


def test_cli_unknown_tool_exits_nonzero_with_suggestions():
    rc, out = _cli("--tool", "health_chek", "--params", "{}")
    assert rc == 1
    assert out["error_type"] == "unknown_tool"
    assert "health_check" in out["error"]


def test_cli_invalid_params_exits_nonzero():
    rc, out = _cli("--tool", "kg_search", "--params", "{}")
    assert rc == 1
    assert out["error_type"] == "invalid_params"
    assert "required property" in out["error"]


def test_cli_accepts_runner_injected_flags():
    """workflow_runner always appends --project-id/--run-id/--json."""
    rc, out = _cli("--tool", "health_check", "--params", "{}",
                   "--project-id", "p1", "--run-id", "r1", "--json")
    assert rc == 0
    assert out["status"] == "success"
