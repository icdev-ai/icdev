#!/usr/bin/env python3
"""Core MCP server exposing project management tools for the ICDEV system.

Tools:
    project_create  - Create a new project (UUID, directory, DB record, audit)
    project_list    - List projects from the database
    project_status  - Detailed project status with compliance/security/deployment
    task_dispatch   - Create an A2A task record
    agent_status    - Query agent statuses

Resources:
    projects://list         - List of all projects
    projects://{id}/status  - Project status by ID

Runs as an MCP server over stdio with Content-Length framing.
"""

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — the project root is 3 levels up from this file.
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
PROJECTS_DIR = BASE_DIR / "projects"

# Ensure we can import the base server from the same package
sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402

# Try to import the audit logger; fall back to direct DB writes if unavailable.
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    audit_log_event = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Open a connection to the ICDEV database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _audit(event_type: str, actor: str, action: str, project_id: str = None, details: dict = None):
    """Write an audit trail entry, using the audit_logger module if available."""
    if audit_log_event is not None:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                project_id=project_id,
                details=details,
                db_path=DB_PATH,
            )
            return
        except Exception:
            pass  # Fall through to direct write

    # Direct write fallback
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, 'CUI')""",
            (project_id, event_type, actor, action, json.dumps(details) if details else None),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Audit failures must not crash the server


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_project_create(args: dict) -> dict:
    """Create a new project: generate UUID, create directory, insert DB record, log audit."""
    name = args.get("name")
    if not name:
        raise ValueError("'name' is required")

    description = args.get("description", "")
    project_type = args.get("type", "webapp")
    classification = args.get("classification", "CUI")
    tech_stack = args.get("tech_stack", {})

    project_id = str(uuid.uuid4())
    dir_name = name.lower().replace(" ", "-").replace("/", "-").replace("\\", "-")
    project_dir = PROJECTS_DIR / dir_name

    # Create project directory
    project_dir.mkdir(parents=True, exist_ok=True)

    # Parse tech_stack
    if isinstance(tech_stack, str):
        try:
            tech_stack = json.loads(tech_stack)
        except json.JSONDecodeError:
            tech_stack = {}

    backend = tech_stack.get("backend", "")
    frontend = tech_stack.get("frontend", "")
    database = tech_stack.get("database", "")

    # Insert into database
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO projects
               (id, name, description, type, classification, status,
                tech_stack_backend, tech_stack_frontend, tech_stack_database,
                directory_path, created_by)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 'icdev-core-mcp')""",
            (
                project_id,
                name,
                description,
                project_type,
                classification,
                backend,
                frontend,
                database,
                str(project_dir),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Audit trail
    _audit(
        event_type="project_created",
        actor="icdev-core-mcp",
        action=f"Created project '{name}' ({project_type})",
        project_id=project_id,
        details={
            "name": name,
            "type": project_type,
            "classification": classification,
            "directory": str(project_dir),
        },
    )

    return {
        "project_id": project_id,
        "name": name,
        "type": project_type,
        "classification": classification,
        "directory": str(project_dir),
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
    }


def handle_project_list(args: dict) -> dict:
    """List all projects, optionally filtered by status."""
    status_filter = args.get("status_filter")

    conn = _get_db()
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()

        projects = []
        for row in rows:
            projects.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "type": row["type"],
                "classification": row["classification"],
                "status": row["status"],
                "tech_stack": {
                    "backend": row["tech_stack_backend"],
                    "frontend": row["tech_stack_frontend"],
                    "database": row["tech_stack_database"],
                },
                "directory_path": row["directory_path"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
    finally:
        conn.close()

    return {"projects": projects, "total": len(projects)}


def handle_project_status(args: dict) -> dict:
    """Get detailed project status including compliance, security, and deployment info."""
    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    conn = _get_db()
    try:
        # Core project info
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise ValueError(f"Project not found: {project_id}")

        project_info = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "type": row["type"],
            "classification": row["classification"],
            "status": row["status"],
            "tech_stack": {
                "backend": row["tech_stack_backend"],
                "frontend": row["tech_stack_frontend"],
                "database": row["tech_stack_database"],
            },
            "directory_path": row["directory_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

        # Compliance status
        ssp = conn.execute(
            "SELECT version, status, approved_by, approved_at FROM ssp_documents WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        poam_counts = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM poam_items WHERE project_id = ? GROUP BY status",
            (project_id,),
        ).fetchall()

        stig_counts = conn.execute(
            "SELECT severity, status, COUNT(*) as cnt FROM stig_findings WHERE project_id = ? GROUP BY severity, status",
            (project_id,),
        ).fetchall()

        control_counts = conn.execute(
            "SELECT implementation_status, COUNT(*) as cnt FROM project_controls WHERE project_id = ? GROUP BY implementation_status",
            (project_id,),
        ).fetchall()

        compliance_status = {
            "ssp": {
                "version": ssp["version"] if ssp else None,
                "status": ssp["status"] if ssp else "not_generated",
                "approved_by": ssp["approved_by"] if ssp else None,
                "approved_at": ssp["approved_at"] if ssp else None,
            },
            "poam": {k: v for k, v in [(r["status"], r["cnt"]) for r in poam_counts]} if poam_counts else {"open": 0},
            "stig_findings": {},
            "controls": {r["implementation_status"]: r["cnt"] for r in control_counts} if control_counts else {},
        }

        # Organize STIG findings by severity and status
        for finding in stig_counts:
            sev = finding["severity"]
            stat = finding["status"]
            if sev not in compliance_status["stig_findings"]:
                compliance_status["stig_findings"][sev] = {}
            compliance_status["stig_findings"][sev][stat] = finding["cnt"]

        # Security status (from audit trail scan events and SBOM)
        last_scan = conn.execute(
            "SELECT created_at, details FROM audit_trail WHERE project_id = ? AND event_type = 'security_scan' ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        open_vulns = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE project_id = ? AND event_type = 'vulnerability_found' AND created_at > COALESCE((SELECT MAX(created_at) FROM audit_trail WHERE project_id = ? AND event_type = 'vulnerability_resolved'), '1970-01-01')",
            (project_id, project_id),
        ).fetchone()

        latest_sbom = conn.execute(
            "SELECT version, component_count, vulnerability_count, generated_at FROM sbom_records WHERE project_id = ? ORDER BY generated_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        security_status = {
            "last_scan_date": last_scan["created_at"] if last_scan else None,
            "open_vulnerabilities": open_vulns["cnt"] if open_vulns else 0,
            "sbom": {
                "version": latest_sbom["version"] if latest_sbom else None,
                "component_count": latest_sbom["component_count"] if latest_sbom else 0,
                "vulnerability_count": latest_sbom["vulnerability_count"] if latest_sbom else 0,
                "generated_at": latest_sbom["generated_at"] if latest_sbom else None,
            },
        }

        # Deployment status (latest per environment)
        deployments_rows = conn.execute(
            """SELECT environment, version, status, deployed_by, health_check_passed, created_at, completed_at
               FROM deployments WHERE project_id = ?
               AND id IN (SELECT MAX(id) FROM deployments WHERE project_id = ? GROUP BY environment)
               ORDER BY environment""",
            (project_id, project_id),
        ).fetchall()

        deployment_status = {}
        for dep in deployments_rows:
            deployment_status[dep["environment"]] = {
                "version": dep["version"],
                "status": dep["status"],
                "deployed_by": dep["deployed_by"],
                "health_check_passed": bool(dep["health_check_passed"]) if dep["health_check_passed"] is not None else None,
                "deployed_at": dep["created_at"],
                "completed_at": dep["completed_at"],
            }

        # Test status (from metric snapshots and audit trail)
        last_test = conn.execute(
            "SELECT created_at, details FROM audit_trail WHERE project_id = ? AND event_type IN ('test_executed', 'test_passed', 'test_failed') ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        test_pass_rate = conn.execute(
            "SELECT metric_value FROM metric_snapshots WHERE project_id = ? AND metric_name = 'test_pass_rate' ORDER BY collected_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        test_coverage = conn.execute(
            "SELECT metric_value FROM metric_snapshots WHERE project_id = ? AND metric_name = 'test_coverage' ORDER BY collected_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        test_status = {
            "last_run": last_test["created_at"] if last_test else None,
            "pass_rate": test_pass_rate["metric_value"] if test_pass_rate else None,
            "coverage": test_coverage["metric_value"] if test_coverage else None,
        }

    finally:
        conn.close()

    return {
        "project": project_info,
        "compliance": compliance_status,
        "security": security_status,
        "deployments": deployment_status,
        "tests": test_status,
    }


def handle_task_dispatch(args: dict) -> dict:
    """Create an A2A task record."""
    project_id = args.get("project_id")
    target_agent_id = args.get("target_agent_id")
    skill_id = args.get("skill_id")
    if not skill_id:
        raise ValueError("'skill_id' is required")

    input_data = args.get("input_data", {})
    priority = args.get("priority", 5)

    task_id = str(uuid.uuid4())

    if isinstance(input_data, dict):
        input_data_str = json.dumps(input_data)
    else:
        input_data_str = str(input_data)

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO a2a_tasks
               (id, project_id, source_agent_id, target_agent_id, skill_id,
                status, input_data, priority)
               VALUES (?, ?, 'icdev-core-mcp', ?, ?, 'submitted', ?, ?)""",
            (task_id, project_id, target_agent_id, skill_id, input_data_str, priority),
        )
        conn.commit()
    finally:
        conn.close()

    # Audit trail
    _audit(
        event_type="agent_task_submitted",
        actor="icdev-core-mcp",
        action=f"Dispatched task to agent '{target_agent_id}' with skill '{skill_id}'",
        project_id=project_id,
        details={
            "task_id": task_id,
            "target_agent_id": target_agent_id,
            "skill_id": skill_id,
            "priority": priority,
        },
    )

    # Actually dispatch to the target agent via A2A protocol
    dispatch_status = "submitted"
    dispatch_error = None
    try:
        from tools.a2a.agent_registry import get_agent
        from tools.a2a.agent_client import A2AAgentClient

        agent_info = get_agent(target_agent_id)
        if agent_info and agent_info.get("url"):
            client = A2AAgentClient(verify_ssl=False, timeout=30)
            result = client.send_task(
                agent_url=agent_info["url"],
                skill_id=skill_id,
                input_data=input_data if isinstance(input_data, dict) else {"data": input_data},
                project_id=project_id,
                task_id=task_id,
            )
            dispatch_status = result.get("status", "submitted")
            # Update DB with actual status from agent
            conn2 = _get_db()
            try:
                conn2.execute(
                    "UPDATE a2a_tasks SET status = ? WHERE id = ?",
                    (dispatch_status, task_id),
                )
                conn2.commit()
            finally:
                conn2.close()
        else:
            dispatch_error = f"Agent '{target_agent_id}' not found or has no URL"
    except Exception as e:
        dispatch_error = str(e)

    if dispatch_error:
        _audit(
            event_type="agent_task_failed",
            actor="icdev-core-mcp",
            action=f"Failed to dispatch to agent '{target_agent_id}': {dispatch_error}",
            project_id=project_id,
            details={"task_id": task_id, "error": dispatch_error},
        )

    return {
        "task_id": task_id,
        "project_id": project_id,
        "target_agent_id": target_agent_id,
        "skill_id": skill_id,
        "status": dispatch_status,
        "priority": priority,
        "dispatch_error": dispatch_error,
        "created_at": datetime.utcnow().isoformat(),
    }


def handle_agent_status(args: dict) -> dict:
    """Get agent statuses, optionally filtered by agent_id."""
    agent_id = args.get("agent_id")

    conn = _get_db()
    try:
        if agent_id:
            rows = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()

        agents = []
        for row in rows:
            capabilities = row["capabilities"]
            if capabilities:
                try:
                    capabilities = json.loads(capabilities)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Count active tasks for this agent
            task_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM a2a_tasks WHERE target_agent_id = ? AND status IN ('submitted', 'working')",
                (row["id"],),
            ).fetchone()

            agents.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "url": row["url"],
                "status": row["status"],
                "capabilities": capabilities,
                "last_heartbeat": row["last_heartbeat"],
                "active_tasks": task_count["cnt"] if task_count else 0,
                "created_at": row["created_at"],
            })
    finally:
        conn.close()

    return {"agents": agents, "total": len(agents)}


# ---------------------------------------------------------------------------
# Resource handlers
# ---------------------------------------------------------------------------

def handle_resource_projects_list(uri: str) -> dict:
    """Resource handler for projects://list."""
    return handle_project_list({})


def handle_resource_project_status(uri: str) -> dict:
    """Resource handler for projects://{id}/status."""
    # Extract project ID from URI: projects://<id>/status
    parts = uri.replace("projects://", "").split("/")
    project_id = parts[0] if parts else ""
    return handle_project_status({"project_id": project_id})


# ---------------------------------------------------------------------------
# Server setup & main
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the core MCP server with all tools and resources."""
    server = MCPServer(name="icdev-core", version="1.0.0")

    # --- Tools ---

    server.register_tool(
        name="project_create",
        description="Create a new ICDEV project with UUID, directory scaffolding, database record, and audit trail entry.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Project name (human-readable)",
                },
                "description": {
                    "type": "string",
                    "description": "Project description",
                },
                "type": {
                    "type": "string",
                    "description": "Project type",
                    "enum": ["webapp", "microservice", "api", "cli", "data_pipeline", "iac"],
                    "default": "webapp",
                },
                "classification": {
                    "type": "string",
                    "description": "Data classification level",
                    "enum": ["CUI", "FOUO", "Public"],
                    "default": "CUI",
                },
                "tech_stack": {
                    "type": "object",
                    "description": "Technology stack (backend, frontend, database)",
                    "properties": {
                        "backend": {"type": "string"},
                        "frontend": {"type": "string"},
                        "database": {"type": "string"},
                    },
                },
            },
            "required": ["name"],
        },
        handler=handle_project_create,
    )

    server.register_tool(
        name="project_list",
        description="List all ICDEV projects from the database, optionally filtered by status.",
        input_schema={
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "Filter by project status (active, archived, suspended)",
                    "enum": ["active", "archived", "suspended"],
                },
            },
        },
        handler=handle_project_list,
    )

    server.register_tool(
        name="project_status",
        description="Get detailed project status including compliance (SSP, POA&M, STIG), security (scans, vulnerabilities, SBOM), deployment (per environment), and test (pass rate, coverage) summaries.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "UUID of the project",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_project_status,
    )

    server.register_tool(
        name="task_dispatch",
        description="Create an A2A (Agent-to-Agent) task record to dispatch work to another agent.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "UUID of the project (optional)",
                },
                "target_agent_id": {
                    "type": "string",
                    "description": "ID of the target agent to receive the task",
                },
                "skill_id": {
                    "type": "string",
                    "description": "Skill identifier the target agent should execute",
                },
                "input_data": {
                    "type": "object",
                    "description": "Input data for the task",
                },
                "priority": {
                    "type": "integer",
                    "description": "Task priority (1=highest, 10=lowest)",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 5,
                },
            },
            "required": ["skill_id"],
        },
        handler=handle_task_dispatch,
    )

    server.register_tool(
        name="agent_status",
        description="Get the status of registered A2A agents, including active task counts.",
        input_schema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Specific agent ID to query (omit for all agents)",
                },
            },
        },
        handler=handle_agent_status,
    )

    # --- Resources ---

    server.register_resource(
        uri="projects://list",
        name="Project List",
        description="List of all ICDEV projects",
        handler=handle_resource_projects_list,
    )

    server.register_resource(
        uri="projects://{id}/status",
        name="Project Status",
        description="Detailed status for a specific project",
        handler=handle_resource_project_status,
    )

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
