#!/usr/bin/env python3
# CUI // SP-CTI
"""Integration MCP server for external system sync, DOORS NG export, approvals, and traceability.

Tools:
    configure_jira      - Configure Jira integration for a project
    sync_jira           - Push/pull bidirectional Jira sync
    configure_servicenow - Configure ServiceNow integration for a project
    sync_servicenow     - Push/pull bidirectional ServiceNow sync
    configure_gitlab    - Configure GitLab integration for a project
    sync_gitlab         - Push/pull bidirectional GitLab sync
    export_reqif        - Export requirements as ReqIF 1.2 for DOORS NG
    submit_approval     - Submit a requirements package for approval
    review_approval     - Record a reviewer decision on an approval
    build_traceability  - Build full Requirements Traceability Matrix (RTM)

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
# Tool handlers — Jira
# ---------------------------------------------------------------------------

def handle_configure_jira(args: dict) -> dict:
    """Configure Jira integration for a project."""
    configure = _import_tool("tools.integration.jira_connector", "configure")
    if not configure:
        return {"error": "jira_connector module not available", "status": "pending"}

    project_id = args.get("project_id")
    instance_url = args.get("instance_url")
    project_key = args.get("project_key")
    auth_secret_ref = args.get("auth_secret_ref")
    if not all([project_id, instance_url, project_key, auth_secret_ref]):
        return {"error": "project_id, instance_url, project_key, and auth_secret_ref are required"}

    try:
        return configure(
            project_id=project_id,
            instance_url=instance_url,
            project_key=project_key,
            auth_secret_ref=auth_secret_ref,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_sync_jira(args: dict) -> dict:
    """Push or pull Jira sync."""
    direction = args.get("direction", "push")
    if direction == "push":
        fn = _import_tool("tools.integration.jira_connector", "push_to_jira")
    elif direction == "pull":
        fn = _import_tool("tools.integration.jira_connector", "pull_from_jira")
    else:
        return {"error": f"Invalid direction: {direction}. Use 'push' or 'pull'"}

    if not fn:
        return {"error": "jira_connector module not available", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return fn(
            project_id=project_id,
            session_id=args.get("session_id"),
            dry_run=args.get("dry_run", False),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Tool handlers — ServiceNow
# ---------------------------------------------------------------------------

def handle_configure_servicenow(args: dict) -> dict:
    """Configure ServiceNow integration for a project."""
    configure = _import_tool("tools.integration.servicenow_connector", "configure")
    if not configure:
        return {"error": "servicenow_connector module not available", "status": "pending"}

    project_id = args.get("project_id")
    instance_url = args.get("instance_url")
    table_name = args.get("table_name", "rm_story")
    auth_secret_ref = args.get("auth_secret_ref")
    if not all([project_id, instance_url, auth_secret_ref]):
        return {"error": "project_id, instance_url, and auth_secret_ref are required"}

    try:
        return configure(
            project_id=project_id,
            instance_url=instance_url,
            table_name=table_name,
            auth_secret_ref=auth_secret_ref,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_sync_servicenow(args: dict) -> dict:
    """Push or pull ServiceNow sync."""
    direction = args.get("direction", "push")
    if direction == "push":
        fn = _import_tool("tools.integration.servicenow_connector", "push_to_servicenow")
    elif direction == "pull":
        fn = _import_tool("tools.integration.servicenow_connector", "pull_from_servicenow")
    else:
        return {"error": f"Invalid direction: {direction}. Use 'push' or 'pull'"}

    if not fn:
        return {"error": "servicenow_connector module not available", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return fn(
            project_id=project_id,
            session_id=args.get("session_id"),
            dry_run=args.get("dry_run", False),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Tool handlers — GitLab
# ---------------------------------------------------------------------------

def handle_configure_gitlab(args: dict) -> dict:
    """Configure GitLab integration for a project."""
    configure = _import_tool("tools.integration.gitlab_connector", "configure")
    if not configure:
        return {"error": "gitlab_connector module not available", "status": "pending"}

    project_id = args.get("project_id")
    instance_url = args.get("instance_url")
    gitlab_project_id = args.get("gitlab_project_id")
    auth_secret_ref = args.get("auth_secret_ref")
    if not all([project_id, instance_url, gitlab_project_id, auth_secret_ref]):
        return {"error": "project_id, instance_url, gitlab_project_id, and auth_secret_ref are required"}

    try:
        return configure(
            project_id=project_id,
            instance_url=instance_url,
            gitlab_project_id=gitlab_project_id,
            auth_secret_ref=auth_secret_ref,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_sync_gitlab(args: dict) -> dict:
    """Push or pull GitLab sync."""
    direction = args.get("direction", "push")
    if direction == "push":
        fn = _import_tool("tools.integration.gitlab_connector", "push_to_gitlab")
    elif direction == "pull":
        fn = _import_tool("tools.integration.gitlab_connector", "pull_from_gitlab")
    else:
        return {"error": f"Invalid direction: {direction}. Use 'push' or 'pull'"}

    if not fn:
        return {"error": "gitlab_connector module not available", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return fn(
            project_id=project_id,
            session_id=args.get("session_id"),
            dry_run=args.get("dry_run", False),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Tool handlers — DOORS NG ReqIF export
# ---------------------------------------------------------------------------

def handle_export_reqif(args: dict) -> dict:
    """Export requirements as ReqIF 1.2 for DOORS NG import."""
    export_reqif = _import_tool("tools.integration.doors_exporter", "export_reqif")
    if not export_reqif:
        return {"error": "doors_exporter module not available", "status": "pending"}

    session_id = args.get("session_id")
    output_path = args.get("output_path")
    if not session_id or not output_path:
        return {"error": "session_id and output_path are required"}

    try:
        return export_reqif(
            session_id=session_id,
            output_path=output_path,
            include_trace=args.get("include_trace", True),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Tool handlers — Approval workflow
# ---------------------------------------------------------------------------

def handle_submit_approval(args: dict) -> dict:
    """Submit a requirements package for approval."""
    submit_for_approval = _import_tool("tools.integration.approval_manager", "submit_for_approval")
    if not submit_for_approval:
        return {"error": "approval_manager module not available", "status": "pending"}

    session_id = args.get("session_id")
    approval_type = args.get("approval_type")
    submitted_by = args.get("submitted_by")
    if not all([session_id, approval_type, submitted_by]):
        return {"error": "session_id, approval_type, and submitted_by are required"}

    valid_types = ["requirements_package", "coa_selection", "boundary_acceptance", "deployment_gate"]
    if approval_type not in valid_types:
        return {"error": f"Invalid approval_type: {approval_type}. Must be one of {valid_types}"}

    reviewers = args.get("reviewers")
    if isinstance(reviewers, str):
        try:
            reviewers = json.loads(reviewers)
        except (json.JSONDecodeError, TypeError):
            reviewers = [reviewers]

    try:
        return submit_for_approval(
            session_id=session_id,
            approval_type=approval_type,
            submitted_by=submitted_by,
            reviewers=reviewers,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_review_approval(args: dict) -> dict:
    """Record a reviewer decision on an approval."""
    review_approval = _import_tool("tools.integration.approval_manager", "review_approval")
    if not review_approval:
        return {"error": "approval_manager module not available", "status": "pending"}

    approval_id = args.get("approval_id")
    reviewer = args.get("reviewer")
    decision = args.get("decision")
    rationale = args.get("rationale")
    if not all([approval_id, reviewer, decision, rationale]):
        return {"error": "approval_id, reviewer, decision, and rationale are required"}

    valid_decisions = ["approved", "rejected", "conditional"]
    if decision not in valid_decisions:
        return {"error": f"Invalid decision: {decision}. Must be one of {valid_decisions}"}

    try:
        return review_approval(
            approval_id=approval_id,
            reviewer=reviewer,
            decision=decision,
            rationale=rationale,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Tool handlers — Traceability
# ---------------------------------------------------------------------------

def handle_build_traceability(args: dict) -> dict:
    """Build full Requirements Traceability Matrix (RTM)."""
    build_rtm = _import_tool("tools.requirements.traceability_builder", "build_rtm")
    if not build_rtm:
        return {"error": "traceability_builder module not available", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        return build_rtm(
            project_id=project_id,
            session_id=args.get("session_id"),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the Integration MCP server."""
    server = MCPServer(name="icdev-integration", version="1.0.0")

    # -- Jira --
    server.register_tool(
        name="configure_jira",
        description="Configure Jira integration for bidirectional requirement sync",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "instance_url": {"type": "string", "description": "Jira instance URL (e.g., https://org.atlassian.net)"},
                "project_key": {"type": "string", "description": "Jira project key (e.g., PROJ)"},
                "auth_secret_ref": {"type": "string", "description": "AWS Secrets Manager reference for Jira credentials"},
            },
            "required": ["project_id", "instance_url", "project_key", "auth_secret_ref"],
        },
        handler=handle_configure_jira,
    )

    server.register_tool(
        name="sync_jira",
        description="Push decomposed SAFe items to Jira or pull status updates from Jira",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "direction": {"type": "string", "default": "push", "enum": ["push", "pull"], "description": "Sync direction: push to Jira or pull from Jira"},
                "session_id": {"type": "string", "description": "RICOAS session ID to scope the sync"},
                "dry_run": {"type": "boolean", "default": False, "description": "Preview changes without applying"},
            },
            "required": ["project_id"],
        },
        handler=handle_sync_jira,
    )

    # -- ServiceNow --
    server.register_tool(
        name="configure_servicenow",
        description="Configure ServiceNow integration for bidirectional requirement sync",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "instance_url": {"type": "string", "description": "ServiceNow instance URL (e.g., https://org.service-now.com)"},
                "table_name": {"type": "string", "default": "rm_story", "description": "ServiceNow table name for requirements"},
                "auth_secret_ref": {"type": "string", "description": "AWS Secrets Manager reference for ServiceNow credentials"},
            },
            "required": ["project_id", "instance_url", "auth_secret_ref"],
        },
        handler=handle_configure_servicenow,
    )

    server.register_tool(
        name="sync_servicenow",
        description="Push decomposed SAFe items to ServiceNow or pull status updates from ServiceNow",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "direction": {"type": "string", "default": "push", "enum": ["push", "pull"], "description": "Sync direction: push to ServiceNow or pull from ServiceNow"},
                "session_id": {"type": "string", "description": "RICOAS session ID to scope the sync"},
                "dry_run": {"type": "boolean", "default": False, "description": "Preview changes without applying"},
            },
            "required": ["project_id"],
        },
        handler=handle_sync_servicenow,
    )

    # -- GitLab --
    server.register_tool(
        name="configure_gitlab",
        description="Configure GitLab integration for bidirectional requirement sync",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "instance_url": {"type": "string", "description": "GitLab instance URL (e.g., https://gitlab.example.com)"},
                "gitlab_project_id": {"type": "string", "description": "GitLab project ID (numeric or path)"},
                "auth_secret_ref": {"type": "string", "description": "AWS Secrets Manager reference for GitLab credentials"},
            },
            "required": ["project_id", "instance_url", "gitlab_project_id", "auth_secret_ref"],
        },
        handler=handle_configure_gitlab,
    )

    server.register_tool(
        name="sync_gitlab",
        description="Push decomposed SAFe items to GitLab or pull status updates from GitLab",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "direction": {"type": "string", "default": "push", "enum": ["push", "pull"], "description": "Sync direction: push to GitLab or pull from GitLab"},
                "session_id": {"type": "string", "description": "RICOAS session ID to scope the sync"},
                "dry_run": {"type": "boolean", "default": False, "description": "Preview changes without applying"},
            },
            "required": ["project_id"],
        },
        handler=handle_sync_gitlab,
    )

    # -- DOORS NG ReqIF Export --
    server.register_tool(
        name="export_reqif",
        description="Export requirements as ReqIF 1.2 XML for import into IBM DOORS NG",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "RICOAS session ID containing requirements to export"},
                "output_path": {"type": "string", "description": "File path for the generated ReqIF XML output"},
                "include_trace": {"type": "boolean", "default": True, "description": "Include traceability links in the export"},
            },
            "required": ["session_id", "output_path"],
        },
        handler=handle_export_reqif,
    )

    # -- Approval Workflow --
    server.register_tool(
        name="submit_approval",
        description="Submit a requirements package, COA selection, boundary acceptance, or deployment gate for approval",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "RICOAS session ID for the approval subject"},
                "approval_type": {
                    "type": "string",
                    "enum": ["requirements_package", "coa_selection", "boundary_acceptance", "deployment_gate"],
                    "description": "Type of approval workflow to initiate",
                },
                "submitted_by": {"type": "string", "description": "Identity of the submitter (e.g., agent name or user ID)"},
                "reviewers": {"type": "string", "description": "JSON string array of reviewer identities"},
            },
            "required": ["session_id", "approval_type", "submitted_by"],
        },
        handler=handle_submit_approval,
    )

    server.register_tool(
        name="review_approval",
        description="Record a reviewer decision (approved, rejected, conditional) on a pending approval",
        input_schema={
            "type": "object",
            "properties": {
                "approval_id": {"type": "string", "description": "ID of the approval to review"},
                "reviewer": {"type": "string", "description": "Identity of the reviewer"},
                "decision": {
                    "type": "string",
                    "enum": ["approved", "rejected", "conditional"],
                    "description": "Reviewer decision",
                },
                "rationale": {"type": "string", "description": "Explanation for the decision"},
            },
            "required": ["approval_id", "reviewer", "decision", "rationale"],
        },
        handler=handle_review_approval,
    )

    # -- Traceability --
    server.register_tool(
        name="build_traceability",
        description="Build a full Requirements Traceability Matrix linking requirement to SysML to code to test to NIST control",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "session_id": {"type": "string", "description": "Optional RICOAS session ID to scope the RTM"},
            },
            "required": ["project_id"],
        },
        handler=handle_build_traceability,
    )

    return server


def main():
    """Run the Integration MCP server."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
# CUI // SP-CTI
