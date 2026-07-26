# CUI // SP-CTI
"""The browser primitive must be live at all four agent-tool seams (oss-browse-03).

There are four parallel agent-tool registries in this repo. Registering in the
wrong one strands the capability *silently* — nothing errors, the tool simply is
not there for the agent that needed it:

1. ``tools/agent_toolkit/__init__.py``      — in-process primitives
2. ``tools/mcp/tool_registry.py`` + ``gap_handlers.py`` — the MCP gateway
   (from which ``tools/agent_runtime/discovery.py`` derives the SAG ToolSpec)
3. ``args/agent_toolsets.yaml``             — the standalone agent's bundles
4. ``tools/ace/agent_tools.py``             — ACE co-workers

These tests pin all four to one vocabulary
(:data:`tools.browser.session.BROWSER_TOOL_NAMES`) and re-assert the oss-fix-01
lesson at the MCP seam: a ``TOOL_REGISTRY`` entry whose handler does not exist is
replaced by an error stub in ``unified_server.py``, so the tool is advertised,
answers plausibly, and never works.

Nothing here launches a browser. Every assertion is about *registration* —
resolution, schema shape, gating — so the suite runs on a host with no driver.
"""
from __future__ import annotations

import importlib

import pytest
import yaml

from tools.browser.session import BROWSER_TOOL_NAMES
from tools.mcp.tool_registry import TOOL_REGISTRY

BROWSER_TOOLS = list(BROWSER_TOOL_NAMES)


def _resolve(entry: dict):
    module = importlib.import_module(entry["module"])
    return getattr(module, entry["handler"])


def test_vocabulary_is_non_trivial():
    """Guard against every sweep below passing vacuously on an empty list."""
    assert len(BROWSER_TOOLS) == 8
    assert all(n.startswith("browser_") for n in BROWSER_TOOLS)


# ---------------------------------------------------------------------------
# Seam 1 — tools/agent_toolkit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_toolkit_exports_the_primitive(name):
    """Each browser op is importable from the toolkit, alongside read_file."""
    import tools.agent_toolkit as toolkit

    assert name in toolkit.__all__, f"{name} missing from agent_toolkit.__all__"
    assert callable(getattr(toolkit, name)), f"{name} is not callable"


def test_toolkit_still_exports_the_existing_primitives():
    """The browser export must not have displaced the established surface."""
    import tools.agent_toolkit as toolkit

    for name in ("read_file", "execute_shell", "write_todos", "create_agent"):
        assert callable(getattr(toolkit, name))


def test_toolkit_does_not_reimplement_the_browser():
    """The toolkit seam re-exports the session ops — it never builds a driver.

    A second implementation behind one seam is how the guard rails
    (allowlist / budget / audit) get bypassed. Assert identity, not behaviour.
    """
    import tools.agent_toolkit as toolkit
    from tools.browser import session

    for name in BROWSER_TOOLS:
        assert getattr(toolkit, name) is getattr(session, name), (
            f"{name} in the toolkit is not the shared tools.browser.session op"
        )


# ---------------------------------------------------------------------------
# Seam 2 — MCP registry + gap handlers (the oss-fix-01 lesson)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_browser_tool_is_registered_in_mcp(name):
    entry = TOOL_REGISTRY.get(name)
    assert entry is not None, f"{name} is not in TOOL_REGISTRY"
    assert entry.get("module") and entry.get("handler")
    assert entry.get("category") == "browser"
    assert entry.get("description")
    assert entry.get("input_schema", {}).get("type") == "object"


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_browser_handler_actually_resolves(name):
    """oss-fix-01: a registry entry naming a handler that does not exist is
    silently swapped for an error stub. Resolve it for real."""
    handler = _resolve(TOOL_REGISTRY[name])
    assert callable(handler)
    assert handler.__name__ == f"handle_{name}"


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_browser_handler_is_not_the_server_stub(name):
    """Exercise the production path, not just importlib.

    ``UnifiedMCPServer._resolve_handler`` is what actually runs; assert it never
    falls back to its ``_stub`` closure for a browser tool.
    """
    from tools.mcp.unified_server import UnifiedMCPServer

    server = UnifiedMCPServer()
    handler = server._resolve_handler(name, TOOL_REGISTRY[name])
    assert getattr(handler, "__name__", "") != "_stub", (
        f"{name} resolves to the 'Module not available' stub"
    )


@pytest.mark.parametrize(
    "name, args, missing",
    [
        ("browser_navigate", {}, "url"),
        ("browser_click", {}, "index"),
        ("browser_type", {"index": 1}, "text"),
        ("browser_type", {"text": "x"}, "index"),
        ("browser_select", {"index": 1}, "value"),
        ("browser_press", {}, "key"),
    ],
)
def test_browser_handlers_validate_args_before_opening_a_browser(name, args, missing):
    """Argument validation happens before any driver is created.

    This is also what keeps this suite driver-free: a handler that launched a
    browser to discover a missing argument would fail here on a bare host.
    """
    handler = _resolve(TOOL_REGISTRY[name])
    result = handler({**args, "mcp_role": "admin"})
    assert result["status"] == "failed"
    assert missing in result["error"]


def test_browser_close_is_safe_with_no_session_open():
    """The one browser handler that must work without a driver present."""
    from tools.mcp.gap_handlers import handle_browser_close

    result = handle_browser_close({"session": "test-never-opened", "mcp_role": "admin"})
    assert result.get("closed") is False
    assert result.get("session") == "test-never-opened"


# ---------------------------------------------------------------------------
# Seam 2c — the RBAC policy is actually consulted at the MCP call path
#
# `_mcp_authz_gate` is opt-in per handler in gap_handlers.py. Declaring
# `browser_*` denies in args/owasp_agentic_config.yaml without calling it would
# leave the policy inert while reading as enforced — the tool would be reachable
# by any role. These tests pin the call, not just the policy.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _no_authz_bypass(monkeypatch):
    monkeypatch.delenv("ICDEV_MCP_AUTHZ_BYPASS", raising=False)


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_mcp_handler_denies_when_no_role_is_supplied(name, _no_authz_bypass):
    """Deny-by-default: no `mcp_role` means no browser, whatever the args say."""
    result = _resolve(TOOL_REGISTRY[name])({})
    assert result.get("authorized") is False
    assert "authorization required" in result["error"]


@pytest.mark.parametrize("name", BROWSER_TOOLS)
@pytest.mark.parametrize("role", ["pm", "developer", "isso", "co"])
def test_mcp_handler_denies_non_admin_roles(role, name, _no_authz_bypass):
    """The deny in owasp_agentic_config.yaml reaches the actual call path."""
    result = _resolve(TOOL_REGISTRY[name])({"mcp_role": role})
    assert result.get("authorized") is False
    assert "unauthorized" in result["error"]


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_mcp_handler_authorization_precedes_argument_validation(name, _no_authz_bypass):
    """An unauthorized caller must not learn the tool's argument shape.

    Every browser tool refuses an empty-args call from a denied role with an
    authorization error — never with a 'requires X' hint.
    """
    result = _resolve(TOOL_REGISTRY[name])({"mcp_role": "pm"})
    assert result.get("authorized") is False
    assert "requires" not in result["error"]


def test_mcp_handler_lets_an_authorized_role_through_to_the_argument_check():
    """The gate must not be a blanket ban — admin reaches the handler body."""
    from tools.mcp.gap_handlers import handle_browser_navigate

    result = handle_browser_navigate({"mcp_role": "admin"})
    assert result.get("authorized") is not False
    assert "url" in result["error"]


# ---------------------------------------------------------------------------
# Seam 2b — SAG discovery derives the ToolSpec from the MCP registration
# ---------------------------------------------------------------------------


def test_discovery_derives_specs_from_the_mcp_registration():
    from tools.agent_runtime import discovery

    specs = {s.name: s for s in discovery.discover_mcp_tools(set(BROWSER_TOOLS))}
    assert set(specs) == set(BROWSER_TOOLS)
    for name, spec in specs.items():
        assert spec.source == "mcp"
        assert spec.schema["function"]["name"] == name
        assert spec.module and spec.handler


def test_discovery_treats_every_browser_tool_as_mutating():
    """read_only would skip the safety gate — no browser tool may be read-only.

    ``browser_read_state`` does not change the page, but it is how untrusted
    remote content enters the model's context, so the whole surface is gated.
    """
    from tools.agent_runtime import discovery

    for spec in discovery.discover_mcp_tools(set(BROWSER_TOOLS)):
        assert spec.read_only is False, f"{spec.name} would bypass the safety gate"


# ---------------------------------------------------------------------------
# Seam 3 — args/agent_toolsets.yaml
# ---------------------------------------------------------------------------


def _bundles() -> dict:
    from tools.agent_runtime.toolsets import _BUNDLE_PATH

    data = yaml.safe_load(_BUNDLE_PATH.read_text(encoding="utf-8")) or {}
    return data.get("bundles") or {}


def test_browser_bundle_exists_and_is_marked_mutating():
    bundle = _bundles().get("browser")
    assert bundle is not None, "no 'browser' bundle in args/agent_toolsets.yaml"
    assert bundle.get("mutating") is True, "the browser bundle must be mutating"
    assert (bundle.get("description") or "").strip()
    assert sorted(bundle.get("tools") or []) == sorted(BROWSER_TOOLS)


def test_browser_bundle_resolves_through_the_toolset_loader():
    from tools.agent_runtime.toolsets import resolve_bundles

    assert resolve_bundles(["browser"]) == set(BROWSER_TOOLS)


def test_browser_bundle_advertises_only_working_tools():
    """A bundle must not hand a model a tool that resolves to the import stub."""
    broken = []
    for tool in _bundles()["browser"]["tools"]:
        entry = TOOL_REGISTRY.get(tool)
        assert entry is not None, f"bundle advertises unregistered tool {tool}"
        try:
            _resolve(entry)
        except (ImportError, AttributeError, ModuleNotFoundError) as exc:
            broken.append(f"{tool}: {exc}")
    assert not broken, f"browser bundle advertises non-functional tools: {broken}"


# ---------------------------------------------------------------------------
# Seam 4 — tools/ace/agent_tools.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_ace_registry_has_a_schema_and_a_handler(name):
    from tools.ace import agent_tools as ace_tools

    assert name in ace_tools._SCHEMAS, f"{name} missing from ACE _SCHEMAS"

    registry = ace_tools.AgentToolRegistry(spec=object(), instance_id="test-instance")
    handler = registry._make_handler(name)
    assert callable(handler), f"ACE _make_handler returned nothing for {name}"


def test_ace_builds_the_browser_tools_when_asked():
    from tools.ace.agent_tools import AgentToolRegistry

    registry = AgentToolRegistry(spec=object(), instance_id="test-instance")
    tools, handlers = registry.build(BROWSER_TOOLS)
    assert sorted(handlers) == sorted(BROWSER_TOOLS)
    assert [t["function"]["name"] for t in tools] == BROWSER_TOOLS


def test_ace_browser_tools_are_opt_in():
    """The default ACE tool set must not include the browser."""
    from tools.ace.agent_tools import AgentToolRegistry

    registry = AgentToolRegistry(spec=object(), instance_id="test-instance")
    _tools, handlers = registry.build([])
    assert not set(handlers) & set(BROWSER_TOOLS)


def test_ace_sessions_are_per_instance():
    """Two concurrent co-workers must not share element indices."""
    from tools.ace.agent_tools import AgentToolRegistry

    a = AgentToolRegistry(spec=object(), instance_id="inst-a")
    b = AgentToolRegistry(spec=object(), instance_id="inst-b")
    a._make_handler("browser_click")
    b._make_handler("browser_click")
    assert a._browser_registry._session != b._browser_registry._session


# ---------------------------------------------------------------------------
# Enforcement — safety gate + deny-first RBAC
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_safety_gate_denies_browser_tools_by_default(name, monkeypatch):
    from tools.agent_runtime.dispatch import default_safety_gate

    monkeypatch.delenv("ICDEV_SAG_ALLOW_MUTATION", raising=False)
    allowed, reason = default_safety_gate(name, {}, False)
    assert allowed is False
    assert reason


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_safety_gate_allows_browser_tools_on_explicit_opt_in(name, monkeypatch):
    from tools.agent_runtime.dispatch import default_safety_gate

    monkeypatch.setenv("ICDEV_SAG_ALLOW_MUTATION", "1")
    allowed, _reason = default_safety_gate(name, {}, False)
    assert allowed is True


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_dispatch_blocks_a_gated_browser_tool_before_invoking_it(name, monkeypatch):
    """The gate runs before dispatch resolves the handler — no driver is touched."""
    from tools.agent_runtime import discovery
    from tools.agent_runtime.dispatch import default_safety_gate, make_handler

    monkeypatch.delenv("ICDEV_SAG_ALLOW_MUTATION", raising=False)
    spec = {s.name: s for s in discovery.discover_mcp_tools({name})}[name]
    handler = make_handler(spec, gate=default_safety_gate)
    assert handler({}, None).startswith("blocked:")


@pytest.mark.parametrize("name", BROWSER_TOOLS)
@pytest.mark.parametrize("role", ["pm", "developer", "isso", "co"])
def test_rbac_denies_browser_tools_to_non_admin_roles(role, name):
    from tools.security.mcp_tool_authorizer import MCPToolAuthorizer

    result = MCPToolAuthorizer().authorize(role, name)
    assert result["allowed"] is False, f"{role} may call {name}"
    assert "deny" in result["reason"].lower()


@pytest.mark.parametrize("name", BROWSER_TOOLS)
def test_rbac_allows_browser_tools_to_admin(name):
    """The deny must be role-scoped, not a blanket ban that makes the tool dead."""
    from tools.security.mcp_tool_authorizer import MCPToolAuthorizer

    assert MCPToolAuthorizer().authorize("admin", name)["allowed"] is True


def test_rbac_deny_is_explicit_not_merely_the_default_policy():
    """A future wildcard broadening must not silently grant the browser.

    ``default_policy: deny`` alone would let ``allow: ["browser_*"]`` — or a
    broader ``allow: ["b*"]`` — turn the browser on for a role by accident.
    """
    from tools.security.mcp_tool_authorizer import MCPToolAuthorizer

    authorizer = MCPToolAuthorizer()
    for role in ("pm", "developer", "isso", "co"):
        deny = authorizer.list_allowed_tools(role)["deny"]
        assert "browser_*" in deny, f"role {role!r} has no explicit browser_* deny"


# ---------------------------------------------------------------------------
# Cross-seam parity — the point of the whole task
# ---------------------------------------------------------------------------


def test_all_four_seams_expose_the_same_tool_names():
    """One vocabulary. A rename that misses a seam fails here, not in production."""
    import tools.agent_toolkit as toolkit
    from tools.ace import agent_tools as ace_tools

    seams = {
        "agent_toolkit": {n for n in BROWSER_TOOLS if n in toolkit.__all__},
        "mcp_registry": {n for n in BROWSER_TOOLS if n in TOOL_REGISTRY},
        "toolset_bundle": set(_bundles().get("browser", {}).get("tools") or []),
        "ace_registry": {n for n in BROWSER_TOOLS if n in ace_tools._SCHEMAS},
    }
    expected = set(BROWSER_TOOLS)
    stranded = {seam: sorted(expected - names) for seam, names in seams.items() if expected - names}
    assert not stranded, f"browser capability stranded at seams: {stranded}"


def test_agent_loop_registry_covers_the_same_vocabulary():
    """The oss-browse-01 agent-loop registry is the fifth consumer of the same names."""
    from tools.browser.agent_tools import DEFAULT_TOOLS, browser_schemas

    assert set(browser_schemas()) == set(BROWSER_TOOLS)
    assert set(DEFAULT_TOOLS) == set(BROWSER_TOOLS)
