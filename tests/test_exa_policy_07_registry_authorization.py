#!/usr/bin/env python3
# CUI // SP-CTI
"""One per-tool authorization declaration, read by every MCP surface (exa-policy-07).

``min_il`` and ``required_roles`` are declared once per tool in
``tools/mcp/tool_registry.py``. Before this task there were two declarations and
they disagreed: ``agent_tool_gate._inherited_limits`` asked the registry for
limits it never declared (so every tool fell through to the IL4 baseline with no
role limit), while ``args/owasp_agentic_config.yaml`` carried a hand-written
``role_tool_matrix`` in which ``developer`` allowed 8 tools out of roughly 700.

These tests pin the properties that make the single declaration trustworthy:

1. every registered tool resolves to a usable declaration;
2. anything the derivation cannot vouch for is RESTRICTIVE, not permissive;
3. the derivation's inputs stay wired (read_only, mutating bundles, category);
4. every hand-written override states why it exists;
5. all three consuming surfaces read the same answer;
6. the retired matrix has not grown back.

No DB, no LLM, no network — air-gap safe.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.mcp import tool_registry as reg  # noqa: E402
from tools.security.mcp_tool_authorizer import MCPToolAuthorizer  # noqa: E402
from tools.studio.executors import agent_tool_gate, mcp_executor  # noqa: E402


@pytest.fixture
def authorizer():
    """The shipped authorizer — registry mode, no injected matrix."""
    return MCPToolAuthorizer()


def _registered_tools() -> dict:
    tools = {}
    for registry in (reg.TOOL_REGISTRY, reg.RESOURCE_REGISTRY):
        for name, entry in registry.items():
            if isinstance(entry, dict) and "input_schema" in entry:
                tools[name] = entry
    return tools


# ---------------------------------------------------------------------------
# 1. Every registered tool carries a usable declaration
# ---------------------------------------------------------------------------
def test_every_registered_tool_resolves_to_a_declaration():
    order = mcp_executor._il_order()
    unresolved = []
    for name in _registered_tools():
        auth = reg.tool_authorization(name)
        if auth["min_il"] not in order or not isinstance(auth["required_roles"], tuple):
            unresolved.append((name, auth["min_il"]))
    assert unresolved == [], unresolved


def test_declared_roles_are_all_in_the_role_vocabulary():
    """A typo'd role denies silently — it matches no caller and no alias."""
    stray = {
        name: [r for r in auth["required_roles"] if r not in reg.ROLES]
        for name, auth in reg.authorization_declarations().items()
        if any(r not in reg.ROLES for r in auth["required_roles"])
    }
    assert stray == {}, stray


def test_no_registered_tool_fell_through_to_the_restrictive_default():
    """Empty in a healthy tree.

    A non-empty list means somebody added a tool with no ``read_only``
    declaration, or a category with no CATEGORY_WRITE_ROLES row. Either denies
    everyone but admin, which is the safe direction but not a deliberate one.
    """
    assert reg.undeclared_authorizations() == []


def test_every_registry_category_is_mapped():
    """A new category must be a build failure, not a silent admin-only lockdown."""
    categories = {
        str(entry.get("category") or "") for entry in _registered_tools().values()
    }
    unmapped = sorted(c for c in categories if c and c not in reg.CATEGORY_WRITE_ROLES)
    assert unmapped == [], unmapped


# ---------------------------------------------------------------------------
# 2. Undeclared is restrictive, not permissive
# ---------------------------------------------------------------------------
def test_an_unregistered_tool_is_restrictive():
    auth = reg.tool_authorization("no_such_tool_exa_policy_07")
    assert auth["tier"] == reg.TIER_UNKNOWN
    assert auth["min_il"] == reg.RESTRICTED_MIN_IL == "IL5"
    assert auth["required_roles"] == reg.RESTRICTED_ROLES == ("admin",)


def test_a_registered_tool_with_no_read_only_declaration_is_restrictive(monkeypatch):
    """The failure mode being designed out: added, classified by nobody, open to all."""
    monkeypatch.setitem(reg.TOOL_REGISTRY, "stub_exa_policy_07_unclassified", {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle",
        "description": "Registered but never classified.",
        "input_schema": {"type": "object", "properties": {}},
    })
    reg.reset_authorization_cache()
    try:
        auth = reg.tool_authorization("stub_exa_policy_07_unclassified")
        assert auth["tier"] == reg.TIER_UNDECLARED
        assert auth["min_il"] == reg.RESTRICTED_MIN_IL
        assert auth["required_roles"] == reg.RESTRICTED_ROLES
    finally:
        reg.reset_authorization_cache()


def test_an_unmapped_category_denies_everyone_but_admin(monkeypatch):
    monkeypatch.setitem(reg.TOOL_REGISTRY, "stub_exa_policy_07_odd_category", {
        "category": "a_category_nobody_mapped",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle",
        "description": "Mutating, in a category with no roles declared.",
        "input_schema": {"type": "object", "properties": {}},
    })
    monkeypatch.setattr(
        reg, "READ_ONLY_DECLARATIONS",
        type(reg.READ_ONLY_DECLARATIONS)(
            {**reg.READ_ONLY_DECLARATIONS, "stub_exa_policy_07_odd_category": False}
        ),
    )
    reg.reset_authorization_cache()
    try:
        auth = reg.tool_authorization("stub_exa_policy_07_odd_category")
        assert auth["required_roles"] == reg.RESTRICTED_ROLES
        # ...and it is reported, so the lockdown does not stay invisible.
        assert "stub_exa_policy_07_odd_category" in reg.undeclared_authorizations()
    finally:
        reg.reset_authorization_cache()


@pytest.mark.parametrize("role", ["viewer", "auditor", "hacker", "", None])
def test_an_unrecognised_role_normalises_to_nothing(role):
    assert reg.normalize_role(role) == ""
    assert reg.tools_for_role(role) == []


@pytest.mark.parametrize("alias,canonical", sorted(reg.ROLE_ALIASES.items()))
def test_role_aliases_resolve(alias, canonical):
    assert reg.normalize_role(alias) == canonical
    assert reg.normalize_role(alias.upper()) == canonical


# ---------------------------------------------------------------------------
# 3. The derivation's inputs stay wired
# ---------------------------------------------------------------------------
def test_a_read_only_tool_carries_no_role_limit():
    auth = reg.tool_authorization("nist_lookup")
    assert auth["tier"] == reg.TIER_READ
    assert auth["required_roles"] == ()
    assert auth["min_il"] == reg.DEFAULT_MIN_IL


def test_a_mutating_tool_inherits_its_categorys_roles():
    auth = reg.tool_authorization("ssp_generate")
    assert auth["tier"] == reg.TIER_WRITE
    assert auth["category"] == "compliance"
    assert auth["required_roles"] == reg.CATEGORY_WRITE_ROLES["compliance"]
    assert "developer" not in auth["required_roles"]


def test_the_mutating_bundle_signal_escalates_a_read_only_tool():
    """``browser_read_state`` is the concrete case the second signal exists for.

    It is declared read-only — it only reads the page — but reading through a
    DRIVEN browser is not a pure read, and the ``browser`` bundle says so.
    """
    assert reg.READ_ONLY_DECLARATIONS["browser_read_state"] is True
    assert "browser_read_state" in reg.mutating_bundle_tools()
    auth = reg.tool_authorization("browser_read_state")
    assert auth["tier"] == reg.TIER_WRITE


def test_the_mutating_bundle_file_still_parses():
    """The bundle read degrades to an empty set rather than denying everything.

    That is the right failure mode at runtime and the wrong one to ship, so the
    shipped file is checked directly here.
    """
    path = BASE_DIR / "args" / "agent_toolsets.yaml"
    bundles = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("bundles")
    assert bundles, "args/agent_toolsets.yaml declares no bundles"
    assert reg.mutating_bundle_tools(), "no mutating bundle tools were resolved"


# ---------------------------------------------------------------------------
# 4. Overrides are justified
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tool", sorted(reg.AUTHZ_OVERRIDES))
def test_every_override_states_why_it_exists(tool):
    """An override with no reason is indistinguishable from a mistake."""
    assert reg.AUTHZ_OVERRIDES[tool].get("why", "").strip(), tool


@pytest.mark.parametrize("tool", sorted(reg.AUTHZ_OVERRIDES))
def test_every_override_declares_a_known_impact_level(tool):
    order = mcp_executor._il_order()
    assert reg.tool_authorization(tool)["min_il"] in order


def test_the_unattended_workflow_tier_keeps_no_role_limit():
    """These three are allowlisted for unattended dispatch in security_gates.yaml.

    A Studio ``mcp`` step resolves a caller with no roles by default, so a role
    floor on them would deny the very scans that allowlist exists to run.
    """
    policy = yaml.safe_load(
        (BASE_DIR / "args" / "security_gates.yaml").read_text(encoding="utf-8")
    )["mcp_workflow_tools"]
    for tool in ("stig_check", "code_analyze", "scan_dependencies"):
        assert tool in policy["allowed"], tool
        assert reg.tool_authorization(tool)["required_roles"] == (), tool


@pytest.mark.parametrize(
    "tool",
    ["terraform_apply", "k8s_deploy", "ansible_run", "rollback", "sandbox_execute",
     "send_command", "self_heal", "install_asset", "proxy_key_issue",
     "credential_broker_request", "kanban_delete_task", "studio_run_start"],
)
def test_privileged_tools_are_admin_only_above_the_baseline(tool):
    auth = reg.tool_authorization(tool)
    assert auth["min_il"] == reg.RESTRICTED_MIN_IL, auth
    assert auth["required_roles"] == reg.RESTRICTED_ROLES, auth


# ---------------------------------------------------------------------------
# 5. All three surfaces read the same declaration
# ---------------------------------------------------------------------------
def test_agent_tool_gate_inherits_real_data_not_the_default():
    """The acceptance criterion: _inherited_limits returns the declaration.

    It used to gate on component ownership, and most registry tools are owned by
    no component, so their declarations were discarded before being read.
    """
    inherited = agent_tool_gate._inherited_limits("terraform_apply")
    assert inherited is not None
    assert inherited["component"] == "", "no component owns this tool"
    assert inherited["min_il"] == "IL5"
    assert inherited["required_roles"] == ("admin",)
    assert "tool_registry" in inherited["source"]


def test_agent_tool_gate_tool_limits_carry_the_declaration_through():
    limits = agent_tool_gate.tool_limits("terraform_apply")
    assert limits["min_il"] == "IL5"
    assert limits["required_roles"] == ("admin",)
    assert "AUTHZ_OVERRIDES" in limits["source"]


def test_the_worktree_toolset_is_still_governed_by_its_own_policy():
    """Worktree tools have no registry entry, so the gate policy still decides.

    ``run_command``'s IL5 comes from ``agent_workflow_tools.tool_limits``, not
    from the registry; inheriting nothing here is correct, not a gap.
    """
    assert agent_tool_gate._inherited_limits("read_file") is None
    limits = agent_tool_gate.tool_limits("run_command")
    assert limits["min_il"] == "IL5"
    assert limits["source"] == "agent_workflow_tools.tool_limits.run_command"


def test_the_mcp_surface_reads_the_same_declaration():
    requirements = mcp_executor.tool_requirements("terraform_apply")
    assert requirements["min_il"] == "IL5"
    assert requirements["required_roles"] == ("admin",)
    assert requirements["tier"] == reg.TIER_DECLARED


@pytest.mark.parametrize(
    "tool", ["terraform_apply", "ssp_generate", "run_tests", "nist_lookup"]
)
def test_the_three_surfaces_agree(tool, authorizer):
    """One declaration means one answer, whichever surface asks."""
    declaration = reg.tool_authorization(tool)
    assert mcp_executor.tool_requirements(tool)["min_il"] == declaration["min_il"]
    assert agent_tool_gate._inherited_limits(tool)["min_il"] == declaration["min_il"]

    for role in reg.ROLES:
        expected = role == reg.ADMIN_ROLE or (
            not declaration["required_roles"] or role in declaration["required_roles"]
        )
        assert authorizer.authorize(role, tool)["allowed"] is expected, (role, tool)


# ---------------------------------------------------------------------------
# 6. The retired matrix has not grown back
# ---------------------------------------------------------------------------
def test_the_hand_written_matrix_is_retired():
    cfg = yaml.safe_load(
        (BASE_DIR / "args" / "owasp_agentic_config.yaml").read_text(encoding="utf-8")
    )
    section = cfg["mcp_authorization"]
    assert "role_tool_matrix" not in section, (
        "role_tool_matrix is back — MCPToolAuthorizer falls into matrix mode and "
        "the registry declarations stop being read"
    )
    assert section["default_policy"] == "deny"
    assert section["source"] == "mcp_registry"


def test_the_shipped_authorizer_is_in_registry_mode(authorizer):
    assert authorizer.source == "mcp_registry"
    assert sorted(authorizer.get_roles()) == sorted(reg.ROLES)


def test_the_authorizer_view_is_generated_not_hand_written(authorizer):
    """``list_allowed_tools`` enumerates the declarations rather than patterns."""
    view = authorizer.list_allowed_tools("developer")
    declarations = reg.authorization_declarations()
    assert view["source"] == "mcp_registry"
    assert set(view["allow"]) | set(view["deny"]) == set(declarations)
    assert "run_tests" in view["allow"]
    assert "terraform_apply" in view["deny"]
    assert "ssp_generate" in view["deny"]


def test_shipped_declarations_validate_as_a_policy(authorizer):
    result = authorizer.validate_config()
    assert result["valid"] is True, result
    assert result["errors"] == []
    assert result["warnings"] == [], result["warnings"]
    assert result["tool_count"] > 400


def test_admin_reaches_every_declared_tool():
    declarations = reg.authorization_declarations()
    assert len(reg.tools_for_role("admin")) == len(declarations)
    assert len(reg.tools_for_role("tenant_admin")) == len(declarations)


def test_an_explicit_matrix_still_works():
    """Matrix mode is retained so a deployment can pin its own policy."""
    auth = MCPToolAuthorizer(config={
        "default_policy": "deny",
        "role_tool_matrix": {"tester": {"allow": ["*"], "deny": ["terraform_apply"]}},
    })
    assert auth.source == "role_tool_matrix"
    assert auth.authorize("tester", "lint")["allowed"] is True
    assert auth.authorize("tester", "terraform_apply")["allowed"] is False


def test_an_empty_matrix_is_still_reported_as_a_misconfiguration():
    """`role_tool_matrix: {}` authorizes nobody; it must not read as healthy."""
    auth = MCPToolAuthorizer(config={"role_tool_matrix": {}})
    assert auth.validate_config()["valid"] is False


def test_mcp_http_keeps_no_role_map_of_its_own():
    text = (BASE_DIR / "tools" / "saas" / "mcp_http.py").read_text(encoding="utf-8")
    assert "SAAS_ROLE_TO_RBAC_ROLE" not in text
    assert "role_tool_matrix" not in text


def test_the_generator_preserves_the_hand_maintained_declarations():
    """``generate_registry.py`` rewrites the whole file — it must carry these over.

    Before exa-policy-07 it wrote only the two registries, so a regeneration
    silently deleted ``READ_ONLY_DECLARATIONS`` and (had they existed) the
    authorization declarations. Both fail silent AND permissive when absent.
    """
    from tools.mcp import generate_registry

    registry_text = (BASE_DIR / "tools" / "mcp" / "tool_registry.py").read_text(
        encoding="utf-8"
    )
    assert registry_text.count(generate_registry.PRESERVE_MARKER) == 1

    tail = generate_registry._preserved_tail(BASE_DIR / "tools" / "mcp" / "tool_registry.py")
    for symbol in ("READ_ONLY_DECLARATIONS", "AUTHZ_OVERRIDES",
                   "CATEGORY_WRITE_ROLES", "ROLE_ALIASES", "def tool_authorization"):
        assert symbol in tail, f"{symbol} is above the marker and would be dropped"

    # The marker must precede every hand-maintained symbol, not merely exist.
    marker_at = registry_text.index(generate_registry.PRESERVE_MARKER)
    assert marker_at < registry_text.index("READ_ONLY_DECLARATIONS = ")
    assert marker_at < registry_text.index("AUTHZ_OVERRIDES = ")


def test_the_generator_warns_rather_than_dropping_silently(tmp_path):
    from tools.mcp import generate_registry

    assert generate_registry._preserved_tail(tmp_path / "absent.py") == ""
    unmarked = tmp_path / "unmarked.py"
    unmarked.write_text("TOOL_REGISTRY = {}\n", encoding="utf-8")
    assert generate_registry._preserved_tail(unmarked) == ""


@pytest.mark.parametrize(
    "relpath",
    [
        "tools/mcp/tool_registry.py",
        "tools/security/mcp_tool_authorizer.py",
        "tools/studio/executors/mcp_executor.py",
        "tools/studio/executors/agent_tool_gate.py",
    ],
)
def test_the_icdev_mirror_carries_the_declarations(relpath):
    """A pip-installed deployment reads icdev/, not tools/.

    ``tools/`` is a backward-compat shim, so a declaration that lives only in
    the checkout copy is a declaration an installed ICDEV never enforces.
    """
    checkout = (BASE_DIR / relpath).read_text(encoding="utf-8")
    mirror = (BASE_DIR / "icdev" / relpath).read_text(encoding="utf-8")
    assert checkout == mirror, f"icdev/{relpath} has drifted from {relpath}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
