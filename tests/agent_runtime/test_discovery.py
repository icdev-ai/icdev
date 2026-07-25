# CUI // SP-CTI
"""Unit tests for SAG tool auto-discovery + schema generation (sag-reg-01).

DB-independent: exercises pure schema synthesis, the ``@tool`` decorator, MCP
derivation (using a fake registry via shim-aware monkeypatch), availability
checks, and the JSON cache round-trip. No chat/agent_loop tables are touched.
"""
from __future__ import annotations

import importlib
import json
from typing import Literal, Optional

from tools.agent_runtime.discovery import (
    ToolSpec,
    discover_decorated,
    load_cache,
    parse_docstring,
    python_type_to_json_schema,
    schema_from_callable,
    schema_from_mcp_entry,
    to_openai_tools,
    tool,
    write_cache,
)


# ---------------------------------------------------------------------------
# python_type_to_json_schema
# ---------------------------------------------------------------------------
def test_primitive_type_mapping():
    assert python_type_to_json_schema(str) == {"type": "string"}
    assert python_type_to_json_schema(int) == {"type": "integer"}
    assert python_type_to_json_schema(float) == {"type": "number"}
    assert python_type_to_json_schema(bool) == {"type": "boolean"}


def test_optional_unwraps_to_inner_type():
    assert python_type_to_json_schema(Optional[int]) == {"type": "integer"}


def test_list_type_has_items():
    frag = python_type_to_json_schema(list[str])
    assert frag["type"] == "array"
    assert frag["items"] == {"type": "string"}


def test_literal_becomes_enum():
    frag = python_type_to_json_schema(Literal["a", "b"])
    assert frag["type"] == "string"
    assert frag["enum"] == ["a", "b"]


def test_unknown_type_defaults_to_string():
    class Weird:  # noqa: D401
        pass

    assert python_type_to_json_schema(Weird) == {"type": "string"}


# ---------------------------------------------------------------------------
# parse_docstring
# ---------------------------------------------------------------------------
def test_parse_docstring_summary_and_args():
    doc = """Do a thing to the widget.

    Args:
        kind (str): the widget kind.
        count: how many.

    Returns:
        A string.
    """
    summary, params = parse_docstring(doc)
    assert summary == "Do a thing to the widget."
    assert params["kind"] == "the widget kind."
    assert params["count"] == "how many."


def test_parse_docstring_empty():
    assert parse_docstring(None) == ("", {})
    assert parse_docstring("") == ("", {})


# ---------------------------------------------------------------------------
# schema_from_callable
# ---------------------------------------------------------------------------
def test_schema_from_callable_signature_and_required():
    def sample(kind: str, count: int = 3, stop_event=None) -> str:
        """List widgets.

        Args:
            kind (str): the widget kind.
            count (int): how many.
        """
        return ""

    schema = schema_from_callable(sample, read_only=True)
    fn = schema["function"]
    assert fn["name"] == "sample"
    assert fn["description"] == "List widgets."
    props = fn["parameters"]["properties"]
    # stop_event is injected plumbing — excluded from the model-facing schema
    assert set(props) == {"kind", "count"}
    assert props["kind"] == {"type": "string", "description": "the widget kind."}
    assert props["count"]["type"] == "integer"
    # only kind is required (count has a default)
    assert fn["parameters"]["required"] == ["kind"]
    assert schema["is_read_only"] is True
    assert fn["is_read_only"] is True


def test_schema_from_callable_no_params():
    def ping() -> str:
        """Ping."""
        return "pong"

    schema = schema_from_callable(ping)
    assert schema["function"]["parameters"] == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# @tool decorator + discover_decorated
# ---------------------------------------------------------------------------
def test_tool_decorator_attaches_schema_and_stays_callable():
    @tool(read_only=True)
    def echo(text: str) -> str:
        """Echo text back."""
        return text

    assert echo("hi") == "hi"  # unchanged, still callable
    assert echo.__tool_schema__["function"]["name"] == "echo"
    assert echo.__tool_meta__["read_only"] is True


def test_tool_decorator_bare_usage():
    @tool
    def bare(x: int) -> str:
        """Bare tool."""
        return str(x)

    assert bare.__tool_schema__["function"]["name"] == "bare"


def test_discover_decorated_from_module(tmp_path):
    # discover tools defined on the discovery module's own test double: use this
    # test module itself, which has @tool-decorated functions above at import time
    specs = discover_decorated([__name__])
    names = {s.name for s in specs}
    # echo/bare are defined inside test functions (local) so won't appear; define
    # a module-level decorated tool to assert discovery picks module-level ones.
    assert "module_level_tool" in names
    spec = next(s for s in specs if s.name == "module_level_tool")
    assert spec.source == "decorated"
    assert spec.module == __name__


@tool(read_only=True)
def module_level_tool(name: str) -> str:
    """A module-level tool for discovery tests."""
    return name


# ---------------------------------------------------------------------------
# MCP derivation
# ---------------------------------------------------------------------------
def test_schema_from_mcp_entry_wraps_input_schema():
    entry = {
        "module": "tools.mcp.core_server",
        "handler": "handle_x",
        "description": "Do X.",
        "input_schema": {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        },
    }
    schema = schema_from_mcp_entry("get_x", entry)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "get_x"
    assert schema["function"]["description"] == "Do X."
    assert schema["function"]["parameters"] == entry["input_schema"]
    # get_ prefix => read-only guess
    assert schema["is_read_only"] is True


def test_discover_mcp_tools_with_fake_registry(monkeypatch):
    fake = {
        "get_thing": {
            "module": "m",
            "handler": "h",
            "description": "Get a thing.",
            "input_schema": {"type": "object", "properties": {}},
        },
        "mutate_thing": {
            "module": "m",
            "handler": "h2",
            "description": "Mutate a thing.",
            "input_schema": {"type": "object", "properties": {}},
        },
    }
    # shim-aware monkeypatch of the loader
    d = importlib.import_module("tools.agent_runtime.discovery")
    monkeypatch.setattr(d, "load_mcp_registry", lambda: dict(fake))
    specs = d.discover_mcp_tools()
    by_name = {s.name: s for s in specs}
    assert set(by_name) == {"get_thing", "mutate_thing"}
    assert by_name["get_thing"].read_only is True
    assert by_name["mutate_thing"].read_only is False
    assert by_name["get_thing"].module == "m"
    # whitelist filter
    only = d.discover_mcp_tools(names={"get_thing"})
    assert {s.name for s in only} == {"get_thing"}


# ---------------------------------------------------------------------------
# build_registry + availability checks
# ---------------------------------------------------------------------------
def test_build_registry_drops_unavailable(monkeypatch):
    d = importlib.import_module("tools.agent_runtime.discovery")
    monkeypatch.setattr(d, "discover_mcp_tools", lambda names=None: [])
    monkeypatch.setattr(d, "discover_builtin_tools", lambda: [])

    avail = ToolSpec(
        name="avail", schema=schema_from_callable(lambda: None, name="avail"),
        source="decorated", check=lambda: True,
    )
    hidden = ToolSpec(
        name="hidden", schema=schema_from_callable(lambda: None, name="hidden"),
        source="decorated", check=lambda: False,
    )
    reg = d.build_registry(
        include_mcp=False, include_builtin=False, extra_specs=[avail, hidden]
    )
    assert "avail" in reg
    assert "hidden" not in reg


def test_build_registry_later_source_wins(monkeypatch):
    d = importlib.import_module("tools.agent_runtime.discovery")
    mcp_spec = ToolSpec(name="dup", schema={"type": "function", "function": {"name": "dup"}}, source="mcp")
    dec_spec = ToolSpec(name="dup", schema={"type": "function", "function": {"name": "dup"}}, source="decorated")
    monkeypatch.setattr(d, "discover_mcp_tools", lambda names=None: [mcp_spec])
    monkeypatch.setattr(d, "discover_builtin_tools", lambda: [])
    reg = d.build_registry(include_mcp=True, include_builtin=False, extra_specs=[dec_spec])
    assert reg["dup"].source == "decorated"


def test_to_openai_tools_returns_schema_list():
    spec = ToolSpec(name="t", schema={"type": "function", "function": {"name": "t"}})
    assert to_openai_tools({"t": spec}) == [spec.schema]


# ---------------------------------------------------------------------------
# JSON cache round-trip
# ---------------------------------------------------------------------------
def test_cache_round_trip(tmp_path):
    spec = ToolSpec(
        name="cached",
        schema=schema_from_callable(lambda x: None, name="cached"),
        source="mcp",
        read_only=True,
        module="some.mod",
        handler="handle_cached",
        check=lambda: True,  # not serialised
    )
    dest = tmp_path / "reg.json"
    write_cache({"cached": spec}, dest)
    raw = json.loads(dest.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    entries = load_cache(dest)
    assert len(entries) == 1
    e = entries[0]
    assert e["name"] == "cached"
    assert e["module"] == "some.mod"
    assert e["handler"] == "handle_cached"
    assert e["read_only"] is True
    # live callables/probes must NOT be in the cache
    assert "check" not in e
    assert "callable" not in e


def test_load_cache_missing_returns_empty(tmp_path):
    assert load_cache(tmp_path / "nope.json") == []
