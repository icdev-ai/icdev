#!/usr/bin/env python3
# CUI // SP-CTI
"""Supply Chain MCP server exposing SCRM, dependency graph, ISA management, CVE triage, and boundary analysis tools.

Tools:
    register_ato_system    - Register existing ATO boundary
    assess_boundary_impact - Assess requirement's ATO boundary impact
    generate_red_alternative - Generate alternative COAs for RED-tier requirements
    add_vendor             - Register a supply chain vendor
    build_dependency_graph - Build/query dependency graph
    propagate_impact       - Propagate impact through dependency graph
    manage_isa             - ISA/MOU lifecycle management
    assess_scrm            - NIST 800-161 SCRM assessment
    triage_cve             - CVE triage with blast radius

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

def handle_register_ato_system(args: dict) -> dict:
    """Register an existing ATO boundary system."""
    register_system = _import_tool("tools.requirements.boundary_analyzer", "register_system")
    if not register_system:
        return {"error": "boundary_analyzer module not available", "status": "pending"}

    project_id = args.get("project_id")
    system_name = args.get("system_name")
    if not project_id or not system_name:
        return {"error": "project_id and system_name are required"}

    try:
        boundary_definition = args.get("boundary_definition")
        if boundary_definition and isinstance(boundary_definition, str):
            boundary_definition = json.loads(boundary_definition)

        baseline_controls = args.get("baseline_controls")
        if baseline_controls and isinstance(baseline_controls, str):
            baseline_controls = json.loads(baseline_controls)

        return register_system(
            project_id=project_id,
            system_name=system_name,
            ato_status=args.get("ato_status", "active"),
            boundary_definition=boundary_definition,
            baseline_controls=baseline_controls,
            classification=args.get("classification", "CUI"),
            impact_level=args.get("impact_level", "IL4"),
            db_path=str(DB_PATH),
        )
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in boundary_definition or baseline_controls: {e}"}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_assess_boundary_impact(args: dict) -> dict:
    """Assess a requirement's impact on ATO boundary."""
    assess = _import_tool("tools.requirements.boundary_analyzer", "assess_boundary_impact")
    if not assess:
        return {"error": "boundary_analyzer module not available", "status": "pending"}

    project_id = args.get("project_id")
    system_id = args.get("system_id")
    requirement_id = args.get("requirement_id")
    if not all([project_id, system_id, requirement_id]):
        return {"error": "project_id, system_id, and requirement_id are required"}

    try:
        return assess(
            project_id=project_id,
            system_id=system_id,
            requirement_id=requirement_id,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_generate_red_alternative(args: dict) -> dict:
    """Generate alternative COAs for RED-tier requirements."""
    generate_alternatives = _import_tool("tools.requirements.boundary_analyzer", "generate_alternatives")
    if not generate_alternatives:
        return {"error": "boundary_analyzer module not available", "status": "pending"}

    project_id = args.get("project_id")
    assessment_id = args.get("assessment_id")
    if not project_id or not assessment_id:
        return {"error": "project_id and assessment_id are required"}

    try:
        return generate_alternatives(
            project_id=project_id,
            assessment_id=assessment_id,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_add_vendor(args: dict) -> dict:
    """Register a supply chain vendor."""
    add_vendor = _import_tool("tools.supply_chain.dependency_graph", "add_vendor")
    if not add_vendor:
        return {"error": "dependency_graph module not available", "status": "pending"}

    project_id = args.get("project_id")
    vendor_name = args.get("vendor_name")
    country_of_origin = args.get("country_of_origin")
    if not all([project_id, vendor_name, country_of_origin]):
        return {"error": "project_id, vendor_name, and country_of_origin are required"}

    try:
        return add_vendor(
            project_id=project_id,
            vendor_name=vendor_name,
            vendor_type=args.get("vendor_type", "software"),
            country_of_origin=country_of_origin,
            scrm_risk_tier=args.get("scrm_risk_tier", "medium"),
            section_889_status=args.get("section_889_status", "compliant"),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_build_dependency_graph(args: dict) -> dict:
    """Build or query the project dependency graph."""
    build_graph = _import_tool("tools.supply_chain.dependency_graph", "build_graph")
    if not build_graph:
        return {"error": "dependency_graph module not available", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return build_graph(project_id=project_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_propagate_impact(args: dict) -> dict:
    """Propagate impact through the dependency graph."""
    propagate_impact = _import_tool("tools.supply_chain.dependency_graph", "propagate_impact")
    if not propagate_impact:
        return {"error": "dependency_graph module not available", "status": "pending"}

    project_id = args.get("project_id")
    component = args.get("component")
    if not project_id or not component:
        return {"error": "project_id and component are required"}

    try:
        return propagate_impact(
            project_id=project_id,
            component=component,
            impact_type=args.get("impact_type", "vulnerability"),
            severity=args.get("severity", "high"),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_manage_isa(args: dict) -> dict:
    """ISA/MOU lifecycle management — list, create, check expiring, review due."""
    project_id = args.get("project_id")
    action = args.get("action")
    if not project_id or not action:
        return {"error": "project_id and action are required"}

    valid_actions = ["list", "create", "expiring", "review_due"]
    if action not in valid_actions:
        return {"error": f"Invalid action: {action}. Use: {', '.join(valid_actions)}"}

    try:
        if action == "list":
            list_isas = _import_tool("tools.supply_chain.isa_manager", "list_isas")
            if not list_isas:
                return {"error": "isa_manager module not available", "status": "pending"}
            return list_isas(project_id=project_id, db_path=str(DB_PATH))

        elif action == "create":
            create_isa = _import_tool("tools.supply_chain.isa_manager", "create_isa")
            if not create_isa:
                return {"error": "isa_manager module not available", "status": "pending"}
            source_system = args.get("source_system")
            target_system = args.get("target_system")
            if not source_system or not target_system:
                return {"error": "source_system and target_system are required for create"}
            return create_isa(
                project_id=project_id,
                source_system=source_system,
                target_system=target_system,
                data_types_shared=args.get("data_types_shared"),
                auth_date=args.get("auth_date"),
                expiry_date=args.get("expiry_date"),
                db_path=str(DB_PATH),
            )

        elif action == "expiring":
            get_expiring = _import_tool("tools.supply_chain.isa_manager", "get_expiring")
            if not get_expiring:
                return {"error": "isa_manager module not available", "status": "pending"}
            return get_expiring(
                project_id=project_id,
                days_ahead=args.get("days_ahead", 90),
                db_path=str(DB_PATH),
            )

        elif action == "review_due":
            get_review_due = _import_tool("tools.supply_chain.isa_manager", "get_review_due")
            if not get_review_due:
                return {"error": "isa_manager module not available", "status": "pending"}
            return get_review_due(project_id=project_id, db_path=str(DB_PATH))

    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_assess_scrm(args: dict) -> dict:
    """NIST 800-161 SCRM assessment — per vendor or aggregate project."""
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    aggregate = args.get("aggregate", False)
    vendor_id = args.get("vendor_id")

    try:
        if aggregate:
            assess_project = _import_tool("tools.supply_chain.scrm_assessor", "assess_project")
            if not assess_project:
                return {"error": "scrm_assessor module not available", "status": "pending"}
            return assess_project(project_id=project_id, db_path=str(DB_PATH))
        else:
            if not vendor_id:
                return {"error": "vendor_id is required when aggregate is false"}
            assess_vendor = _import_tool("tools.supply_chain.scrm_assessor", "assess_vendor")
            if not assess_vendor:
                return {"error": "scrm_assessor module not available", "status": "pending"}
            return assess_vendor(
                project_id=project_id,
                vendor_id=vendor_id,
                db_path=str(DB_PATH),
            )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_triage_cve(args: dict) -> dict:
    """CVE triage with blast radius analysis."""
    triage_cve = _import_tool("tools.supply_chain.cve_triager", "triage_cve")
    if not triage_cve:
        return {"error": "cve_triager module not available", "status": "pending"}

    project_id = args.get("project_id")
    cve_id = args.get("cve_id")
    component = args.get("component")
    severity = args.get("severity")
    if not all([project_id, cve_id, component, severity]):
        return {"error": "project_id, cve_id, component, and severity are required"}

    try:
        return triage_cve(
            project_id=project_id,
            cve_id=cve_id,
            component=component,
            cvss_score=args.get("cvss_score"),
            severity=severity,
            description=args.get("description"),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the Supply Chain MCP server."""
    server = MCPServer(name="icdev-supply-chain", version="1.0.0")

    server.register_tool(
        name="register_ato_system",
        description="Register an existing ATO boundary system for boundary impact analysis",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "system_name": {"type": "string", "description": "Name of the ATO system boundary"},
                "ato_status": {"type": "string", "default": "active", "enum": ["active", "conditional", "expired", "pending"], "description": "Current ATO status"},
                "boundary_definition": {"type": "string", "description": "JSON string defining the boundary (components, networks, data flows)"},
                "baseline_controls": {"type": "string", "description": "JSON string of baseline NIST 800-53 controls implemented"},
                "classification": {"type": "string", "default": "CUI", "description": "Classification marking"},
                "impact_level": {"type": "string", "default": "IL4", "enum": ["IL2", "IL4", "IL5", "IL6"], "description": "DoD Impact Level"},
            },
            "required": ["project_id", "system_name"],
        },
        handler=handle_register_ato_system,
    )

    server.register_tool(
        name="assess_boundary_impact",
        description="Assess a requirement's impact on an ATO boundary — returns GREEN/YELLOW/ORANGE/RED tier classification",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "system_id": {"type": "string", "description": "ATO system boundary ID"},
                "requirement_id": {"type": "string", "description": "Requirement ID to assess"},
            },
            "required": ["project_id", "system_id", "requirement_id"],
        },
        handler=handle_assess_boundary_impact,
    )

    server.register_tool(
        name="generate_red_alternative",
        description="Generate alternative COAs (Courses of Action) for RED-tier requirements that would invalidate ATO",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "assessment_id": {"type": "string", "description": "Boundary impact assessment ID (from assess_boundary_impact)"},
            },
            "required": ["project_id", "assessment_id"],
        },
        handler=handle_generate_red_alternative,
    )

    server.register_tool(
        name="add_vendor",
        description="Register a supply chain vendor with SCRM risk tier and Section 889 compliance status",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "vendor_name": {"type": "string", "description": "Vendor or supplier name"},
                "vendor_type": {"type": "string", "default": "software", "enum": ["software", "hardware", "cloud_service", "integrator", "open_source"], "description": "Type of vendor"},
                "country_of_origin": {"type": "string", "description": "ISO 3166-1 alpha-2 country code (e.g., US, GB, DE)"},
                "scrm_risk_tier": {"type": "string", "default": "medium", "enum": ["low", "medium", "high", "critical"], "description": "Initial SCRM risk tier"},
                "section_889_status": {"type": "string", "default": "compliant", "enum": ["compliant", "non_compliant", "under_review", "exempt"], "description": "Section 889 compliance status"},
            },
            "required": ["project_id", "vendor_name", "country_of_origin"],
        },
        handler=handle_add_vendor,
    )

    server.register_tool(
        name="build_dependency_graph",
        description="Build or query the project supply chain dependency graph — vendors, components, and their relationships",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
            },
            "required": ["project_id"],
        },
        handler=handle_build_dependency_graph,
    )

    server.register_tool(
        name="propagate_impact",
        description="Propagate a vulnerability or supply chain event through the dependency graph to determine blast radius",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "component": {"type": "string", "description": "Affected component name"},
                "impact_type": {"type": "string", "default": "vulnerability", "enum": ["vulnerability", "supply_disruption", "license_change", "eol_notice", "vendor_breach"], "description": "Type of supply chain impact"},
                "severity": {"type": "string", "default": "high", "enum": ["critical", "high", "medium", "low"], "description": "Impact severity"},
            },
            "required": ["project_id", "component"],
        },
        handler=handle_propagate_impact,
    )

    server.register_tool(
        name="manage_isa",
        description="ISA/MOU lifecycle management — list, create, check expiring, or find review-due agreements",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "action": {"type": "string", "enum": ["list", "create", "expiring", "review_due"], "description": "ISA management action"},
                "source_system": {"type": "string", "description": "Source system name (required for create)"},
                "target_system": {"type": "string", "description": "Target system name (required for create)"},
                "data_types_shared": {"type": "string", "description": "Comma-separated data types shared between systems"},
                "auth_date": {"type": "string", "description": "Authorization date (YYYY-MM-DD)"},
                "expiry_date": {"type": "string", "description": "Expiry date (YYYY-MM-DD)"},
                "days_ahead": {"type": "number", "default": 90, "description": "Look-ahead days for expiring check"},
            },
            "required": ["project_id", "action"],
        },
        handler=handle_manage_isa,
    )

    server.register_tool(
        name="assess_scrm",
        description="Run NIST 800-161 Supply Chain Risk Management assessment — per vendor or aggregate project-level",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "vendor_id": {"type": "string", "description": "Vendor ID (required when aggregate is false)"},
                "aggregate": {"type": "boolean", "default": False, "description": "If true, assess all vendors for the project"},
            },
            "required": ["project_id"],
        },
        handler=handle_assess_scrm,
    )

    server.register_tool(
        name="triage_cve",
        description="Triage a CVE with blast radius analysis through the dependency graph — assigns SLA and escalation timeline",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "cve_id": {"type": "string", "description": "CVE identifier (e.g., CVE-2024-12345)"},
                "component": {"type": "string", "description": "Affected component name"},
                "cvss_score": {"type": "number", "description": "CVSS v3.1 base score (0.0-10.0)"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"], "description": "CVE severity level"},
                "description": {"type": "string", "description": "CVE description for context"},
            },
            "required": ["project_id", "cve_id", "component", "severity"],
        },
        handler=handle_triage_cve,
    )

    return server


def main():
    """Run the Supply Chain MCP server."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
# CUI // SP-CTI
