# CUI // SP-CTI
"""External MCP tools reaching the agent runtime (hgx-fed-01).

``tools/mcp_client`` was a complete, tested outbound MCP client with no
consumers: discovery named three sources and dispatch handled three branches, so
a server enabled in ``args/external_mcp_servers.yaml`` surfaced nothing. These
tests cover the wiring — and, more importantly, that wiring it in did not move
any of the client's controls out of the client.

The stub server is a real subprocess speaking JSON-RPC over pipes, and it
records every ``tools/call`` it receives to a sidecar file. That log is what
makes "the classification ceiling is checked *before* dialing" a testable claim
rather than an assertion about where a line sits in the source.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from icdev.tools.mcp_client import registry as reg
from tools.agent_runtime import discovery, toolsets
from tools.agent_runtime.dispatch import (
    build_handlers,
    default_classification,
    default_safety_gate,
)

def _ALLOW_ALL(name, tool_input, read_only):  # noqa: N802 — reads as a constant gate
    """Allow everything, so a refusal in these tests came from the external path."""
    return True, ""


@pytest.fixture(autouse=True)
def _clean_registry():
    reg.reset_external_registry()
    yield
    reg.reset_external_registry()


# ---------------------------------------------------------------------------
# A real subprocess MCP server that records what it was actually asked to do
# ---------------------------------------------------------------------------
STUB_SERVER = textwrap.dedent('''
    import json, sys
    from pathlib import Path

    CALL_LOG = Path(sys.argv[1])
    TOOLS = [
        {"name": "search", "description": "Search the corpus.",
         "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
        {"name": "delete_everything", "description": "Not allowlisted.",
         "inputSchema": {"type": "object"}},
    ]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue
        if method == "initialize":
            res = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "stub"}}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            name = msg["params"]["name"]
            with CALL_LOG.open("a", encoding="utf-8", newline="") as fh:
                fh.write(name + "\\n")
            res = {"content": [{"type": "text", "text": "stub ran " + name}]}
        else:
            res = {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\\n")
        sys.stdout.flush()
''')


@pytest.fixture
def stub_server(tmp_path, monkeypatch):
    """Enable one external server backed by a real subprocess.

    Yields the call log path so a test can assert what the server was reached
    for — including that it was not reached at all.
    """
    script = tmp_path / "stub_mcp_server.py"
    script.write_text(STUB_SERVER, encoding="utf-8", newline="")
    call_log = tmp_path / "calls.log"

    spec = {
        "name": "stub",
        "transport": "stdio",
        "command": [sys.executable, str(script), str(call_log)],
        "tools": ["search"],          # delete_everything deliberately absent
        "classification_ceiling": "UNCLASSIFIED",
        "enabled": True,
        "timeout_seconds": 30.0,
    }
    monkeypatch.setattr(reg, "enabled_servers", lambda: [spec])
    # Arguments are declared at the server's ceiling unless a test says otherwise.
    monkeypatch.setenv("ICDEV_CLASSIFICATION", "UNCLASSIFIED")
    return call_log


def _calls(call_log: Path) -> list[str]:
    if not call_log.exists():
        return []
    return [
        line.strip()
        for line in call_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _cheap_registry(**kwargs):
    """Build a registry without paying for the 440-tool MCP derivation."""
    return discovery.build_registry(
        include_mcp=False,
        include_builtin=True,
        decorated_modules=["tools.agent_runtime.mutating_tools"],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Nothing changes for a deployment that has not opted in
# ---------------------------------------------------------------------------
def test_shipped_config_contributes_no_tools():
    """`enabled: false` is the shipped default, so the source yields nothing."""
    assert discovery.discover_external_tools() == []


def test_discovery_is_unchanged_when_no_server_is_enabled():
    """With the shipped config, include_external makes no difference at all."""
    with_ext = _cheap_registry(include_external=True)
    without_ext = _cheap_registry(include_external=False)

    assert sorted(with_ext) == sorted(without_ext)
    assert [with_ext[n].to_cache_entry() for n in sorted(with_ext)] == [
        without_ext[n].to_cache_entry() for n in sorted(without_ext)
    ]
    assert not [n for n in with_ext if n.startswith(reg.NAMESPACE_PREFIX)]


def test_a_broken_client_does_not_break_discovery(monkeypatch):
    """Discovery must survive an external registry that raises."""
    def _boom():
        raise RuntimeError("config on fire")

    monkeypatch.setattr(reg, "enabled_servers", _boom)
    assert discovery.discover_external_tools() == []


# ---------------------------------------------------------------------------
# With a server enabled: tools appear, namespaced
# ---------------------------------------------------------------------------
def test_enabled_server_tools_appear_namespaced(stub_server):
    registry = _cheap_registry()

    spec = registry["ext__stub__search"]
    assert spec.source == "external"
    assert spec.schema["function"]["name"] == "ext__stub__search"
    assert spec.schema["function"]["parameters"]["properties"]["q"]["type"] == "string"


def test_description_reaching_the_prompt_is_the_sanitised_one(stub_server):
    """The schema carries the client's framed text, never the raw remote text."""
    registry = _cheap_registry()

    description = registry["ext__stub__search"].schema["function"]["description"]
    assert description.startswith("[untrusted description from external MCP server 'stub']")
    assert "Search the corpus." in description


def test_non_allowlisted_tool_is_never_discovered(stub_server):
    """The stub advertises delete_everything; the allowlist omits it."""
    registry = _cheap_registry()

    assert not [n for n in registry if "delete_everything" in n]


def test_external_tools_are_never_marked_read_only(stub_server):
    """ICDEV cannot know whether somebody else's tool mutates state."""
    registry = _cheap_registry()

    spec = registry["ext__stub__search"]
    assert spec.read_only is False
    assert spec.schema["function"]["is_read_only"] is False


# ---------------------------------------------------------------------------
# ...and are callable through the normal dispatch path
# ---------------------------------------------------------------------------
def test_external_tool_is_callable_through_dispatch(stub_server):
    handlers = build_handlers(_cheap_registry(), safety_gate=_ALLOW_ALL)

    result = handlers["ext__stub__search"]({"q": "widgets"}, None)

    assert "stub ran search" in result
    assert _calls(stub_server) == ["search"]


def test_external_tool_goes_through_the_safety_gate(stub_server):
    """Not read-only means the default gate refuses it, like any mutating tool."""
    handlers = build_handlers(_cheap_registry())  # fail-closed default gate

    result = handlers["ext__stub__search"]({"q": "widgets"}, None)

    assert result.startswith("blocked:")
    assert _calls(stub_server) == []


def test_gate_decisions_are_recorded_for_external_tools(stub_server):
    """The gate sees the namespaced name, so a policy can single a server out."""
    seen: list[tuple[str, bool]] = []

    def _recording_gate(name, tool_input, read_only):
        seen.append((name, read_only))
        return True, ""

    handlers = build_handlers(_cheap_registry(), safety_gate=_recording_gate)
    handlers["ext__stub__search"]({"q": "x"}, None)

    assert seen == [("ext__stub__search", False)]


def test_a_tool_outside_the_allowlist_cannot_be_dispatched(stub_server):
    """Even naming it directly: the registry refuses what it never exposed."""
    from tools.agent_runtime.discovery import ToolSpec
    from tools.agent_runtime.dispatch import make_handler

    forged = ToolSpec(
        name="ext__stub__delete_everything",
        schema={"type": "function", "function": {"name": "ext__stub__delete_everything"}},
        source="external",
    )
    handler = make_handler(forged, gate=_ALLOW_ALL)

    result = handler({}, None)

    assert "unknown external tool" in result
    assert _calls(stub_server) == []


def test_remote_failure_arrives_as_a_tool_result_not_an_exception(stub_server, monkeypatch):
    """A remote that hangs, crashes or answers garbage must not crash the loop.

    Every failure mode in the transport converges on ``call_tool`` returning
    ``None``, so that is what is simulated here rather than one of the several
    ways to produce it.
    """
    handlers = build_handlers(_cheap_registry(), safety_gate=_ALLOW_ALL)
    transport = reg.get_external_registry()._transports["stub"]
    monkeypatch.setattr(transport, "call_tool", lambda tool, arguments: None)

    result = handlers["ext__stub__search"]({"q": "x"}, None)

    assert isinstance(result, str)
    assert result.startswith("error:")


# ---------------------------------------------------------------------------
# The controls the client already had, still enforced from the runtime
# ---------------------------------------------------------------------------
def test_classification_ceiling_is_checked_before_dialing(stub_server, monkeypatch):
    """A CUI deployment gets no connection to an UNCLASSIFIED server."""
    monkeypatch.setenv("ICDEV_CLASSIFICATION", "CUI")
    handlers = build_handlers(_cheap_registry(), safety_gate=_ALLOW_ALL)

    result = handlers["ext__stub__search"]({"q": "secret"}, None)

    assert "exceeds the ceiling" in result
    assert _calls(stub_server) == [], "the server was reached despite the ceiling"


def test_declared_classification_defaults_to_the_deployment(monkeypatch):
    """Not UNCLASSIFIED — declaring that by default would void the ceiling."""
    monkeypatch.delenv("ICDEV_CLASSIFICATION", raising=False)
    assert default_classification() == "CUI"

    monkeypatch.setenv("ICDEV_CLASSIFICATION", "unclassified")
    assert default_classification() == "UNCLASSIFIED"


def test_explicit_classification_overrides_the_deployment_default(stub_server, monkeypatch):
    monkeypatch.setenv("ICDEV_CLASSIFICATION", "CUI")
    handlers = build_handlers(
        _cheap_registry(), safety_gate=_ALLOW_ALL, classification="UNCLASSIFIED"
    )

    result = handlers["ext__stub__search"]({"q": "x"}, None)

    assert "stub ran search" in result


def test_air_gap_yields_zero_external_tools(monkeypatch):
    """An external MCP server is off-box by definition.

    Deliberately does not use the ``stub_server`` fixture: that fixture patches
    ``enabled_servers``, which is the function the interlock lives in. Here the
    config is enabled through ``load_config`` so the real ``enabled_servers``
    runs and the real interlock is the thing under test.
    """
    from icdev.tools.mcp_client import client as transport_mod

    monkeypatch.setattr(reg, "load_config", lambda: {
        "enabled": True,
        "defaults": {},
        "servers": [{"name": "stub", "transport": "stdio",
                     "command": [sys.executable, "-c", "pass"],
                     "tools": ["search"], "enabled": True}],
    })
    assert reg.enabled_servers(), "precondition: the server is enabled off air-gap"

    monkeypatch.setattr(transport_mod, "_airgap_blocks", lambda: True)

    assert reg.enabled_servers() == []
    assert discovery.discover_external_tools() == []
    assert not [n for n in _cheap_registry() if n.startswith(reg.NAMESPACE_PREFIX)]


# ---------------------------------------------------------------------------
# A remote server must not be able to reach a first-party tool
# ---------------------------------------------------------------------------
def test_an_external_tool_never_shadows_a_first_party_one(monkeypatch):
    """Namespacing makes this unreachable; it is enforced anyway."""
    from tools.agent_runtime.discovery import ToolSpec

    monkeypatch.setattr(
        discovery,
        "discover_external_tools",
        lambda names=None: [
            ToolSpec(name="write_file", schema={"type": "function"}, source="external")
        ],
    )
    registry = _cheap_registry()

    assert registry["write_file"].source == "decorated"


def test_an_unnamespaced_remote_tool_is_dropped(stub_server, monkeypatch):
    monkeypatch.setattr(
        reg.ExternalToolRegistry,
        "list_tools",
        lambda self: [{"name": "run_command", "description": "hi", "input_schema": {}}],
    )
    assert discovery.discover_external_tools() == []


def test_an_over_long_name_is_dropped_rather_than_offered(stub_server, monkeypatch):
    """One 65-char name would make the provider reject the entire request."""
    long_name = "ext__stub__" + ("t" * 60)
    monkeypatch.setattr(
        reg.ExternalToolRegistry,
        "list_tools",
        lambda self: [{"name": long_name, "description": "hi", "input_schema": {}}],
    )
    assert len(long_name) > 64
    assert discovery.discover_external_tools() == []


def test_a_payload_only_description_becomes_first_party_text():
    """Sanitising to nothing must not leave the model an empty description."""
    schema = discovery.schema_from_external_tool(
        {"name": "ext__evil__x", "remote_name": "x", "server": "evil", "description": ""}
    )

    assert "external MCP server 'evil'" in schema["function"]["description"]


# ---------------------------------------------------------------------------
# Bundles — the second allowlist
# ---------------------------------------------------------------------------
def test_a_bundle_can_hand_an_external_tool_to_a_role(stub_server, tmp_path, monkeypatch):
    bundle_file = tmp_path / "toolsets.yaml"
    bundle_file.write_text(
        "version: 1\nbundles:\n  ticketing:\n    tools:\n"
        "      - ext__stub__search\n    mutating: true\n",
        encoding="utf-8",
        newline="",
    )
    monkeypatch.setenv("ICDEV_AGENT_TOOLSETS", str(bundle_file))

    tools, handlers = toolsets.build_toolset(["ticketing"], safety_gate=_ALLOW_ALL)

    assert [t["function"]["name"] for t in tools] == ["ext__stub__search"]
    assert "stub ran search" in handlers["ext__stub__search"]({"q": "x"}, None)


def test_an_enabled_server_is_not_automatically_in_every_bundle(stub_server, tmp_path, monkeypatch):
    """Enabling a server grants reachability, not distribution."""
    bundle_file = tmp_path / "toolsets.yaml"
    bundle_file.write_text(
        "version: 1\nbundles:\n  files:\n    tools:\n      - read_file\n",
        encoding="utf-8",
        newline="",
    )
    monkeypatch.setenv("ICDEV_AGENT_TOOLSETS", str(bundle_file))

    registry = toolsets.build_registry_for_bundles(["files"])

    assert not [n for n in registry if n.startswith(reg.NAMESPACE_PREFIX)]


def test_shipped_bundles_name_no_external_tool():
    """No live ext__ entry ships: args/external_mcp_servers.yaml has no servers."""
    for bundle in toolsets.list_bundles():
        assert not [t for t in bundle["tools"] if t.startswith(reg.NAMESPACE_PREFIX)], (
            f"bundle {bundle['name']!r} names an external tool that cannot exist"
        )


def test_default_gate_still_fails_closed_for_mutating_tools():
    """Guard against the external branch loosening the shared default."""
    allowed, reason = default_safety_gate("ext__stub__search", {}, False)
    assert allowed is False
    assert reason
