#!/usr/bin/env python3
"""Maintenance MCP Server — exposes dependency scanning, vulnerability checking,
maintenance auditing, and remediation tools via MCP protocol.

Tools:
    scan_dependencies     - Inventory all project dependencies with version staleness
    check_vulnerabilities - Check deps against advisory databases
    run_maintenance_audit - Full maintenance audit with scoring and SLA tracking
    remediate            - Auto-implement dependency fixes
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
# Lazy tool imports — tools may still be under construction
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

def handle_scan_dependencies(args: dict) -> dict:
    """Inventory all project dependencies with version staleness."""
    scan = _import_tool("tools.maintenance.dependency_scanner", "scan_dependencies")
    if not scan:
        return {"error": "dependency_scanner module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    language = args.get("language")
    offline = args.get("offline", False)

    try:
        result = scan(project_id, language=language, offline=offline, db_path=str(DB_PATH))
        return result
    except Exception as exc:
        return {"error": f"Dependency scan failed: {exc}", "project_id": project_id}


def handle_check_vulnerabilities(args: dict) -> dict:
    """Check dependencies against advisory databases for known CVEs."""
    check = _import_tool("tools.maintenance.vulnerability_checker", "check_vulnerabilities")
    if not check:
        return {"error": "vulnerability_checker module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    try:
        result = check(project_id, db_path=str(DB_PATH))
        return result
    except Exception as exc:
        return {"error": f"Vulnerability check failed: {exc}", "project_id": project_id}


def handle_run_maintenance_audit(args: dict) -> dict:
    """Run full maintenance audit with scoring and SLA tracking."""
    audit = _import_tool("tools.maintenance.maintenance_auditor", "run_maintenance_audit")
    if not audit:
        return {"error": "maintenance_auditor module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    offline = args.get("offline", False)

    try:
        result = audit(project_id, offline=offline, db_path=str(DB_PATH))
        return result
    except Exception as exc:
        return {"error": f"Maintenance audit failed: {exc}", "project_id": project_id}


def handle_remediate(args: dict) -> dict:
    """Auto-implement dependency fixes and track remediation actions."""
    remediate = _import_tool("tools.maintenance.remediation_engine", "remediate")
    if not remediate:
        return {"error": "remediation_engine module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    vulnerability_id = args.get("vulnerability_id")
    auto = args.get("auto", False)
    dry_run = args.get("dry_run", False)

    try:
        result = remediate(
            project_id,
            vulnerability_id=vulnerability_id,
            auto=auto,
            dry_run=dry_run,
            db_path=str(DB_PATH),
        )
        return result
    except Exception as exc:
        return {"error": f"Remediation failed: {exc}", "project_id": project_id}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the maintenance MCP server with all tools registered."""
    server = MCPServer(name="icdev-maintenance", version="1.0.0")

    server.register_tool(
        name="scan_dependencies",
        description=(
            "Inventory all project dependencies across detected languages (Python, Node.js, "
            "Rust, Go, Java, .NET). Reports current version, latest version, and days stale. "
            "Use --offline for air-gapped environments where registry access is unavailable."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "UUID of the project to scan",
                },
                "language": {
                    "type": "string",
                    "description": "Specific language to scan (optional, auto-detects all if omitted)",
                    "enum": ["python", "nodejs", "rust", "go", "java", "dotnet"],
                },
                "offline": {
                    "type": "boolean",
                    "description": "Run in offline/air-gapped mode (skip registry checks)",
                    "default": False,
                },
            },
            "required": ["project_id"],
        },
        handler=handle_scan_dependencies,
    )

    server.register_tool(
        name="check_vulnerabilities",
        description=(
            "Check project dependencies against advisory databases (NVD, GitHub Advisories, "
            "pip-audit, npm audit, cargo-audit). Maps findings to SLA deadlines: "
            "critical=48hr, high=7d, medium=30d, low=90d."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "UUID of the project to check",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_check_vulnerabilities,
    )

    server.register_tool(
        name="run_maintenance_audit",
        description=(
            "Run a full maintenance audit: dependency inventory, vulnerability check, "
            "maintenance score computation (0-100), SLA compliance tracking, trend analysis, "
            "and CUI-marked report generation. Gate: score >= 50 required for deployment."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "UUID of the project to audit",
                },
                "offline": {
                    "type": "boolean",
                    "description": "Run in offline/air-gapped mode (skip registry checks)",
                    "default": False,
                },
            },
            "required": ["project_id"],
        },
        handler=handle_run_maintenance_audit,
    )

    server.register_tool(
        name="remediate",
        description=(
            "Auto-implement dependency fixes: update dependency files, create remediation "
            "branches, run tests to verify fixes. Medium/low severity auto-fixed; "
            "critical/high require manual approval. Use --dry-run to preview changes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "UUID of the project to remediate",
                },
                "vulnerability_id": {
                    "type": "integer",
                    "description": "Specific vulnerability ID to remediate (optional, remediates all eligible if omitted)",
                },
                "auto": {
                    "type": "boolean",
                    "description": "Enable auto-remediation for medium/low severity vulnerabilities",
                    "default": False,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview remediation actions without applying changes",
                    "default": False,
                },
            },
            "required": ["project_id"],
        },
        handler=handle_remediate,
    )

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
