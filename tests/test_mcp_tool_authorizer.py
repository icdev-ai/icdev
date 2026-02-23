#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for MCPToolAuthorizer (Phase 45, Gap 6, D261)."""

from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.security.mcp_tool_authorizer import MCPToolAuthorizer


@pytest.fixture
def default_config():
    """Default MCP authorization config matching owasp_agentic_config.yaml."""
    return {
        "enabled": True,
        "default_policy": "deny",
        "role_tool_matrix": {
            "admin": {
                "allow": ["*"],
            },
            "pm": {
                "allow": [
                    "project_*", "task_*", "agent_status",
                    "ssp_generate", "poam_generate", "search_*",
                ],
                "deny": ["terraform_apply", "k8s_deploy", "rollback"],
            },
            "developer": {
                "allow": [
                    "scaffold", "generate_code", "write_tests",
                    "run_tests", "lint", "format",
                    "search_knowledge", "add_pattern",
                ],
                "deny": [
                    "terraform_apply", "k8s_deploy",
                    "ssp_generate", "stig_check",
                ],
            },
            "isso": {
                "allow": [
                    "ssp_generate", "poam_generate", "stig_check",
                    "sbom_generate", "cui_mark", "control_map",
                    "nist_lookup", "fedramp_*", "cmmc_*",
                    "zta_*", "mosa_*", "search_*",
                ],
                "deny": [
                    "generate_code", "scaffold", "terraform_apply",
                ],
            },
            "co": {
                "allow": [
                    "project_status", "project_list", "agent_status",
                    "ssp_generate", "poam_generate",
                ],
                "deny": [
                    "generate_code", "terraform_apply",
                    "k8s_deploy", "rollback",
                ],
            },
        },
    }


class TestMCPToolAuthorizer:
    """Tests for MCPToolAuthorizer."""

    # --- Admin role ---
    def test_admin_allows_everything(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        result = auth.authorize("admin", "terraform_apply")
        assert result["allowed"] is True

    def test_admin_allows_any_tool(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        for tool in ["scaffold", "ssp_generate", "k8s_deploy", "rollback", "nuclear_launch"]:
            assert auth.authorize("admin", tool)["allowed"] is True

    # --- PM role ---
    def test_pm_allowed_project_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("pm", "project_create")["allowed"] is True
        assert auth.authorize("pm", "project_status")["allowed"] is True
        assert auth.authorize("pm", "task_dispatch")["allowed"] is True

    def test_pm_denied_infra_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("pm", "terraform_apply")["allowed"] is False
        assert auth.authorize("pm", "k8s_deploy")["allowed"] is False
        assert auth.authorize("pm", "rollback")["allowed"] is False

    def test_pm_allowed_compliance_views(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("pm", "ssp_generate")["allowed"] is True
        assert auth.authorize("pm", "poam_generate")["allowed"] is True

    # --- Developer role ---
    def test_developer_allowed_build_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("developer", "scaffold")["allowed"] is True
        assert auth.authorize("developer", "generate_code")["allowed"] is True
        assert auth.authorize("developer", "run_tests")["allowed"] is True
        assert auth.authorize("developer", "lint")["allowed"] is True

    def test_developer_denied_compliance_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("developer", "ssp_generate")["allowed"] is False
        assert auth.authorize("developer", "stig_check")["allowed"] is False

    def test_developer_denied_infra_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("developer", "terraform_apply")["allowed"] is False
        assert auth.authorize("developer", "k8s_deploy")["allowed"] is False

    # --- ISSO role ---
    def test_isso_allowed_compliance_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("isso", "ssp_generate")["allowed"] is True
        assert auth.authorize("isso", "stig_check")["allowed"] is True
        assert auth.authorize("isso", "sbom_generate")["allowed"] is True
        assert auth.authorize("isso", "nist_lookup")["allowed"] is True

    def test_isso_allowed_fedramp_wildcard(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("isso", "fedramp_assess")["allowed"] is True
        assert auth.authorize("isso", "fedramp_report")["allowed"] is True

    def test_isso_denied_dev_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("isso", "generate_code")["allowed"] is False
        assert auth.authorize("isso", "scaffold")["allowed"] is False

    # --- CO role ---
    def test_co_allowed_read_only(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("co", "project_status")["allowed"] is True
        assert auth.authorize("co", "project_list")["allowed"] is True
        assert auth.authorize("co", "agent_status")["allowed"] is True

    def test_co_denied_everything_dangerous(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        assert auth.authorize("co", "generate_code")["allowed"] is False
        assert auth.authorize("co", "terraform_apply")["allowed"] is False
        assert auth.authorize("co", "k8s_deploy")["allowed"] is False

    # --- Default policy ---
    def test_unknown_role_denied(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        result = auth.authorize("hacker", "terraform_apply")
        assert result["allowed"] is False
        assert "Unknown role" in result["reason"]

    def test_unmatched_tool_denied(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        result = auth.authorize("developer", "unknown_tool_xyz")
        assert result["allowed"] is False
        assert "default policy" in result["reason"]

    # --- Deny takes precedence ---
    def test_deny_overrides_wildcard_allow(self):
        """Even with wildcard allow, explicit deny wins."""
        config = {
            "default_policy": "deny",
            "role_tool_matrix": {
                "tester": {
                    "allow": ["*"],
                    "deny": ["terraform_apply"],
                },
            },
        }
        auth = MCPToolAuthorizer(config=config)
        assert auth.authorize("tester", "terraform_apply")["allowed"] is False
        assert auth.authorize("tester", "lint")["allowed"] is True

    # --- list_allowed_tools ---
    def test_list_allowed_tools(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        result = auth.list_allowed_tools("developer")
        assert "scaffold" in result["allow"]
        assert "terraform_apply" in result["deny"]

    def test_list_unknown_role(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        result = auth.list_allowed_tools("unknown")
        assert "error" in result

    # --- get_roles ---
    def test_get_roles(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        roles = auth.get_roles()
        assert "admin" in roles
        assert "pm" in roles
        assert "developer" in roles
        assert "isso" in roles
        assert "co" in roles

    # --- validate_config ---
    def test_validate_config_valid(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        result = auth.validate_config()
        assert result["valid"] is True
        assert result["role_count"] == 5

    def test_validate_config_empty(self):
        auth = MCPToolAuthorizer(config={"role_tool_matrix": {}})
        result = auth.validate_config()
        assert result["valid"] is False

    def test_validate_config_missing_role(self):
        config = {
            "default_policy": "deny",
            "role_tool_matrix": {
                "admin": {"allow": ["*"]},
            },
        }
        auth = MCPToolAuthorizer(config=config)
        result = auth.validate_config()
        assert len(result["warnings"]) > 0

    def test_result_structure(self, default_config):
        auth = MCPToolAuthorizer(config=default_config)
        result = auth.authorize("developer", "lint")
        assert "allowed" in result
        assert "role" in result
        assert "tool" in result
        assert "reason" in result
