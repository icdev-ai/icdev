#!/usr/bin/env python3
# CUI // SP-CTI
"""Modernization MCP server exposing legacy analysis, 7R assessment, migration planning, and code generation tools.

Tools:
    register_legacy_app  - Register a legacy application for analysis
    analyze_legacy       - Run full legacy code analysis (AST + regex)
    extract_architecture - Reverse-engineer architecture from analyzed code
    generate_docs        - Generate documentation from analysis data
    assess_seven_r       - Run 7R migration strategy assessment
    create_migration_plan - Create migration plan with tasks
    track_migration      - Get migration progress and velocity
    generate_migration_code - Generate adapters, facades, scaffolding
    check_compliance_bridge - Validate ATO coverage during migration
    migrate_version      - Run version/framework migration transforms

Runs as an MCP server over stdio with Content-Length framing.
"""

import json
import os
import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402


# ---------------------------------------------------------------------------
# Lazy tool imports
# ---------------------------------------------------------------------------

def _import_tool(module_path, func_name):
    """Dynamically import a function from a module. Returns None if unavailable."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_register_legacy_app(args: dict) -> dict:
    """Register a legacy application for analysis."""
    register = _import_tool("tools.modernization.legacy_analyzer", "register_application")
    if not register:
        return {"error": "legacy_analyzer module not available", "status": "pending"}

    project_id = args.get("project_id")
    name = args.get("name")
    source_path = args.get("source_path")
    if not all([project_id, name, source_path]):
        return {"error": "project_id, name, and source_path are required"}

    try:
        return register(project_id, name, source_path, description=args.get("description"))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_analyze_legacy(args: dict) -> dict:
    """Run full legacy code analysis."""
    analyze = _import_tool("tools.modernization.legacy_analyzer", "analyze_full")
    if not analyze:
        return {"error": "legacy_analyzer module not available", "status": "pending"}

    project_id = args.get("project_id")
    app_id = args.get("app_id")
    if not all([project_id, app_id]):
        return {"error": "project_id and app_id are required"}

    try:
        return analyze(project_id, app_id)
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_extract_architecture(args: dict) -> dict:
    """Reverse-engineer architecture from analyzed legacy code."""
    extract = _import_tool("tools.modernization.architecture_extractor", "generate_architecture_summary")
    if not extract:
        return {"error": "architecture_extractor module not available", "status": "pending"}

    app_id = args.get("app_id")
    if not app_id:
        return {"error": "app_id is required"}

    try:
        return extract(app_id)
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_generate_docs(args: dict) -> dict:
    """Generate documentation from legacy code analysis."""
    gen_docs = _import_tool("tools.modernization.doc_generator", "generate_full_documentation")
    if not gen_docs:
        return {"error": "doc_generator module not available", "status": "pending"}

    app_id = args.get("app_id")
    output_dir = args.get("output_dir")
    if not all([app_id, output_dir]):
        return {"error": "app_id and output_dir are required"}

    try:
        return gen_docs(app_id, output_dir)
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_assess_seven_r(args: dict) -> dict:
    """Run 7R migration strategy assessment with scored decision matrix."""
    assess = _import_tool("tools.modernization.seven_r_assessor", "run_seven_r_assessment")
    if not assess:
        return {"error": "seven_r_assessor module not available", "status": "pending"}

    project_id = args.get("project_id")
    app_id = args.get("app_id")
    if not all([project_id, app_id]):
        return {"error": "project_id and app_id are required"}

    try:
        custom_weights = None
        weights_path = args.get("weights_path")
        if weights_path and Path(weights_path).exists():
            with open(weights_path) as f:
                custom_weights = json.load(f)
        return assess(project_id, app_id, custom_weights=custom_weights)
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_create_migration_plan(args: dict) -> dict:
    """Create a migration plan with tasks based on assessment."""
    create_plan = _import_tool("tools.modernization.monolith_decomposer", "create_migration_plan")
    if not create_plan:
        return {"error": "monolith_decomposer module not available", "status": "pending"}

    project_id = args.get("project_id")
    app_id = args.get("app_id")
    strategy = args.get("strategy")
    if not all([project_id, app_id, strategy]):
        return {"error": "project_id, app_id, and strategy are required"}

    try:
        return create_plan(
            project_id, app_id, strategy,
            target_lang=args.get("target_language"),
            target_framework=args.get("target_framework"),
            target_db=args.get("target_database"),
            target_arch=args.get("target_architecture", "microservices"),
            approach=args.get("migration_approach", "strangler_fig"),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_track_migration(args: dict) -> dict:
    """Get migration progress, velocity, and burndown."""
    plan_id = args.get("plan_id")
    if not plan_id:
        return {"error": "plan_id is required"}

    action = args.get("action", "dashboard")

    try:
        if action == "snapshot":
            snapshot = _import_tool("tools.modernization.migration_tracker", "create_pi_migration_snapshot")
            if not snapshot:
                return {"error": "migration_tracker module not available", "status": "pending"}
            pi_number = args.get("pi_number")
            snapshot_type = args.get("snapshot_type", "manual")
            return snapshot(plan_id, pi_number, snapshot_type)
        elif action == "velocity":
            velocity = _import_tool("tools.modernization.migration_tracker", "get_migration_velocity")
            if not velocity:
                return {"error": "migration_tracker module not available", "status": "pending"}
            return velocity(plan_id)
        elif action == "burndown":
            burndown = _import_tool("tools.modernization.migration_tracker", "get_migration_burndown")
            if not burndown:
                return {"error": "migration_tracker module not available", "status": "pending"}
            return burndown(plan_id)
        elif action == "dashboard":
            dashboard = _import_tool("tools.modernization.migration_tracker", "get_dashboard")
            if not dashboard:
                return {"error": "migration_tracker module not available", "status": "pending"}
            return dashboard(plan_id)
        else:
            return {"error": f"Unknown action: {action}. Use: snapshot, velocity, burndown, dashboard"}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_generate_migration_code(args: dict) -> dict:
    """Generate adapters, facades, scaffolding, and tests."""
    gen_all = _import_tool("tools.modernization.migration_code_generator", "generate_all")
    if not gen_all:
        return {"error": "migration_code_generator module not available", "status": "pending"}

    plan_id = args.get("plan_id")
    output_dir = args.get("output_dir")
    if not all([plan_id, output_dir]):
        return {"error": "plan_id and output_dir are required"}

    try:
        return gen_all(plan_id, output_dir)
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_check_compliance_bridge(args: dict) -> dict:
    """Validate ATO coverage during migration."""
    plan_id = args.get("plan_id")
    if not plan_id:
        return {"error": "plan_id is required"}

    action = args.get("action", "validate")

    try:
        if action == "validate":
            validate = _import_tool("tools.modernization.compliance_bridge", "validate_ato_coverage")
            if not validate:
                return {"error": "compliance_bridge module not available", "status": "pending"}
            return validate(plan_id)
        elif action == "gaps":
            gaps = _import_tool("tools.modernization.compliance_bridge", "identify_ato_gaps")
            if not gaps:
                return {"error": "compliance_bridge module not available", "status": "pending"}
            return gaps(plan_id)
        elif action == "dashboard":
            dashboard = _import_tool("tools.modernization.compliance_bridge", "get_compliance_dashboard")
            if not dashboard:
                return {"error": "compliance_bridge module not available", "status": "pending"}
            return dashboard(plan_id)
        elif action == "report":
            report = _import_tool("tools.modernization.compliance_bridge", "generate_ato_impact_report")
            if not report:
                return {"error": "compliance_bridge module not available", "status": "pending"}
            return report(plan_id, output_dir=args.get("output_dir"))
        else:
            return {"error": f"Unknown action: {action}. Use: validate, gaps, dashboard, report"}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_migrate_version(args: dict) -> dict:
    """Run version or framework migration transforms."""
    source_path = args.get("source_path")
    output_path = args.get("output_path")
    if not all([source_path, output_path]):
        return {"error": "source_path and output_path are required"}

    migration_type = args.get("migration_type", "version")

    try:
        if migration_type == "version":
            language = args.get("language")
            from_ver = args.get("from_version")
            to_ver = args.get("to_version")
            if not all([language, from_ver, to_ver]):
                return {"error": "language, from_version, and to_version are required for version migration"}

            if language == "python":
                migrate = _import_tool("tools.modernization.version_migrator", "migrate_python2_to_3")
            elif language == "java":
                migrate = _import_tool("tools.modernization.version_migrator", "migrate_java_version")
            elif language == "csharp":
                migrate = _import_tool("tools.modernization.version_migrator", "migrate_dotnet_framework")
            else:
                return {"error": f"Unsupported language: {language}"}

            if not migrate:
                return {"error": "version_migrator module not available", "status": "pending"}

            if language == "python":
                return migrate(source_path, output_path)
            else:
                return migrate(source_path, output_path, from_ver, to_ver)

        elif migration_type == "framework":
            from_fw = args.get("from_framework")
            to_fw = args.get("to_framework")
            if not all([from_fw, to_fw]):
                return {"error": "from_framework and to_framework are required for framework migration"}

            fw_map = {
                ("struts", "spring-boot"): "migrate_struts_to_spring",
                ("ejb", "spring"): "migrate_ejb_to_spring",
                ("wcf", "aspnet-core-grpc"): "migrate_wcf_to_aspnet_core",
                ("webforms", "razor"): "migrate_webforms_to_razor",
                ("django-1", "django-4"): "migrate_django_version",
                ("flask-0", "flask-3"): "migrate_flask_version",
            }

            func_name = fw_map.get((from_fw, to_fw))
            if not func_name:
                return {"error": f"Unsupported framework migration: {from_fw} → {to_fw}"}

            migrate = _import_tool("tools.modernization.framework_migrator", func_name)
            if not migrate:
                return {"error": "framework_migrator module not available", "status": "pending"}

            return migrate(source_path, output_path)
        else:
            return {"error": f"Unknown migration_type: {migration_type}. Use: version, framework"}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the Modernization MCP server."""
    server = MCPServer(name="icdev-modernization", version="1.0.0")

    server.register_tool(
        name="register_legacy_app",
        description="Register a legacy application for modernization analysis",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "name": {"type": "string", "description": "Application name"},
                "source_path": {"type": "string", "description": "Path to legacy source code"},
                "description": {"type": "string", "description": "Application description"},
            },
            "required": ["project_id", "name", "source_path"],
        },
        handler=handle_register_legacy_app,
    )

    server.register_tool(
        name="analyze_legacy",
        description="Run full legacy code analysis — AST parsing, dependency extraction, framework detection, API discovery, complexity metrics",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "app_id": {"type": "string", "description": "Legacy application ID"},
            },
            "required": ["project_id", "app_id"],
        },
        handler=handle_analyze_legacy,
    )

    server.register_tool(
        name="extract_architecture",
        description="Reverse-engineer architecture from analyzed legacy code — call graph, component diagram, data flow, service boundaries",
        input_schema={
            "type": "object",
            "properties": {
                "app_id": {"type": "string", "description": "Legacy application ID"},
            },
            "required": ["app_id"],
        },
        handler=handle_extract_architecture,
    )

    server.register_tool(
        name="generate_docs",
        description="Generate documentation from legacy code analysis — API docs, data dictionary, component docs, dependency map",
        input_schema={
            "type": "object",
            "properties": {
                "app_id": {"type": "string", "description": "Legacy application ID"},
                "output_dir": {"type": "string", "description": "Output directory for generated documentation"},
            },
            "required": ["app_id", "output_dir"],
        },
        handler=handle_generate_docs,
    )

    server.register_tool(
        name="assess_seven_r",
        description="Run 7R migration strategy assessment — scores all 7 Rs (Rehost, Replatform, Refactor, Re-architect, Repurchase, Retire, Retain) with weighted decision matrix",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "app_id": {"type": "string", "description": "Legacy application ID"},
                "weights_path": {"type": "string", "description": "Optional path to custom scoring weights JSON"},
            },
            "required": ["project_id", "app_id"],
        },
        handler=handle_assess_seven_r,
    )

    server.register_tool(
        name="create_migration_plan",
        description="Create a migration plan with decomposition tasks, timeline, and effort estimates",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "app_id": {"type": "string", "description": "Legacy application ID"},
                "strategy": {"type": "string", "enum": ["rehost", "replatform", "refactor", "rearchitect", "repurchase", "retire", "retain"]},
                "target_language": {"type": "string", "description": "Target programming language"},
                "target_framework": {"type": "string", "description": "Target framework"},
                "target_database": {"type": "string", "description": "Target database"},
                "target_architecture": {"type": "string", "default": "microservices", "enum": ["microservices", "modular_monolith", "serverless", "event_driven", "layered", "hexagonal"]},
                "migration_approach": {"type": "string", "default": "strangler_fig", "enum": ["big_bang", "strangler_fig", "parallel_run", "blue_green", "canary", "phased"]},
            },
            "required": ["project_id", "app_id", "strategy"],
        },
        handler=handle_create_migration_plan,
    )

    server.register_tool(
        name="track_migration",
        description="Track migration progress — PI snapshots, velocity, burndown, dashboard",
        input_schema={
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Migration plan ID"},
                "action": {"type": "string", "default": "dashboard", "enum": ["snapshot", "velocity", "burndown", "dashboard"]},
                "pi_number": {"type": "string", "description": "PI identifier (e.g., PI-25.3) — required for snapshot"},
                "snapshot_type": {"type": "string", "default": "manual", "enum": ["pi_start", "pi_end", "milestone", "manual"]},
            },
            "required": ["plan_id"],
        },
        handler=handle_track_migration,
    )

    server.register_tool(
        name="generate_migration_code",
        description="Generate migration code — adapters, facades, service scaffolding, data access layers, tests, rollback scripts",
        input_schema={
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Migration plan ID"},
                "output_dir": {"type": "string", "description": "Output directory for generated code"},
            },
            "required": ["plan_id", "output_dir"],
        },
        handler=handle_generate_migration_code,
    )

    server.register_tool(
        name="check_compliance_bridge",
        description="Validate ATO compliance coverage during migration — control inheritance, gap analysis, coverage validation",
        input_schema={
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Migration plan ID"},
                "action": {"type": "string", "default": "validate", "enum": ["validate", "gaps", "dashboard", "report"]},
                "output_dir": {"type": "string", "description": "Output directory for reports"},
            },
            "required": ["plan_id"],
        },
        handler=handle_check_compliance_bridge,
    )

    server.register_tool(
        name="migrate_version",
        description="Run version or framework migration transforms — Python 2→3, Java 8→17, .NET Framework→.NET 8, Struts→Spring, WCF→ASP.NET Core, etc.",
        input_schema={
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Source code directory"},
                "output_path": {"type": "string", "description": "Output directory for transformed code"},
                "migration_type": {"type": "string", "default": "version", "enum": ["version", "framework"]},
                "language": {"type": "string", "description": "Language (for version migration)", "enum": ["python", "java", "csharp"]},
                "from_version": {"type": "string", "description": "Source version (e.g., 2.7, 8, 4.8)"},
                "to_version": {"type": "string", "description": "Target version (e.g., 3.11, 17, 8.0)"},
                "from_framework": {"type": "string", "description": "Source framework (for framework migration)"},
                "to_framework": {"type": "string", "description": "Target framework (for framework migration)"},
            },
            "required": ["source_path", "output_path"],
        },
        handler=handle_migrate_version,
    )

    return server


def main():
    """Run the Modernization MCP server."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
