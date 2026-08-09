#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Industry Research Engine MCP server exposing research tools.

Tools:
    research_create_session  — Create a new research session for a vertical
    research_run_stage       — Run a specific pipeline stage
    research_run_pipeline    — Run full 9-stage research pipeline
    research_get_status      — Get session/engine status
    research_list_sessions   — List all research sessions
    research_get_dossier     — Get a generated dossier
    research_review_dossier  — Submit HITL review for a dossier
    research_list_verticals  — List available industry verticals
    research_get_challenges  — Get scored challenges for a session
    research_get_forecasts   — Get AI-generated predictions for a session
    research_trigger_fitness — Trigger child app fitness from approved dossier

Runs as MCP server over stdio with Content-Length framing.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.mcp.base_server import MCPServer  # noqa: E402


def _import_tool(module_path, func_name):
    """Dynamically import a function (graceful fallback)."""
    try:
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


def _get_db():
    conn = get_connection()
    return conn


# =========================================================================
# TOOL HANDLERS
# =========================================================================
def handle_research_create_session(args: dict) -> dict:
    """Create a new research session for a vertical."""
    vertical = args.get("vertical")
    name = args.get("name")
    focus_areas = args.get("focus_areas", [])
    if not vertical:
        return {"error": "vertical is required"}
    func = _import_tool("tools.research.session_manager", "create_session")
    if func:
        return func(vertical_id=vertical, name=name, focus_areas=focus_areas, db_path=str(DB_PATH))
    return {"error": "session_manager not available"}


def handle_research_run_stage(args: dict) -> dict:
    """Run a specific pipeline stage for a session."""
    session_id = args.get("session_id")
    stage = args.get("stage")
    if not session_id or not stage:
        return {"error": "session_id and stage are required"}
    func = _import_tool("tools.research.research_engine", "run_stage")
    if func:
        return func(session_id=session_id, stage=stage, db_path=str(DB_PATH))
    return {"error": "research_engine not available"}


def handle_research_run_pipeline(args: dict) -> dict:
    """Run full 9-stage research pipeline for a session."""
    session_id = args.get("session_id")
    vertical = args.get("vertical")
    func = _import_tool("tools.research.research_engine", "run_pipeline")
    if func:
        return func(session_id=session_id, vertical=vertical, db_path=str(DB_PATH))
    return {"error": "research_engine not available"}


def handle_research_get_status(args: dict) -> dict:
    """Get research engine or session status."""
    session_id = args.get("session_id")
    func = _import_tool("tools.research.research_engine", "get_status")
    if func:
        return func(session_id=session_id, db_path=str(DB_PATH))
    return {"error": "research_engine not available"}


def handle_research_list_sessions(args: dict) -> dict:
    """List all research sessions with optional status filter."""
    status = args.get("status")
    func = _import_tool("tools.research.session_manager", "list_sessions")
    if func:
        return func(status=status, db_path=str(DB_PATH))
    return {"error": "session_manager not available"}


def handle_research_get_dossier(args: dict) -> dict:
    """Get a generated research dossier."""
    dossier_id = args.get("dossier_id")
    session_id = args.get("session_id")
    func = _import_tool("tools.research.dossier_generator", "get_dossier")
    if func:
        if dossier_id:
            return func(dossier_id=dossier_id, db_path=str(DB_PATH))
        elif session_id:
            return func(session_id=session_id, db_path=str(DB_PATH))
        return {"error": "dossier_id or session_id required"}
    return {"error": "dossier_generator not available"}


def handle_research_review_dossier(args: dict) -> dict:
    """Submit HITL review for a dossier."""
    dossier_id = args.get("dossier_id")
    reviewer = args.get("reviewer")
    decision = args.get("decision")
    notes = args.get("notes", "")
    if not dossier_id or not reviewer or not decision:
        return {"error": "dossier_id, reviewer, and decision are required"}
    func = _import_tool("tools.research.dossier_generator", "review_dossier")
    if func:
        return func(dossier_id=dossier_id, reviewer=reviewer, decision=decision, notes=notes, db_path=str(DB_PATH))
    return {"error": "dossier_generator not available"}


def handle_research_list_verticals(args: dict) -> dict:
    """List available industry verticals."""
    func = _import_tool("tools.research.vertical_loader", "list_verticals")
    if func:
        return func(db_path=str(DB_PATH))
    return {"error": "vertical_loader not available"}


def handle_research_get_challenges(args: dict) -> dict:
    """Get scored challenges for a session."""
    session_id = args.get("session_id")
    args.get("severity")
    limit = args.get("limit", 50)
    if not session_id:
        return {"error": "session_id is required"}
    func = _import_tool("tools.research.challenge_scorer", "get_top_challenges")
    if func:
        return func(session_id=session_id, limit=limit, db_path=str(DB_PATH))
    return {"error": "challenge_scorer not available"}


def handle_research_get_forecasts(args: dict) -> dict:
    """Get AI-generated predictions/forecasts for a session."""
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}
    limit = int(args.get("limit", 10))
    func = _import_tool("tools.research.forecast_generator", "get_forecasts")
    if func:
        return {"forecasts": func(session_id, db_path=str(DB_PATH), limit=limit)}
    return {"error": "forecast_generator not available"}


def handle_research_trigger_fitness(args: dict) -> dict:
    """Trigger child app fitness assessment from an approved dossier."""
    dossier_id = args.get("dossier_id")
    if not dossier_id:
        return {"error": "dossier_id is required"}

    # Check dossier is approved first
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM research_dossiers WHERE id = %s", (dossier_id,)).fetchone()
        if not row:
            return {"error": f"Dossier not found: {dossier_id}"}

        session_id = row["session_id"]

        # Get session
        session = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not session:
            return {"error": f"Session not found: {session_id}"}
        if session["status"] != "reviewed":
            return {
                "error": f"Session must be in 'reviewed' status (current: {session['status']}). "
                "Run research_review_dossier first."
            }

        # Update session to child_app_triggered
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE research_sessions SET status = %s, updated_at = %s WHERE id = %s",
            ("child_app_triggered", now, session_id),
        )
        conn.commit()

        return {
            "ok": True,
            "message": f"Fitness assessment triggered for session {session_id}",
            "session_id": session_id,
            "dossier_id": dossier_id,
            "vertical": session["vertical_id"],
            "opportunity_score": row["opportunity_score"],
            "next_step": "Run agentic_fitness.py with dossier findings to generate child app blueprint",
        }
    finally:
        conn.close()


# =========================================================================
# SERVER SETUP
# =========================================================================
if __name__ == "__main__":
    server = MCPServer(name="research-server")

    server.register_tool(
        name="research_create_session",
        description="Create a new industry research session for a vertical (trading, healthcare, defense, fintech, cybersecurity, logistics)",  # noqa: E501
        handler=handle_research_create_session,
        input_schema={
            "type": "object",
            "properties": {
                "vertical": {
                    "type": "string",
                    "description": "Vertical slug (trading, healthcare, defense, fintech, cybersecurity, logistics)",
                },
                "name": {"type": "string", "description": "Session name"},
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional focus area keywords",
                },
            },
            "required": ["vertical"],
        },
    )

    server.register_tool(
        name="research_run_stage",
        description="Run a specific pipeline stage (SCOPE, LANDSCAPE, REGULATE, COMMUNITY, ACADEMIC, BUILD_BUY, SYNTHESIZE, FORECAST, DOSSIER)",  # noqa: E501
        handler=handle_research_run_stage,
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Research session ID"},
                "stage": {"type": "string", "description": "Pipeline stage to run"},
            },
            "required": ["session_id", "stage"],
        },
    )

    server.register_tool(
        name="research_run_pipeline",
        description="Run full 9-stage research pipeline: SCOPE → LANDSCAPE → REGULATE → COMMUNITY → ACADEMIC → BUILD_BUY → SYNTHESIZE → DOSSIER",  # noqa: E501
        handler=handle_research_run_pipeline,
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Existing session ID, or omit to create new"},
                "vertical": {"type": "string", "description": "Vertical slug (required if no session_id)"},
            },
        },
    )

    server.register_tool(
        name="research_get_status",
        description="Get research engine status or specific session status",
        handler=handle_research_get_status,
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID for specific status, or omit for engine overview",
                },
            },
        },
    )

    server.register_tool(
        name="research_list_sessions",
        description="List all industry research sessions with status and signal counts",
        handler=handle_research_list_sessions,
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status (created, scoping, scanning, synthesizing, dossier_ready, reviewed, child_app_triggered, archived)",  # noqa: E501
                },
            },
        },
    )

    server.register_tool(
        name="research_get_dossier",
        description="Get a generated industry research dossier with full rendered Markdown",
        handler=handle_research_get_dossier,
        input_schema={
            "type": "object",
            "properties": {
                "dossier_id": {"type": "string", "description": "Specific dossier ID"},
                "session_id": {"type": "string", "description": "Get latest dossier for session"},
            },
        },
    )

    server.register_tool(
        name="research_review_dossier",
        description="Submit HITL review for a research dossier (approve or reject)",
        handler=handle_research_review_dossier,
        input_schema={
            "type": "object",
            "properties": {
                "dossier_id": {"type": "string", "description": "Dossier ID to review"},
                "reviewer": {"type": "string", "description": "Reviewer name/email"},
                "decision": {"type": "string", "enum": ["approved", "rejected"], "description": "Review decision"},
                "notes": {"type": "string", "description": "Review notes"},
            },
            "required": ["dossier_id", "reviewer", "decision"],
        },
    )

    server.register_tool(
        name="research_list_verticals",
        description="List all available industry verticals with their configuration",
        handler=handle_research_list_verticals,
        input_schema={"type": "object", "properties": {}},
    )

    server.register_tool(
        name="research_get_challenges",
        description="Get scored challenges for a research session, ranked by composite score",
        handler=handle_research_get_challenges,
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Research session ID"},
                "severity": {"type": "string", "description": "Filter by severity: critical, notable, appendix"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            },
            "required": ["session_id"],
        },
    )

    server.register_tool(
        name="research_get_forecasts",
        description="Get AI-generated predictions and surprise recommendations for a research session (D-RES-17)",
        handler=handle_research_get_forecasts,
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Research session ID"},
                "limit": {"type": "integer", "description": "Max predictions to return (default 10)"},
            },
            "required": ["session_id"],
        },
    )

    server.register_tool(
        name="research_trigger_fitness",
        description="Trigger child app fitness assessment from an approved research dossier (requires prior HITL review)",  # noqa: E501
        handler=handle_research_trigger_fitness,
        input_schema={
            "type": "object",
            "properties": {
                "dossier_id": {"type": "string", "description": "Approved dossier ID"},
            },
            "required": ["dossier_id"],
        },
    )

    server.run()
