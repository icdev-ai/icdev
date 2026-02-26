#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the Unified MCP Gateway Server (D301).

Validates:
    1. Registry completeness — all 26 categories, no duplicates, valid schemas
    2. Lazy loading — no handler imports at startup, caching works
    3. Handler passthrough — representative tool per category
    4. New tool coverage — one test per gap handler category
    5. Server lifecycle — create, list tools, error handling
"""

import sys
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.mcp.tool_registry import RESOURCE_REGISTRY, TOOL_REGISTRY


# ── Registry Completeness ────────────────────────────────────────

class TestRegistryCompleteness:
    """Verify the tool registry has all expected entries."""

    def test_total_tool_count(self):
        """Registry must have at least 230 tools (grows with new phases)."""
        assert len(TOOL_REGISTRY) >= 230

    def test_total_resource_count(self):
        """Registry must have 6 resources."""
        assert len(RESOURCE_REGISTRY) == 6

    def test_no_duplicate_tool_names(self):
        """Tool names must be unique (dict keys guarantee this, but verify count)."""
        assert len(TOOL_REGISTRY) == len(set(TOOL_REGISTRY.keys()))

    def test_all_categories_present(self):
        """All expected categories must be represented."""
        expected = {
            "core", "compliance", "builder", "infra", "knowledge",
            "maintenance", "mbse", "modernization", "requirements",
            "supply_chain", "simulation", "integration", "marketplace",
            "devsecops", "gateway", "context", "innovation", "observability",
            "translation", "dx", "cloud", "registry", "security_agentic",
            "testing", "installer", "misc", "security",
        }
        actual = {entry["category"] for entry in TOOL_REGISTRY.values()}
        assert expected.issubset(actual), f"Missing: {expected - actual}"

    def test_category_counts(self):
        """Spot-check category minimum sizes (grow as new phases add tools)."""
        cats = Counter(e["category"] for e in TOOL_REGISTRY.values())
        assert cats["core"] >= 5
        assert cats["compliance"] >= 36
        assert cats["builder"] >= 13
        assert cats["gateway"] >= 5
        assert cats["context"] >= 5
        assert cats["innovation"] >= 10
        assert cats["observability"] >= 6

    def test_all_tools_have_required_keys(self):
        """Every tool entry must have category, module, handler, description, input_schema."""
        required = {"category", "module", "handler", "description", "input_schema"}
        for name, entry in TOOL_REGISTRY.items():
            missing = required - set(entry.keys())
            assert not missing, f"Tool '{name}' missing keys: {missing}"

    def test_all_schemas_are_objects(self):
        """All input_schema entries must be JSON Schema objects."""
        for name, entry in TOOL_REGISTRY.items():
            schema = entry["input_schema"]
            assert schema.get("type") == "object", f"Tool '{name}' schema type is not 'object'"
            assert "properties" in schema, f"Tool '{name}' schema missing 'properties'"

    def test_all_resources_have_required_keys(self):
        """Every resource entry must have name, description, module, handler."""
        required = {"name", "description", "module", "handler"}
        for uri, entry in RESOURCE_REGISTRY.items():
            missing = required - set(entry.keys())
            assert not missing, f"Resource '{uri}' missing keys: {missing}"


# ── Existing Server Parity ───────────────────────────────────────

class TestExistingServerParity:
    """Verify all tools from existing 18 servers are in the registry."""

    def test_core_tools_present(self):
        """Core server's 5 tools must be in registry."""
        expected = {"project_create", "project_list", "project_status",
                    "task_dispatch", "agent_status"}
        core_tools = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "core"}
        assert expected == core_tools

    def test_compliance_tools_present(self):
        """Compliance server's tools must be in registry (grows with new frameworks)."""
        compliance_tools = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "compliance"}
        assert len(compliance_tools) >= 36
        assert "ssp_generate" in compliance_tools
        assert "fips199_categorize" in compliance_tools

    def test_gateway_tools_present(self):
        """Gateway server's 5 tools must be in registry."""
        expected = {"bind_user", "list_bindings", "revoke_binding",
                    "send_command", "gateway_status"}
        gw_tools = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "gateway"}
        assert expected == gw_tools

    def test_context_tools_present(self):
        """Context server's 5 tools must be in registry."""
        expected = {"fetch_docs", "list_sections", "get_icdev_metadata",
                    "get_project_context", "get_agent_context"}
        ctx_tools = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "context"}
        assert expected == ctx_tools

    def test_innovation_tools_present(self):
        """Innovation server's 10 tools must be in registry."""
        expected = {"scan_web", "score_signals", "triage_signals",
                    "detect_trends", "generate_solution", "run_pipeline",
                    "get_status", "introspect", "competitive_scan", "standards_check"}
        inn_tools = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "innovation"}
        assert expected == inn_tools

    def test_observability_tools_present(self):
        """Observability server's 6 tools must be in registry."""
        expected = {"trace_query", "trace_summary", "prov_lineage",
                    "prov_export", "shap_analyze", "xai_assess"}
        obs_tools = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "observability"}
        assert expected == obs_tools

    def test_observability_resources_present(self):
        """Observability server's 2 resources must be in registry."""
        assert "observability://config" in RESOURCE_REGISTRY
        assert "observability://stats" in RESOURCE_REGISTRY

    def test_mbse_generate_code_renamed(self):
        """MBSE's generate_code should be renamed to mbse_generate_code to avoid collision."""
        assert "mbse_generate_code" in TOOL_REGISTRY
        entry = TOOL_REGISTRY["mbse_generate_code"]
        assert entry["category"] == "mbse"
        assert "mbse" in entry["module"]


# ── Gap Handler Coverage ─────────────────────────────────────────

class TestGapHandlerCoverage:
    """Verify all 55 new gap handler tools are registered."""

    def test_translation_tools(self):
        """9 translation gap handler tools."""
        names = {"translate_code", "extract_source_ir", "translate_unit",
                 "map_dependencies", "check_types", "assemble_project",
                 "validate_translation", "translate_tests", "map_features"}
        actual = {n for n, e in TOOL_REGISTRY.items()
                  if e["category"] == "translation"}
        assert names == actual

    def test_dx_tools(self):
        """5 DX gap handler tools."""
        names = {"companion_setup", "detect_ai_tools", "generate_instructions",
                 "generate_mcp_configs", "translate_skills"}
        actual = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "dx"}
        assert names == actual

    def test_cloud_tools(self):
        """5 cloud gap handler tools."""
        names = {"csp_monitor_scan", "csp_changelog", "validate_region",
                 "cloud_mode_status", "csp_health_check"}
        actual = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "cloud"}
        assert names == actual

    def test_registry_tools(self):
        """9 registry gap handler tools."""
        names = {"register_child", "list_children", "get_genome",
                 "evaluate_capability", "list_staging", "list_propagations",
                 "absorption_candidates", "unevaluated_behaviors",
                 "cross_pollination_candidates"}
        actual = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "registry"}
        assert names == actual

    def test_security_agentic_tools(self):
        """9 security/agentic gap handler tools."""
        names = {"scan_code_patterns", "validate_tool_chain",
                 "validate_agent_output", "score_agent_trust",
                 "check_mcp_authorization", "ai_telemetry_summary",
                 "generate_ai_bom", "run_atlas_red_team",
                 "detect_behavioral_drift"}
        actual = {n for n, e in TOOL_REGISTRY.items()
                  if e["category"] == "security_agentic"}
        assert names == actual

    def test_testing_tools(self):
        """6 testing gap handler tools."""
        names = {"production_audit", "production_remediate",
                 "validate_claude_dir", "health_check",
                 "validate_screenshot", "run_e2e_tests"}
        actual = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "testing"}
        assert names == actual

    def test_installer_tools(self):
        """4 installer gap handler tools."""
        names = {"install_modules", "validate_module_registry",
                 "list_compliance_postures", "generate_platform_artifacts"}
        actual = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "installer"}
        assert names == actual

    def test_misc_tools(self):
        """8 misc gap handler tools."""
        names = {"register_external_patterns", "analyze_legacy_ui",
                 "generate_profile_md", "generate_claude_md",
                 "version_migrate", "framework_migrate",
                 "worktree_manage", "nlq_query"}
        actual = {n for n, e in TOOL_REGISTRY.items() if e["category"] == "misc"}
        assert names == actual

    def test_all_gap_handlers_point_to_gap_module(self):
        """All gap handler tools must reference tools.mcp.gap_handlers module."""
        gap_categories = {"translation", "dx", "cloud", "registry",
                          "security_agentic", "testing", "installer", "misc"}
        for name, entry in TOOL_REGISTRY.items():
            if entry["category"] in gap_categories:
                assert entry["module"] == "tools.mcp.gap_handlers", \
                    f"Tool '{name}' should reference gap_handlers, got {entry['module']}"


# ── Unified Server Lifecycle ─────────────────────────────────────

class TestUnifiedServerLifecycle:
    """Test server creation, tool listing, and lazy loading."""

    def test_server_creation(self):
        """create_server() must return a valid UnifiedMCPServer."""
        from tools.mcp.unified_server import UnifiedMCPServer, create_server
        server = create_server()
        assert isinstance(server, UnifiedMCPServer)
        assert server.name == "icdev-unified"

    def test_server_registers_all_tools(self):
        """Server must register all tools from the registry."""
        from tools.mcp.unified_server import create_server
        server = create_server()
        assert len(server._tools) == len(TOOL_REGISTRY)

    def test_server_registers_all_resources(self):
        """Server must register all 6 resources."""
        from tools.mcp.unified_server import create_server
        server = create_server()
        assert len(server._resources) == 6

    def test_handler_cache_empty_at_startup(self):
        """No handlers should be imported at startup (lazy loading)."""
        from tools.mcp.unified_server import create_server
        server = create_server()
        assert len(server._handler_cache) == 0

    def test_resolve_handler_caches(self):
        """Calling _resolve_handler twice should return same object."""
        from tools.mcp.unified_server import create_server
        server = create_server()
        entry = TOOL_REGISTRY["project_list"]
        h1 = server._resolve_handler("project_list", entry)
        h2 = server._resolve_handler("project_list", entry)
        assert h1 is h2
        assert "project_list" in server._handler_cache

    def test_resolve_handler_stub_on_bad_module(self):
        """Bad module path should return a graceful stub handler."""
        from tools.mcp.unified_server import create_server
        server = create_server()
        bad_entry = {
            "module": "nonexistent.module",
            "handler": "nonexistent_func",
        }
        handler = server._resolve_handler("fake_tool", bad_entry)
        result = handler({})
        assert "error" in result
        assert result["status"] == "pending"

    def test_lazy_tool_handler_calls_through(self):
        """Calling a registered tool handler should lazy-load and invoke the real handler."""
        from tools.mcp.unified_server import create_server
        server = create_server()

        # Find the lazy handler for project_list
        tool_entry = server._tools.get("project_list")
        assert tool_entry is not None
        handler = tool_entry["handler"]

        # Patch the real handler to verify it gets called
        with patch("tools.mcp.core_server.handle_project_list") as mock_fn:
            mock_fn.return_value = {"projects": [], "count": 0}
            result = handler({})
            mock_fn.assert_called_once_with({})
            assert result == {"projects": [], "count": 0}

    def test_tools_list_response_format(self):
        """Simulated tools/list should return properly formatted tool list."""
        from tools.mcp.unified_server import create_server
        server = create_server()
        tools_list = []
        for name, entry in server._tools.items():
            tools_list.append({
                "name": name,
                "description": entry.get("description", ""),
            })
        assert len(tools_list) == len(TOOL_REGISTRY)
        assert all("name" in t and "description" in t for t in tools_list)


# ── Module Path Validation ───────────────────────────────────────

class TestModulePathValidation:
    """Verify module paths reference real Python modules."""

    def test_existing_server_modules_exist(self):
        """All referenced existing server modules should be importable."""
        existing_modules = set()
        for entry in TOOL_REGISTRY.values():
            mod = entry["module"]
            if mod != "tools.mcp.gap_handlers":
                existing_modules.add(mod)
        for mod_path in existing_modules:
            try:
                __import__(mod_path)
            except ImportError:
                pytest.fail(f"Module {mod_path} is not importable")

    def test_gap_handlers_module_exists(self):
        """gap_handlers module should be importable."""
        import tools.mcp.gap_handlers  # noqa: F401

    def test_gap_handler_functions_exist(self):
        """All referenced handler functions must exist in gap_handlers."""
        import tools.mcp.gap_handlers as gh
        gap_categories = {"translation", "dx", "cloud", "registry",
                          "security_agentic", "testing", "installer", "misc"}
        for name, entry in TOOL_REGISTRY.items():
            if entry["category"] in gap_categories:
                handler_name = entry["handler"]
                assert hasattr(gh, handler_name), \
                    f"gap_handlers missing function: {handler_name} (for tool '{name}')"


# ── Representative Tool Calls ────────────────────────────────────

class TestRepresentativeToolCalls:
    """Call one representative tool per server category via the unified server."""

    @pytest.fixture
    def server(self):
        from tools.mcp.unified_server import create_server
        return create_server()

    def test_core_project_list(self, server):
        """Core: project_list should return without error."""
        handler = server._tools["project_list"]["handler"]
        with patch("tools.mcp.core_server.handle_project_list",
                    return_value={"projects": [], "count": 0}):
            result = handler({})
            assert "projects" in result

    def test_compliance_nist_lookup(self, server):
        """Compliance: nist_lookup should return without error."""
        handler = server._tools["nist_lookup"]["handler"]
        with patch("tools.mcp.compliance_server.handle_nist_lookup",
                    return_value={"control": "AC-2", "title": "Account Management"}):
            result = handler({"control_id": "AC-2"})
            assert "control" in result

    def test_gateway_status(self, server):
        """Gateway: gateway_status should return without error."""
        handler = server._tools["gateway_status"]["handler"]
        with patch("tools.mcp.gateway_server.handle_gateway_status",
                    return_value={"environment_mode": "connected", "channels": []}):
            result = handler({})
            assert "environment_mode" in result

    def test_context_list_sections(self, server):
        """Context: list_sections should return without error."""
        handler = server._tools["list_sections"]["handler"]
        with patch("tools.mcp.context_server.handle_list_sections",
                    return_value={"sections": [], "total": 0}):
            result = handler({})
            assert "sections" in result

    def test_innovation_get_status(self, server):
        """Innovation: get_status should return without error."""
        handler = server._tools["get_status"]["handler"]
        with patch("tools.mcp.innovation_server.handle_get_status",
                    return_value={"signals": 0, "status": "idle"}):
            result = handler({})
            assert "status" in result

    def test_observability_trace_summary(self, server):
        """Observability: trace_summary should return without error."""
        handler = server._tools["trace_summary"]["handler"]
        with patch("tools.mcp.observability_server.trace_summary_handler",
                    return_value={"total_spans": 0, "total_traces": 0}):
            result = handler({})
            assert "total_spans" in result
