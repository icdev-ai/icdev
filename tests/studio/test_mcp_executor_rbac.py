"""IL and RBAC limits on MCP workflow dispatch (dwo-mcp-02-d3).

No DB, no LLM, air-gap safe. The component registry is injected from a fixture
YAML rather than read from ``args/component_registry.yaml``, because the shipped
registry declares no IL5 component — the acceptance case has to be constructed.
Whether the *real* registry parses into these limits is covered separately by
``test_real_registry_limits_are_readable``.
"""

import importlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools.studio.executors import mcp_executor

from .conftest import declare_stubs_read_only, reset_authorization_cache

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tools" / "studio" / "executors" / "mcp_executor.py"

STUB_TOOLS = ("stub_il5", "stub_unowned", "stub_bad_il")


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    """A component registry with one IL5 canvas that requires the 'isso' role."""
    path = tmp_path / "component_registry.yaml"
    path.write_text(
        """
components:
- key: stubc
  kind: canvas
  cli_name: stubc
  display_name: Stub Secure Canvas
  module: tests_mcp_rbac_stub.blueprint
  blueprint_attr: stub_bp
  url_prefix: /stubc
  min_il: IL5
  default_roles:
  - isso
  - compliance_officer
""",
        encoding="utf-8",
    )
    from tools.config.component_registry import ComponentRegistry

    return ComponentRegistry(registry_path=path)


@pytest.fixture
def stub_tools(monkeypatch):
    """Allowlist + register stub tools: one owned by the IL5 canvas, one not."""
    policy = dict(mcp_executor.load_gate_policy())
    policy["allowed"] = list(policy["allowed"]) + list(STUB_TOOLS)
    monkeypatch.setitem(
        mcp_executor._POLICY_CACHE, mcp_executor.GATE_POLICY_KEY, policy
    )

    mod = types.ModuleType("tests_mcp_rbac_stub.handlers")
    calls = []
    mod.handle = lambda args: calls.append(args) or {"ok": True}
    monkeypatch.setitem(sys.modules, "tests_mcp_rbac_stub.handlers", mod)

    from tools.mcp.tool_registry import TOOL_REGISTRY

    monkeypatch.setitem(TOOL_REGISTRY, "stub_il5", {
        "category": "testing",
        "module": "tests_mcp_rbac_stub.handlers",  # inside the IL5 canvas package
        "handler": "handle",
        "description": "Owned by the stub IL5 canvas.",
        "input_schema": {"type": "object", "properties": {}},
    })
    monkeypatch.setitem(TOOL_REGISTRY, "stub_unowned", {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",  # no component owns tools.mcp.*
        "handler": "handle",
        "description": "Owned by no component.",
        "input_schema": {"type": "object", "properties": {}},
    })

    # exa-policy-07: a tool now also carries its OWN registry declaration, and a
    # tool with no `read_only` declaration resolves restrictively (IL5 / admin).
    # These stubs are declared read-only so the tests below keep measuring what
    # they were written to measure -- what COMPONENT OWNERSHIP contributes --
    # rather than the undeclared-tool fallback, which
    # test_exa_policy_07_registry_authorization.py covers directly.
    declare_stubs_read_only(monkeypatch, *STUB_TOOLS)
    yield calls
    reset_authorization_cache()


@pytest.fixture
def use_registry(monkeypatch, registry):
    """Make the fixture registry the one ``run()`` resolves limits from."""
    module = importlib.import_module("tools.config.component_registry")
    monkeypatch.setattr(module, "get_registry", lambda *a, **k: registry)
    return registry


def caller(il="IL4", roles=(), **kw):
    return {"impact_level": il, "roles": tuple(roles), "source": "test", **kw}


# ── Acceptance: IL ─────────────────────────────────────────────────────────

def test_il4_caller_is_blocked_from_an_il5_tool(stub_tools, registry):
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.check_caller_authorized(
            "stub_il5", caller("IL4", ["isso"]), registry=registry
        )
    assert exc.value.reason == "mcp_tool_exceeds_caller_il"
    assert exc.value.tool == "stub_il5"
    message = str(exc.value)
    # Descriptive: names the tool, both levels, and the owning component.
    assert "stub_il5" in message
    assert "IL5" in message and "IL4" in message
    assert "Stub Secure Canvas" in message


def test_il5_caller_clears_the_same_tool(stub_tools, registry):
    requirements = mcp_executor.check_caller_authorized(
        "stub_il5", caller("IL5", ["isso"]), registry=registry
    )
    assert requirements["min_il"] == "IL5"
    assert requirements["component"] == "stubc"


def test_il6_caller_clears_an_il5_tool(stub_tools, registry):
    assert mcp_executor.check_caller_authorized(
        "stub_il5", caller("IL6", ["isso"]), registry=registry
    )["component"] == "stubc"


def test_il2_caller_is_blocked_from_an_unowned_platform_tool(stub_tools, registry):
    """The baseline is live: a platform tool is IL4, so IL2 cannot reach it."""
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.check_caller_authorized(
            "stub_unowned", caller("IL2"), registry=registry
        )
    assert exc.value.reason == "mcp_tool_exceeds_caller_il"
    assert "no component owns it" in str(exc.value)


def test_unrecognized_caller_il_is_refused_not_assumed(stub_tools, registry):
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.check_caller_authorized(
            "stub_unowned", caller("IL9"), registry=registry
        )
    assert exc.value.reason == "mcp_tool_exceeds_caller_il"
    assert "not a known level" in str(exc.value)


def test_unrecognized_component_min_il_is_refused(monkeypatch, stub_tools, tmp_path):
    path = tmp_path / "component_registry.yaml"
    path.write_text(
        "components:\n"
        "- key: stubc\n  kind: canvas\n  display_name: Stub\n"
        "  module: tests_mcp_rbac_stub.blueprint\n  blueprint_attr: b\n"
        "  min_il: IL7\n",
        encoding="utf-8",
    )
    from tools.config.component_registry import ComponentRegistry

    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.check_caller_authorized(
            "stub_il5", caller("IL6"), registry=ComponentRegistry(registry_path=path)
        )
    assert exc.value.reason == "mcp_tool_exceeds_caller_il"
    assert "not a known impact level" in str(exc.value)


# ── Acceptance: roles ──────────────────────────────────────────────────────

def test_role_mismatched_caller_is_blocked(stub_tools, registry):
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.check_caller_authorized(
            "stub_il5", caller("IL5", ["developer"]), registry=registry
        )
    assert exc.value.reason == "mcp_tool_missing_required_role"
    assert exc.value.tool == "stub_il5"
    message = str(exc.value)
    # Descriptive: names what is required, what is held, and the remedy.
    assert "isso" in message and "compliance_officer" in message
    assert "developer" in message
    assert "grant" in message.lower()


def test_caller_with_no_roles_is_blocked_and_told_so(stub_tools, registry):
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.check_caller_authorized(
            "stub_il5", caller("IL5"), registry=registry
        )
    assert exc.value.reason == "mcp_tool_missing_required_role"
    assert "(no roles)" in str(exc.value)


def test_caller_holding_one_required_role_passes(stub_tools, registry):
    assert mcp_executor.check_caller_authorized(
        "stub_il5", caller("IL5", ["developer", "compliance_officer"]), registry=registry
    )["component"] == "stubc"


def test_explicit_canvas_grant_substitutes_for_the_role(monkeypatch, stub_tools, registry):
    """A direct/group grant is a legitimate path in without the default role."""
    monkeypatch.setattr(
        mcp_executor, "_has_canvas_grant", lambda c, name: name == "stubc"
    )
    assert mcp_executor.check_caller_authorized(
        "stub_il5",
        caller("IL5", ["developer"], principal_id="u1", tenant_id="t1"),
        registry=registry,
    )["component"] == "stubc"


def test_grant_lookup_is_skipped_without_an_identity():
    """No principal/tenant means no DB round trip — and no access."""
    assert mcp_executor._has_canvas_grant(caller("IL5"), "stubc") is False


def test_grant_lookup_denies_when_the_store_is_unreachable(monkeypatch):
    import tools.security.canvas_access as canvas_access

    def _boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(canvas_access, "check_access", _boom)
    principal = caller("IL5", principal_id="u1", tenant_id="t1")
    assert mcp_executor._has_canvas_grant(principal, "stubc") is False


def test_a_tool_no_component_owns_carries_no_role_limit(stub_tools, registry):
    requirements = mcp_executor.check_caller_authorized(
        "stub_unowned", caller("IL4"), registry=registry
    )
    assert requirements["required_roles"] == ()
    assert requirements["component"] == ""


# ── Requirement resolution ─────────────────────────────────────────────────

def test_tool_requirements_read_min_il_and_roles_from_the_registry(stub_tools, registry):
    requirements = mcp_executor.tool_requirements("stub_il5", registry=registry)
    assert requirements["min_il"] == "IL5"
    assert requirements["required_roles"] == ("isso", "compliance_officer")
    assert requirements["component"] == "stubc"


def test_unowned_tool_falls_back_to_the_platform_baseline(stub_tools, registry):
    requirements = mcp_executor.tool_requirements("stub_unowned", registry=registry)
    assert requirements["min_il"] == mcp_executor.DEFAULT_TOOL_MIN_IL == "IL4"
    assert requirements["required_roles"] == ()


def test_ownership_is_by_module_package_not_category(stub_tools, registry):
    """category 'testing' must not reach a component named after it."""
    from tools.mcp.tool_registry import TOOL_REGISTRY

    assert TOOL_REGISTRY["stub_unowned"]["category"] == "testing"
    assert mcp_executor._owning_component("tools.mcp.gap_handlers", registry) is None
    assert mcp_executor._owning_component(
        "tests_mcp_rbac_stub.handlers", registry
    ).key == "stubc"


def test_a_sibling_package_is_not_owned(registry):
    assert mcp_executor._owning_component("tests_mcp_rbac_stub_other.x", registry) is None


def test_ambiguous_ownership_resolves_to_the_strictest(tmp_path):
    """Two components in one package: the higher min_il decides."""
    path = tmp_path / "component_registry.yaml"
    path.write_text(
        "components:\n"
        "- key: lax\n  kind: canvas\n  display_name: Lax\n"
        "  module: shared_pkg.blueprint\n  blueprint_attr: b\n  min_il: IL2\n"
        "- key: strict\n  kind: canvas\n  display_name: Strict\n"
        "  module: shared_pkg.other\n  blueprint_attr: b\n  min_il: IL6\n",
        encoding="utf-8",
    )
    from tools.config.component_registry import ComponentRegistry

    owner = mcp_executor._owning_component(
        "shared_pkg.thing", ComponentRegistry(registry_path=path)
    )
    assert owner.key == "strict"


def test_real_registry_limits_are_readable():
    """The shipped registry parses into limits this module can evaluate."""
    from tools.config.component_registry import get_registry

    order = mcp_executor._il_order()
    components = [c for c in get_registry() if c.module]
    assert components, "shipped registry declares no components with a module"
    assert all(c.min_il.upper() in order for c in components)


# ── Caller resolution ──────────────────────────────────────────────────────

def test_caller_defaults_when_nothing_declares_one(monkeypatch):
    for name in mcp_executor.CALLER_IL_ENV + (mcp_executor.CALLER_ROLES_ENV,):
        monkeypatch.delenv(name, raising=False)
    resolved = mcp_executor.resolve_caller()
    assert resolved["impact_level"] == mcp_executor.DEFAULT_CALLER_IL == "IL4"
    assert resolved["roles"] == ()
    assert "default" in resolved["source"]


def test_caller_reads_the_workflow_context_from_run_memory(monkeypatch):
    _fake_run_memory(monkeypatch, {
        "impact_level": "il5",
        "roles": ["isso"],
        "principal_id": "u1",
        "tenant_id": "t1",
    })
    resolved = mcp_executor.resolve_caller("run-1")
    assert resolved["impact_level"] == "IL5"  # normalized
    assert resolved["roles"] == ("isso",)
    assert resolved["principal_id"] == "u1"
    assert mcp_executor.CALLER_KEY in resolved["source"]


def test_explicit_argument_overrides_the_run_context(monkeypatch):
    _fake_run_memory(monkeypatch, {"impact_level": "IL2", "roles": ["isso"]})
    resolved = mcp_executor.resolve_caller(
        "run-1", {"impact_level": "IL6", "roles": "a, b"}
    )
    assert resolved["impact_level"] == "IL6"
    assert resolved["roles"] == ("a", "b")
    assert resolved["source"] == "argument"


def test_environment_is_used_when_the_run_declares_nothing(monkeypatch):
    monkeypatch.setenv("ICDEV_MCP_CALLER_IL", "IL6")
    monkeypatch.setenv("ICDEV_MCP_CALLER_ROLES", "isso , developer")
    resolved = mcp_executor.resolve_caller()
    assert resolved["impact_level"] == "IL6"
    assert resolved["roles"] == ("isso", "developer")
    assert "ICDEV_MCP_CALLER_IL" in resolved["source"]


def test_missing_run_memory_is_not_an_error(monkeypatch):
    """No trigger surface writes the caller key yet — absence must not fail."""
    monkeypatch.setitem(sys.modules, "tools.studio.run_memory", None)
    assert mcp_executor.read_caller_context("run-1") == {}


def test_unreadable_run_memory_falls_back(monkeypatch):
    _fake_run_memory(monkeypatch, RuntimeError("no database"))
    assert mcp_executor.read_caller_context("run-1") == {}


def test_non_dict_caller_context_is_ignored(monkeypatch):
    _fake_run_memory(monkeypatch, "IL6")
    assert mcp_executor.read_caller_context("run-1") == {}


def _fake_run_memory(monkeypatch, value):
    """Install a stub tools.studio.run_memory whose get() returns ``value``."""
    import tools.studio

    def _get(run_id, key, default=None):
        if isinstance(value, Exception):
            raise value
        return value if key == mcp_executor.CALLER_KEY else default

    fake = types.ModuleType("tools.studio.run_memory")
    fake.get = _get
    fake.set = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tools.studio.run_memory", fake)
    monkeypatch.setattr(tools.studio, "run_memory", fake, raising=False)


# ── run() wiring ───────────────────────────────────────────────────────────

def test_run_refuses_before_the_handler_is_called(stub_tools, use_registry):
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run("stub_il5", {}, caller=caller("IL4", ["isso"]))
    assert exc.value.reason == "mcp_tool_exceeds_caller_il"
    assert stub_tools == [], "handler ran despite an IL refusal"


def test_run_refuses_a_role_mismatch_before_dispatch(stub_tools, use_registry):
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run("stub_il5", {}, caller=caller("IL5", ["developer"]))
    assert exc.value.reason == "mcp_tool_missing_required_role"
    assert stub_tools == []


def test_run_dispatches_an_authorized_caller(stub_tools, use_registry):
    payload = mcp_executor.run("stub_il5", {"a": 1}, caller=caller("IL5", ["isso"]))
    assert payload["result"] == {"ok": True}
    assert stub_tools == [{"a": 1}]


def test_payload_records_what_the_dispatch_was_authorized_under(
    stub_tools, use_registry
):
    payload = mcp_executor.run("stub_il5", {}, caller=caller("IL5", ["isso"]))
    assert payload["caller_il"] == "IL5"
    assert payload["required_il"] == "IL5"
    assert payload["component"] == "stubc"


def test_run_resolves_the_caller_when_none_is_passed(stub_tools, use_registry, monkeypatch):
    for name in mcp_executor.CALLER_IL_ENV + (mcp_executor.CALLER_ROLES_ENV,):
        monkeypatch.delenv(name, raising=False)
    # Defaults to IL4, which cannot reach the IL5 tool.
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run("stub_il5", {})
    assert exc.value.reason == "mcp_tool_exceeds_caller_il"


def test_allowlist_is_still_checked_before_il(use_registry):
    """A denied tool is refused for being denied, not for its IL."""
    with pytest.raises(mcp_executor.MCPWorkflowGateError) as exc:
        mcp_executor.run("definitely_not_allowlisted", {}, caller=caller("IL2"))
    assert exc.value.reason == "mcp_tool_not_allowlisted"


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli(*args):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, timeout=180, cwd=str(_ROOT),
    )


def test_cli_low_il_caller_is_refused_with_the_gate_reason():
    proc = _cli("--tool", "health_check", "--params", "{}", "--caller-il", "IL2")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["status"] == "failed"
    assert payload["error_type"] == "mcp_tool_exceeds_caller_il"
    assert "IL2" in payload["error"] and "IL4" in payload["error"]


def test_cli_accepts_the_caller_flags_and_dispatches():
    proc = _cli("--tool", "health_check", "--params", "{}",
                "--caller-il", "IL5", "--caller-roles", "isso",
                "--caller-id", "u1", "--tenant-id", "t1", "--json")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["status"] == "success", proc.stdout + proc.stderr
    assert payload["caller_il"] == "IL5"
