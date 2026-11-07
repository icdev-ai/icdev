"""owui-mcp-04: the SaaS roles viewer and auditor are READ-ONLY roles, not unknown ones.

Measured on the governed Open WebUI demo (2026-11-07): tools.saas.mcp_http.authorize_tool
denied viewer and auditor EVERY tool -- "Unknown role ... default policy: deny" -- because
tools.mcp.tool_registry.ROLES had no such roles. Harmless while one shared developer key
served the demo; under per-user identity it locks two of the five SaaS roles out of the
read-only tools entirely. Operator decision: both become canonical roles with no write grant.
"""
import pytest

from icdev.tools.mcp import tool_registry
from icdev.tools.saas import mcp_http

SAAS_ROLES = ("tenant_admin", "developer", "compliance_officer", "auditor", "viewer")
CHAT_READONLY = ("project_list", "project_status", "nist_lookup")
READ_ROLES = ("viewer", "auditor")


@pytest.mark.parametrize("role", SAAS_ROLES)
@pytest.mark.parametrize("tool", CHAT_READONLY)
def test_every_saas_role_reaches_the_chat_tools(role, tool):
    assert mcp_http.authorize_tool(tool, role)["allowed"]


@pytest.mark.parametrize("role", READ_ROLES)
@pytest.mark.parametrize("tool", ("project_create", "scaffold"))
def test_read_roles_get_no_write_tool(role, tool):
    assert not mcp_http.authorize_tool(tool, role)["allowed"]


@pytest.mark.parametrize("role", READ_ROLES)
def test_read_roles_reach_exactly_the_unrestricted_tools(role):
    """No write grant at all: the reach is the set of tools that declare no role limit."""
    unrestricted = {
        name for name, decl in tool_registry.authorization_declarations().items()
        if not decl.get("required_roles")
    }
    assert unrestricted
    assert set(tool_registry.tools_for_role(role)) == unrestricted


@pytest.mark.parametrize("role", READ_ROLES)
def test_read_roles_are_not_privileged(role):
    assert tool_registry.normalize_role(role) == role
    assert role != tool_registry.ADMIN_ROLE
