# CUI // SP-CTI
"""Unit tests for SAG toolset bundles + handler dispatch (sag-reg-02).

DB-independent: no chat/agent_loop tables are touched. Exercises the safety gate
seam, source-aware invocation with task_id/stop_event injection, YAML bundle
loading/resolution, the write_file/run_command mutating tools, and build_toolset.
"""
from __future__ import annotations

import importlib
import threading

import pytest

from tools.agent_runtime.discovery import ToolSpec, schema_from_callable
from tools.agent_runtime.dispatch import (
    build_handlers,
    default_safety_gate,
    make_handler,
)


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------
def test_default_gate_allows_read_only():
    allowed, reason = default_safety_gate("read_file", {}, True)
    assert allowed is True
    assert reason == ""


def test_default_gate_blocks_mutation_by_default(monkeypatch):
    monkeypatch.delenv("ICDEV_SAG_ALLOW_MUTATION", raising=False)
    allowed, reason = default_safety_gate("write_file", {}, False)
    assert allowed is False
    assert "sag-safe-01" in reason


def test_default_gate_allows_mutation_with_env(monkeypatch):
    monkeypatch.setenv("ICDEV_SAG_ALLOW_MUTATION", "1")
    allowed, _ = default_safety_gate("write_file", {}, False)
    assert allowed is True


# ---------------------------------------------------------------------------
# make_handler — source-aware invocation + injection
# ---------------------------------------------------------------------------
def _always_allow(_n, _i, _ro):
    return True, ""


def test_handler_blocks_when_gate_denies():
    spec = ToolSpec(name="danger", schema={}, source="mcp", read_only=False,
                    module="m", handler="h")

    def deny(_n, _i, _ro):
        return False, "nope"

    h = make_handler(spec, gate=deny)
    out = h({}, None)
    assert out.startswith("blocked:")
    assert "nope" in out


def test_handler_mcp_source_calls_with_dict(monkeypatch):
    d = importlib.import_module("tools.agent_runtime.dispatch")
    captured = {}

    def fake_handler(args):
        captured["args"] = args
        return {"ok": True, "echo": args.get("x")}

    monkeypatch.setattr(d, "_resolve", lambda mod, fn: fake_handler)
    spec = ToolSpec(name="get_x", schema={}, source="mcp", read_only=True,
                    module="m", handler="handle_x")
    h = make_handler(spec, gate=_always_allow)
    out = h({"x": 7}, None)
    assert captured["args"] == {"x": 7}
    assert '"echo": 7' in out  # JSON-serialised dict result


def test_handler_mcp_injects_stop_and_task_id(monkeypatch):
    d = importlib.import_module("tools.agent_runtime.dispatch")
    seen = {}

    def fake_handler(args, stop_event=None, task_id=None):
        seen["stop"] = stop_event
        seen["task_id"] = task_id
        return "done"

    monkeypatch.setattr(d, "_resolve", lambda mod, fn: fake_handler)
    spec = ToolSpec(name="mutate", schema={}, source="mcp", read_only=True,
                    module="m", handler="h")
    ev = threading.Event()
    h = make_handler(spec, gate=_always_allow, task_id="task-1")
    assert h({}, ev) == "done"
    assert seen["stop"] is ev
    assert seen["task_id"] == "task-1"


def test_handler_decorated_source_maps_named_kwargs():
    def echo(name: str, count: int = 1) -> str:
        return f"{name}x{count}"

    schema = schema_from_callable(echo, name="echo")
    spec = ToolSpec(name="echo", schema=schema, source="decorated",
                    read_only=True, callable=echo)
    h = make_handler(spec, gate=_always_allow)
    # extra keys are ignored; missing-with-default is fine
    assert h({"name": "a", "count": 3, "junk": 9}, None) == "ax3"


def test_handler_builtin_source_uses_builtin_handlers():
    spec = ToolSpec(name="read_file", schema={}, source="builtin", read_only=True)
    called = {}

    def fake_builtin(inp, stop):
        called["inp"] = inp
        return "file-content"

    h = make_handler(spec, gate=_always_allow,
                     builtin_handlers={"read_file": fake_builtin})
    assert h({"path": "x"}, None) == "file-content"
    assert called["inp"] == {"path": "x"}


def test_handler_never_raises_on_exception(monkeypatch):
    d = importlib.import_module("tools.agent_runtime.dispatch")

    def boom(args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(d, "_resolve", lambda mod, fn: boom)
    spec = ToolSpec(name="get_x", schema={}, source="mcp", read_only=True,
                    module="m", handler="h")
    h = make_handler(spec, gate=_always_allow)
    out = h({}, None)
    # The contract this test guards is "a raising handler never propagates".
    # The reported shape changed with arr-tax-01: a failure now carries its
    # disposition and type instead of the flat "error executing X: <exc>" that
    # made a network blip and a code bug indistinguishable. RuntimeError is a
    # code-level fault, so it is terminal — not retried.
    assert "error [terminal]" in out
    assert "type=RuntimeError" in out
    assert "get_x" in out and "kaboom" in out


def test_build_handlers_covers_all_specs(monkeypatch):
    d = importlib.import_module("tools.agent_runtime.dispatch")
    monkeypatch.setattr(d, "_resolve", lambda mod, fn: (lambda args: "ok"))
    reg = {
        "a": ToolSpec(name="a", schema={}, source="mcp", read_only=True, module="m", handler="h"),
        "b": ToolSpec(name="b", schema={}, source="mcp", read_only=True, module="m", handler="h"),
    }
    handlers = build_handlers(reg, safety_gate=_always_allow)
    assert set(handlers) == {"a", "b"}
    assert handlers["a"]({}, None) == "ok"


# ---------------------------------------------------------------------------
# mutating_tools
# ---------------------------------------------------------------------------
def test_write_file_rejects_escape():
    from tools.agent_runtime.mutating_tools import write_file

    out = write_file("../../etc/passwd", "x")
    assert out.startswith("error: path escapes")


def test_write_file_writes_within_repo(tmp_path, monkeypatch):
    import tools.agent_runtime.mutating_tools as mt

    monkeypatch.setattr(mt, "_REPO_ROOT", tmp_path)
    out = mt.write_file("sub/dir/note.txt", "hello")
    assert "wrote 5 bytes" in out
    assert (tmp_path / "sub" / "dir" / "note.txt").read_text() == "hello"


def test_run_command_refuses_non_allowlisted():
    from tools.agent_runtime.mutating_tools import run_command

    out = run_command("rm -rf /")
    assert "not in allowlist" in out or "skipped" in out


def test_mutating_tools_are_discovered():
    from tools.agent_runtime.discovery import discover_decorated

    specs = discover_decorated(["tools.agent_runtime.mutating_tools"])
    names = {s.name for s in specs}
    assert {"write_file", "run_command"} <= names
    assert all(s.read_only is False for s in specs)


# ---------------------------------------------------------------------------
# toolsets — bundle loading + resolution
# ---------------------------------------------------------------------------
def test_load_bundles_reads_yaml():
    from tools.agent_runtime.toolsets import load_bundles

    bundles = load_bundles()
    assert "compliance" in bundles
    assert "file" in bundles
    assert "read_file" in bundles["file"]["tools"]


def test_resolve_bundles_union():
    from tools.agent_runtime.toolsets import resolve_bundles

    names = resolve_bundles(["file", "kanban"])
    assert "read_file" in names
    assert "kanban_list_tasks" in names


def test_resolve_unknown_bundle_raises():
    from tools.agent_runtime.toolsets import ToolsetError, resolve_bundles

    with pytest.raises(ToolsetError):
        resolve_bundles(["nonexistent"])


def test_list_bundles_marks_mutating():
    from tools.agent_runtime.toolsets import list_bundles

    by_name = {b["name"]: b for b in list_bundles()}
    assert by_name["file"]["mutating"] is True
    assert by_name["compliance"]["mutating"] is False


def test_build_toolset_returns_tools_and_handlers():
    from tools.agent_runtime.toolsets import build_toolset

    tools, handlers = build_toolset(["file"], safety_gate=_always_allow)
    names = {(t.get("function") or {}).get("name") for t in tools}
    assert "read_file" in names
    assert "write_file" in names
    # handlers callable and keyed by the same names
    assert set(handlers) >= {"read_file", "write_file"}
    assert callable(handlers["read_file"])


def test_all_discovered_tools_includes_mutating_and_mcp():
    from tools.agent_runtime.toolsets import all_discovered_tools

    tools = all_discovered_tools()
    names = {t["name"] for t in tools}
    assert "write_file" in names       # decorated
    assert "read_file" in names        # builtin
    # every entry carries a schema with a function name
    assert all((t["schema"].get("function") or {}).get("name") for t in tools)
