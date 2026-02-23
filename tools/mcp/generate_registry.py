#!/usr/bin/env python3
"""Generate tool_registry.py by reading all 18 MCP server files.

This script imports each server, instantiates it to capture tool/resource
registrations, then writes the declarative registry file.

Usage:
    python tools/mcp/generate_registry.py
"""
import importlib
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

# Ensure DB path is set so servers can import
os.environ.setdefault("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))
os.environ.setdefault("ICDEV_PROJECT_ROOT", str(BASE_DIR))

SERVER_MODULES = [
    ("core", "tools.mcp.core_server"),
    ("compliance", "tools.mcp.compliance_server"),
    ("builder", "tools.mcp.builder_server"),
    ("infra", "tools.mcp.infra_server"),
    ("knowledge", "tools.mcp.knowledge_server"),
    ("maintenance", "tools.mcp.maintenance_server"),
    ("mbse", "tools.mcp.mbse_server"),
    ("modernization", "tools.mcp.modernization_server"),
    ("requirements", "tools.mcp.requirements_server"),
    ("supply_chain", "tools.mcp.supply_chain_server"),
    ("simulation", "tools.mcp.simulation_server"),
    ("integration", "tools.mcp.integration_server"),
    ("marketplace", "tools.mcp.marketplace_server"),
    ("devsecops", "tools.mcp.devsecops_server"),
    ("gateway", "tools.mcp.gateway_server"),
    ("context", "tools.mcp.context_server"),
    ("innovation", "tools.mcp.innovation_server"),
    ("observability", "tools.mcp.observability_server"),
]

# New tools from gap_handlers.py (these don't exist in any server yet)
GAP_HANDLER_TOOLS = {
    # ---- Translation (Phase 43) ----
    "translate_code": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_translate_code",
        "description": "Run full cross-language translation pipeline (Extract, Type-Check, Translate, Assemble, Validate+Repair).",
        "input_schema": {"type": "object", "properties": {"source_path": {"type": "string", "description": "Path to source code directory"}, "source_language": {"type": "string", "description": "Source language"}, "target_language": {"type": "string", "description": "Target language"}, "output_dir": {"type": "string", "description": "Output directory"}, "project_id": {"type": "string"}, "validate": {"type": "boolean", "default": True}, "dry_run": {"type": "boolean", "default": False}}, "required": ["source_path", "source_language", "target_language", "output_dir"]},
    },
    "extract_source_ir": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_extract_source_ir",
        "description": "Extract language-agnostic Intermediate Representation (IR) from source code.",
        "input_schema": {"type": "object", "properties": {"source_path": {"type": "string"}, "language": {"type": "string"}, "output_ir": {"type": "string"}, "project_id": {"type": "string"}}, "required": ["source_path", "language"]},
    },
    "translate_unit": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_translate_unit",
        "description": "Translate a single code unit from IR to target language with pass@k candidates.",
        "input_schema": {"type": "object", "properties": {"ir_file": {"type": "string"}, "source_language": {"type": "string"}, "target_language": {"type": "string"}, "output_dir": {"type": "string"}, "candidates": {"type": "integer", "default": 3}}, "required": ["ir_file", "source_language", "target_language", "output_dir"]},
    },
    "map_dependencies": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_map_dependencies",
        "description": "Map source language dependencies to target language equivalents.",
        "input_schema": {"type": "object", "properties": {"source_language": {"type": "string"}, "target_language": {"type": "string"}, "imports": {"type": "string", "description": "Comma-separated source imports"}}, "required": ["source_language", "target_language", "imports"]},
    },
    "check_types": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_check_types",
        "description": "Run type-compatibility pre-check between source and target type systems.",
        "input_schema": {"type": "object", "properties": {"ir_file": {"type": "string"}, "source_language": {"type": "string"}, "target_language": {"type": "string"}}, "required": ["ir_file", "source_language", "target_language"]},
    },
    "assemble_project": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_assemble_project",
        "description": "Assemble translated units into a complete target-language project.",
        "input_schema": {"type": "object", "properties": {"output_dir": {"type": "string"}, "target_language": {"type": "string"}, "project_name": {"type": "string"}}, "required": ["output_dir", "target_language"]},
    },
    "validate_translation": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_validate_translation",
        "description": "Validate translated code (syntax, API surface, round-trip IR consistency).",
        "input_schema": {"type": "object", "properties": {"source_ir": {"type": "string"}, "output_dir": {"type": "string"}, "target_language": {"type": "string"}}, "required": ["source_ir", "output_dir", "target_language"]},
    },
    "translate_tests": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_translate_tests",
        "description": "Translate test suite from source to target language with framework-specific assertion mapping.",
        "input_schema": {"type": "object", "properties": {"source_test_dir": {"type": "string"}, "source_language": {"type": "string"}, "target_language": {"type": "string"}, "output_dir": {"type": "string"}, "ir_file": {"type": "string"}}, "required": ["source_test_dir", "source_language", "target_language", "output_dir"]},
    },
    "map_features": {
        "category": "translation",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_map_features",
        "description": "Map language-specific features between source and target languages.",
        "input_schema": {"type": "object", "properties": {"source_language": {"type": "string"}, "target_language": {"type": "string"}, "feature_category": {"type": "string"}}, "required": ["source_language", "target_language"]},
    },
    # ---- DX / Companion (Phase 34/DX) ----
    "companion_setup": {
        "category": "dx",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_companion_setup",
        "description": "Auto-detect AI coding tools and generate all companion configuration files.",
        "input_schema": {"type": "object", "properties": {"platforms": {"type": "string", "description": "Comma-separated platforms or 'all'"}, "write": {"type": "boolean", "default": True}, "sync": {"type": "boolean", "default": False}}},
    },
    "detect_ai_tools": {
        "category": "dx",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_detect_ai_tools",
        "description": "Detect installed AI coding tools from environment, config files, and directories.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "generate_instructions": {
        "category": "dx",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_generate_instructions",
        "description": "Generate instruction/rules files for AI coding tools.",
        "input_schema": {"type": "object", "properties": {"platforms": {"type": "string"}, "write": {"type": "boolean", "default": True}}},
    },
    "generate_mcp_configs": {
        "category": "dx",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_generate_mcp_configs",
        "description": "Generate MCP configuration files for AI tools that support MCP.",
        "input_schema": {"type": "object", "properties": {"platforms": {"type": "string"}, "write": {"type": "boolean", "default": True}}},
    },
    "translate_skills": {
        "category": "dx",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_translate_skills",
        "description": "Translate Claude Code skills to equivalent formats for other AI tools.",
        "input_schema": {"type": "object", "properties": {"platforms": {"type": "string"}, "write": {"type": "boolean", "default": True}}},
    },
    # ---- Cloud (Phase 38) ----
    "csp_monitor_scan": {
        "category": "cloud",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_csp_monitor_scan",
        "description": "Scan CSPs for service updates, deprecations, and compliance changes.",
        "input_schema": {"type": "object", "properties": {"csp": {"type": "string"}, "all_csps": {"type": "boolean", "default": True}}},
    },
    "csp_changelog": {
        "category": "cloud",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_csp_changelog",
        "description": "Generate CSP service changelog with recommendations.",
        "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "default": 30}, "format": {"type": "string", "enum": ["json", "markdown"], "default": "json"}, "output": {"type": "string"}}},
    },
    "validate_region": {
        "category": "cloud",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_validate_region",
        "description": "Validate that a CSP region meets required compliance certifications.",
        "input_schema": {"type": "object", "properties": {"csp": {"type": "string"}, "region": {"type": "string"}, "frameworks": {"type": "string"}, "impact_level": {"type": "string"}}, "required": ["csp", "region"]},
    },
    "cloud_mode_status": {
        "category": "cloud",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_cloud_mode_status",
        "description": "Get current cloud mode, CSP configuration, and readiness status.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["status", "validate", "eligible", "check_readiness"], "default": "status"}}},
    },
    "csp_health_check": {
        "category": "cloud",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_csp_health_check",
        "description": "Check health of all configured CSP services.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- Registry / Evolutionary Intelligence (Phase 36) ----
    "register_child": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_register_child",
        "description": "Register a child application in the evolutionary intelligence registry.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "app_type": {"type": "string"}, "endpoint": {"type": "string"}, "project_id": {"type": "string"}}, "required": ["name", "app_type"]},
    },
    "list_children": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_list_children",
        "description": "List all registered child applications with health status.",
        "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
    },
    "get_genome": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_get_genome",
        "description": "Get the current capability genome version with SHA-256 content hash.",
        "input_schema": {"type": "object", "properties": {"version": {"type": "string"}, "history": {"type": "boolean", "default": False}}},
    },
    "evaluate_capability": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_evaluate_capability",
        "description": "Evaluate a capability across 6 dimensions (universality, compliance_safety, risk, evidence, novelty, cost).",
        "input_schema": {"type": "object", "properties": {"capability_data": {"type": "object"}, "child_id": {"type": "string"}}, "required": ["capability_data"]},
    },
    "list_staging": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_list_staging",
        "description": "List staging environments for capability testing.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "list_propagations": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_list_propagations",
        "description": "List capability propagation history.",
        "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
    },
    "absorption_candidates": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_absorption_candidates",
        "description": "Get capabilities eligible for genome absorption (passed 72-hour stability window).",
        "input_schema": {"type": "object", "properties": {}},
    },
    "unevaluated_behaviors": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_unevaluated_behaviors",
        "description": "Get learned behaviors from children that haven't been evaluated yet.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "cross_pollination_candidates": {
        "category": "registry",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_cross_pollination_candidates",
        "description": "Find capabilities that could be shared between child applications.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- Security Agentic (Phase 45) ----
    "scan_code_patterns": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_scan_code_patterns",
        "description": "Scan code for dangerous patterns (eval, exec, os.system, SQL injection) across 6 languages.",
        "input_schema": {"type": "object", "properties": {"project_dir": {"type": "string"}, "language": {"type": "string"}, "gate": {"type": "boolean", "default": False}}, "required": ["project_dir"]},
    },
    "validate_tool_chain": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_validate_tool_chain",
        "description": "Validate MCP tool call sequences against security rules.",
        "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "gate": {"type": "boolean", "default": False}}, "required": ["project_id"]},
    },
    "validate_agent_output": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_validate_agent_output",
        "description": "Validate agent output for classification leaks, PII, and credential exposure.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}, "project_id": {"type": "string"}, "gate": {"type": "boolean", "default": False}}},
    },
    "score_agent_trust": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_score_agent_trust",
        "description": "Compute dynamic trust score for an agent based on behavior history.",
        "input_schema": {"type": "object", "properties": {"agent_id": {"type": "string"}, "all_agents": {"type": "boolean", "default": False}, "gate": {"type": "boolean", "default": False}, "project_id": {"type": "string"}}},
    },
    "check_mcp_authorization": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_check_mcp_authorization",
        "description": "Check if a role is authorized to use a specific MCP tool.",
        "input_schema": {"type": "object", "properties": {"role": {"type": "string"}, "tool": {"type": "string"}, "list_permissions": {"type": "boolean", "default": False}, "validate_config": {"type": "boolean", "default": False}}},
    },
    "ai_telemetry_summary": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_ai_telemetry_summary",
        "description": "Get AI telemetry summary including usage stats and anomaly detection.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["summary", "anomalies", "drift"], "default": "summary"}, "window_hours": {"type": "integer", "default": 24}, "agent_id": {"type": "string"}}},
    },
    "generate_ai_bom": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_generate_ai_bom",
        "description": "Generate AI Bill of Materials tracking all AI/ML components.",
        "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "project_dir": {"type": "string"}, "gate": {"type": "boolean", "default": False}}, "required": ["project_id"]},
    },
    "run_atlas_red_team": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_run_atlas_red_team",
        "description": "Run MITRE ATLAS red team tests (opt-in only).",
        "input_schema": {"type": "object", "properties": {"project_id": {"type": "string"}, "technique": {"type": "string"}, "behavioral": {"type": "boolean", "default": False}, "brt_technique": {"type": "string"}}, "required": ["project_id"]},
    },
    "detect_behavioral_drift": {
        "category": "security_agentic",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_detect_behavioral_drift",
        "description": "Detect behavioral drift in agent tool usage patterns using z-score baseline.",
        "input_schema": {"type": "object", "properties": {"agent_id": {"type": "string"}}},
    },
    # ---- Testing ----
    "production_audit": {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_production_audit",
        "description": "Run production readiness audit (30 checks across 6 categories).",
        "input_schema": {"type": "object", "properties": {"category": {"type": "string"}, "gate": {"type": "boolean", "default": False}}},
    },
    "production_remediate": {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_production_remediate",
        "description": "Auto-fix audit blockers with 3-tier confidence model.",
        "input_schema": {"type": "object", "properties": {"auto": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": False}, "check_id": {"type": "string"}, "skip_audit": {"type": "boolean", "default": False}}},
    },
    "validate_claude_dir": {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_validate_claude_dir",
        "description": "Validate .claude directory governance (append-only tables, hooks, routes, deny rules).",
        "input_schema": {"type": "object", "properties": {"check": {"type": "string"}}},
    },
    "health_check": {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_health_check",
        "description": "Run full system health check.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "validate_screenshot": {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_validate_screenshot",
        "description": "Validate a screenshot using vision LLM for content verification.",
        "input_schema": {"type": "object", "properties": {"image": {"type": "string"}, "assertion": {"type": "string"}, "batch_dir": {"type": "string"}, "check": {"type": "boolean", "default": False}}},
    },
    "run_e2e_tests": {
        "category": "testing",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_run_e2e_tests",
        "description": "Run E2E tests via Playwright MCP.",
        "input_schema": {"type": "object", "properties": {"test_file": {"type": "string"}, "run_all": {"type": "boolean", "default": False}, "validate_screenshots": {"type": "boolean", "default": False}, "vision_strict": {"type": "boolean", "default": False}}},
    },
    # ---- Installer (Phase 33) ----
    "install_modules": {
        "category": "installer",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_install_modules",
        "description": "Install ICDEV modules using profile-based or custom module selection.",
        "input_schema": {"type": "object", "properties": {"profile": {"type": "string"}, "compliance": {"type": "string"}, "platform": {"type": "string"}, "add_module": {"type": "string"}, "interactive": {"type": "boolean", "default": False}}},
    },
    "validate_module_registry": {
        "category": "installer",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_validate_module_registry",
        "description": "Validate the ICDEV module registry for dependency consistency.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "list_compliance_postures": {
        "category": "installer",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_list_compliance_postures",
        "description": "List available compliance posture configurations.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "generate_platform_artifacts": {
        "category": "installer",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_generate_platform_artifacts",
        "description": "Generate platform-specific deployment artifacts (Docker, K8s, Helm, env).",
        "input_schema": {"type": "object", "properties": {"platform": {"type": "string"}, "modules": {"type": "string"}}, "required": ["platform"]},
    },
    # ---- Misc ----
    "register_external_patterns": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_register_external_patterns",
        "description": "Register external framework patterns as innovation signals.",
        "input_schema": {"type": "object", "properties": {"patterns": {"type": "array"}, "source_framework": {"type": "string"}}, "required": ["patterns", "source_framework"]},
    },
    "analyze_legacy_ui": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_analyze_legacy_ui",
        "description": "Analyze legacy UI screenshots for complexity scoring and component extraction.",
        "input_schema": {"type": "object", "properties": {"image": {"type": "string"}, "image_dir": {"type": "string"}, "app_id": {"type": "string"}, "project_id": {"type": "string"}, "store": {"type": "boolean", "default": False}}},
    },
    "generate_profile_md": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_generate_profile_md",
        "description": "Generate PROFILE.md from a development profile.",
        "input_schema": {"type": "object", "properties": {"scope": {"type": "string", "default": "project"}, "scope_id": {"type": "string"}, "output": {"type": "string"}, "store": {"type": "boolean", "default": False}}, "required": ["scope_id"]},
    },
    "generate_claude_md": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_generate_claude_md",
        "description": "Generate dynamic CLAUDE.md for a child application from a blueprint.",
        "input_schema": {"type": "object", "properties": {"blueprint": {"type": "string"}, "output": {"type": "string"}}, "required": ["blueprint"]},
    },
    "version_migrate": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_version_migrate",
        "description": "Run language version migration (e.g., Python 2 to 3, Java 8 to 17).",
        "input_schema": {"type": "object", "properties": {"source": {"type": "string"}, "output": {"type": "string"}, "language": {"type": "string", "enum": ["python", "java", "csharp"]}, "from_version": {"type": "string"}, "to_version": {"type": "string"}}, "required": ["source", "output", "language", "from_version", "to_version"]},
    },
    "framework_migrate": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_framework_migrate",
        "description": "Run framework migration (e.g., Struts to Spring Boot, WCF to ASP.NET Core).",
        "input_schema": {"type": "object", "properties": {"source": {"type": "string"}, "output": {"type": "string"}, "from_framework": {"type": "string"}, "to_framework": {"type": "string"}}, "required": ["source", "output", "from_framework", "to_framework"]},
    },
    "worktree_manage": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_worktree_manage",
        "description": "Manage git worktrees for parallel CI/CD task isolation.",
        "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["create", "list", "cleanup", "status"], "default": "list"}, "task_id": {"type": "string"}, "target_dir": {"type": "string"}, "worktree_name": {"type": "string"}}},
    },
    "nlq_query": {
        "category": "misc",
        "module": "tools.mcp.gap_handlers",
        "handler": "handle_nlq_query",
        "description": "Run natural language compliance query (NLQ to SQL).",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "project_id": {"type": "string"}}, "required": ["query"]},
    },
}


def extract_from_server(category: str, module_path: str) -> tuple:
    """Import a server module, instantiate it, and extract registered tools/resources."""
    tools = {}
    resources = {}

    try:
        mod = importlib.import_module(module_path)
    except Exception as e:
        print(f"  WARNING: Cannot import {module_path}: {e}", file=sys.stderr)
        return tools, resources

    # Find the create_server() factory or server class
    server = None
    if hasattr(mod, "create_server"):
        try:
            server = mod.create_server()
        except Exception as e:
            print(f"  WARNING: create_server() failed for {module_path}: {e}", file=sys.stderr)

    if server is None:
        # Try to find the server class directly
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and attr_name.endswith("Server") and attr_name != "MCPServer":
                try:
                    server = attr()
                    break
                except Exception:
                    pass

    if server is None:
        print(f"  WARNING: Could not instantiate server from {module_path}", file=sys.stderr)
        return tools, resources

    # Extract tools
    for tool_name, tool_info in server._tools.items():
        # For MBSE generate_code, rename to avoid collision with builder's generate_code
        registry_name = tool_name
        if category == "mbse" and tool_name == "generate_code":
            registry_name = "mbse_generate_code"

        # Try to find the original handler function name by looking at the module
        handler_name = f"handle_{tool_name}"

        # Check if handler is directly a module-level function
        handler_fn = tool_info.get("handler")
        if handler_fn and hasattr(handler_fn, "__name__"):
            handler_name = handler_fn.__name__

        tools[registry_name] = {
            "category": category,
            "module": module_path,
            "handler": handler_name,
            "description": tool_info["description"],
            "input_schema": tool_info["input_schema"],
        }

    # Extract resources
    for uri, res_info in server._resources.items():
        handler_fn = res_info.get("handler")
        handler_name = handler_fn.__name__ if handler_fn and hasattr(handler_fn, "__name__") else f"handle_resource_{uri.split('://')[-1].replace('/', '_')}"

        resources[uri] = {
            "name": res_info["name"],
            "description": res_info["description"],
            "module": module_path,
            "handler": handler_name,
            "mime_type": res_info.get("mime_type", "application/json"),
        }

    return tools, resources


def format_dict(d: dict, indent: int = 4) -> str:
    """Format a dict as Python source code."""
    # Use json.dumps for the schema dicts, then convert to Python syntax
    s = json.dumps(d, indent=indent, default=str, ensure_ascii=False)
    # Convert JSON booleans to Python
    s = s.replace(": true", ": True").replace(": false", ": False").replace(": null", ": None")
    return s


def main():
    all_tools = {}
    all_resources = {}

    print("Extracting tools from 18 MCP servers...", file=sys.stderr)
    for category, module_path in SERVER_MODULES:
        print(f"  {category}: {module_path}...", file=sys.stderr)
        tools, resources = extract_from_server(category, module_path)
        print(f"    -> {len(tools)} tools, {len(resources)} resources", file=sys.stderr)
        all_tools.update(tools)
        all_resources.update(resources)

    print(f"\nAdding {len(GAP_HANDLER_TOOLS)} gap handler tools...", file=sys.stderr)
    all_tools.update(GAP_HANDLER_TOOLS)

    print(f"\nTotal: {len(all_tools)} tools, {len(all_resources)} resources", file=sys.stderr)

    # Generate the registry file
    output_path = BASE_DIR / "tools" / "mcp" / "tool_registry.py"

    # Group tools by category for readability
    categories = {}
    for name, info in all_tools.items():
        cat = info["category"]
        if cat not in categories:
            categories[cat] = {}
        categories[cat][name] = info

    lines = []
    lines.append('#!/usr/bin/env python3')
    lines.append('# CUI // SP-CTI')
    lines.append('"""Declarative tool registry for the Unified MCP Gateway Server (D301).')
    lines.append('')
    lines.append('Maps tool name -> (module, handler, schema) for all ICDEV tools.')
    lines.append('Used by unified_server.py for lazy-loaded handler dispatch.')
    lines.append('')
    lines.append('Auto-generated by generate_registry.py — do not edit manually.')
    lines.append('')

    # Category summary
    cat_counts = {cat: len(tools) for cat, tools in categories.items()}
    lines.append('Categories:')
    for cat, count in cat_counts.items():
        lines.append(f'    {cat} ({count})')
    lines.append(f'')
    lines.append(f'Total: {len(all_tools)} tools, {len(all_resources)} resources')
    lines.append('"""')
    lines.append('')
    lines.append('')
    lines.append('TOOL_REGISTRY = {')

    for cat_name in categories:
        cat_tools = categories[cat_name]
        lines.append(f'    # {"=" * 60}')
        lines.append(f'    # {cat_name.upper()} ({len(cat_tools)} tools)')
        lines.append(f'    # {"=" * 60}')

        for tool_name, tool_info in cat_tools.items():
            schema_str = json.dumps(tool_info["input_schema"], separators=(", ", ": "), default=str)
            schema_str = schema_str.replace(": true", ": True").replace(": false", ": False").replace(": null", ": None")

            lines.append(f'    "{tool_name}": {{')
            lines.append(f'        "category": "{tool_info["category"]}",')
            lines.append(f'        "module": "{tool_info["module"]}",')
            lines.append(f'        "handler": "{tool_info["handler"]}",')
            # Escape description for Python string
            desc = tool_info["description"].replace('"', '\\"')
            lines.append(f'        "description": "{desc}",')
            lines.append(f'        "input_schema": {schema_str},')
            lines.append(f'    }},')

    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('RESOURCE_REGISTRY = {')

    for uri, res_info in all_resources.items():
        lines.append(f'    "{uri}": {{')
        lines.append(f'        "name": "{res_info["name"]}",')
        desc = res_info["description"].replace('"', '\\"')
        lines.append(f'        "description": "{desc}",')
        lines.append(f'        "module": "{res_info["module"]}",')
        lines.append(f'        "handler": "{res_info["handler"]}",')
        lines.append(f'        "mime_type": "{res_info["mime_type"]}",')
        lines.append(f'    }},')

    lines.append('}')
    lines.append('')

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nGenerated: {output_path}", file=sys.stderr)
    print(f"  {len(all_tools)} tools, {len(all_resources)} resources", file=sys.stderr)


if __name__ == "__main__":
    main()
