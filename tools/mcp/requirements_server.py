#!/usr/bin/env python3
# CUI // SP-CTI
"""Requirements Intake (RICOAS) MCP server exposing intake, document, gap, readiness, and decomposition tools.

Tools:
    create_intake_session  - Create new AI-driven requirements intake session
    resume_intake_session  - Resume an existing intake session
    get_session_status     - Get session details and status
    process_intake_turn    - Process a conversation turn in the intake session
    upload_document        - Upload a document for requirement extraction
    extract_document       - Extract requirements from an uploaded document
    detect_gaps            - Detect gaps and ambiguities in requirements
    score_readiness        - Score requirement readiness for decomposition
    decompose_requirements - Decompose requirements into SAFe hierarchy
    generate_bdd           - Generate BDD acceptance criteria for requirements

Runs as an MCP server over stdio with Content-Length framing.
"""

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

def handle_create_intake_session(args: dict) -> dict:
    """Create a new intake session."""
    create_session = _import_tool("tools.requirements.intake_engine", "create_session")
    if not create_session:
        return {"error": "intake_engine module not available", "status": "pending"}

    project_id = args.get("project_id")
    customer_name = args.get("customer_name")
    if not project_id or not customer_name:
        return {"error": "project_id and customer_name are required"}

    try:
        return create_session(
            project_id=project_id,
            customer_name=customer_name,
            customer_org=args.get("customer_org"),
            impact_level=args.get("impact_level", "IL4"),
            classification=args.get("classification", "CUI"),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_resume_intake_session(args: dict) -> dict:
    """Resume an existing intake session."""
    resume_session = _import_tool("tools.requirements.intake_engine", "resume_session")
    if not resume_session:
        return {"error": "intake_engine module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    try:
        return resume_session(session_id=session_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_get_session_status(args: dict) -> dict:
    """Get session details and status."""
    get_session = _import_tool("tools.requirements.intake_engine", "get_session")
    if not get_session:
        return {"error": "intake_engine module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    try:
        return get_session(session_id=session_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_process_intake_turn(args: dict) -> dict:
    """Process a conversation turn in the intake session."""
    process_turn = _import_tool("tools.requirements.intake_engine", "process_turn")
    if not process_turn:
        return {"error": "intake_engine module not available", "status": "pending"}

    session_id = args.get("session_id")
    message = args.get("message")
    if not session_id or not message:
        return {"error": "session_id and message are required"}

    try:
        return process_turn(session_id=session_id, message=message, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_upload_document(args: dict) -> dict:
    """Upload a document for requirement extraction."""
    upload_document = _import_tool("tools.requirements.document_extractor", "upload_document")
    if not upload_document:
        return {"error": "document_extractor module not available", "status": "pending"}

    session_id = args.get("session_id")
    file_path = args.get("file_path")
    if not session_id or not file_path:
        return {"error": "session_id and file_path are required"}

    try:
        return upload_document(
            session_id=session_id,
            file_path=file_path,
            document_type=args.get("document_type", "other"),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_extract_document(args: dict) -> dict:
    """Extract requirements from an uploaded document."""
    extract_requirements = _import_tool("tools.requirements.document_extractor", "extract_requirements")
    if not extract_requirements:
        return {"error": "document_extractor module not available", "status": "pending"}

    document_id = args.get("document_id")
    if not document_id:
        return {"error": "document_id is required"}

    try:
        return extract_requirements(document_id=document_id, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_detect_gaps(args: dict) -> dict:
    """Detect gaps and ambiguities in requirements."""
    detect_gaps = _import_tool("tools.requirements.gap_detector", "detect_gaps")
    if not detect_gaps:
        return {"error": "gap_detector module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    checks = {
        "security": args.get("check_security", True),
        "compliance": args.get("check_compliance", True),
        "testability": args.get("check_testability", True),
        "interfaces": args.get("check_interfaces", False),
        "data": args.get("check_data", False),
    }

    try:
        return detect_gaps(session_id=session_id, checks=checks, db_path=str(DB_PATH))
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_score_readiness(args: dict) -> dict:
    """Score requirement readiness for decomposition."""
    score_readiness = _import_tool("tools.requirements.readiness_scorer", "score_readiness")
    if not score_readiness:
        return {"error": "readiness_scorer module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    try:
        return score_readiness(
            session_id=session_id,
            threshold=args.get("threshold", 0.7),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_decompose_requirements(args: dict) -> dict:
    """Decompose requirements into SAFe hierarchy."""
    decompose_requirements = _import_tool("tools.requirements.decomposition_engine", "decompose_requirements")
    if not decompose_requirements:
        return {"error": "decomposition_engine module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    try:
        return decompose_requirements(
            session_id=session_id,
            target_level=args.get("target_level", "story"),
            generate_bdd=args.get("generate_bdd", False),
            estimate=args.get("estimate", True),
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


def handle_generate_bdd(args: dict) -> dict:
    """Generate BDD acceptance criteria for requirements."""
    decompose_requirements = _import_tool("tools.requirements.decomposition_engine", "decompose_requirements")
    if not decompose_requirements:
        return {"error": "decomposition_engine module not available", "status": "pending"}

    session_id = args.get("session_id")
    if not session_id:
        return {"error": "session_id is required"}

    try:
        return decompose_requirements(
            session_id=session_id,
            target_level="story",
            generate_bdd=True,
            estimate=False,
            db_path=str(DB_PATH),
        )
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    """Create and configure the Requirements Intake MCP server."""
    server = MCPServer(name="icdev-requirements", version="1.0.0")

    server.register_tool(
        name="create_intake_session",
        description="Create a new AI-driven requirements intake session for a project",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "ICDEV project ID"},
                "customer_name": {"type": "string", "description": "Customer/stakeholder name"},
                "customer_org": {"type": "string", "description": "Customer organization"},
                "impact_level": {"type": "string", "default": "IL4", "enum": ["IL2", "IL4", "IL5", "IL6"], "description": "DoD Impact Level"},
                "classification": {"type": "string", "default": "CUI", "description": "Classification marking"},
            },
            "required": ["project_id", "customer_name"],
        },
        handler=handle_create_intake_session,
    )

    server.register_tool(
        name="resume_intake_session",
        description="Resume an existing requirements intake session",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID to resume"},
            },
            "required": ["session_id"],
        },
        handler=handle_resume_intake_session,
    )

    server.register_tool(
        name="get_session_status",
        description="Get details and current status of an intake session",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID"},
            },
            "required": ["session_id"],
        },
        handler=handle_get_session_status,
    )

    server.register_tool(
        name="process_intake_turn",
        description="Process a conversation turn in the AI-driven requirements intake session",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID"},
                "message": {"type": "string", "description": "User message or response to intake question"},
            },
            "required": ["session_id", "message"],
        },
        handler=handle_process_intake_turn,
    )

    server.register_tool(
        name="upload_document",
        description="Upload a document (SOW, CDD, ConOps, SRD, SRS) for requirement extraction",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID"},
                "file_path": {"type": "string", "description": "Path to the document file"},
                "document_type": {"type": "string", "default": "other", "enum": ["sow", "cdd", "conops", "srd", "srs", "other"], "description": "Type of document being uploaded"},
            },
            "required": ["session_id", "file_path"],
        },
        handler=handle_upload_document,
    )

    server.register_tool(
        name="extract_document",
        description="Extract requirements from a previously uploaded document",
        input_schema={
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "Document ID returned from upload_document"},
            },
            "required": ["document_id"],
        },
        handler=handle_extract_document,
    )

    server.register_tool(
        name="detect_gaps",
        description="Detect gaps, ambiguities, and missing requirements in the intake session",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID"},
                "check_security": {"type": "boolean", "default": True, "description": "Check for missing security requirements"},
                "check_compliance": {"type": "boolean", "default": True, "description": "Check for missing compliance requirements"},
                "check_testability": {"type": "boolean", "default": True, "description": "Check requirement testability"},
                "check_interfaces": {"type": "boolean", "default": False, "description": "Check for missing interface definitions"},
                "check_data": {"type": "boolean", "default": False, "description": "Check for missing data requirements"},
            },
            "required": ["session_id"],
        },
        handler=handle_detect_gaps,
    )

    server.register_tool(
        name="score_readiness",
        description="Score requirement readiness to determine if requirements are complete enough for decomposition",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID"},
                "threshold": {"type": "number", "default": 0.7, "description": "Minimum readiness score (0.0-1.0) to pass"},
            },
            "required": ["session_id"],
        },
        handler=handle_score_readiness,
    )

    server.register_tool(
        name="decompose_requirements",
        description="Decompose requirements into SAFe hierarchy (Epic > Capability > Feature > Story)",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID"},
                "target_level": {"type": "string", "default": "story", "enum": ["epic", "capability", "feature", "story"], "description": "Target decomposition level"},
                "generate_bdd": {"type": "boolean", "default": False, "description": "Generate BDD acceptance criteria for stories"},
                "estimate": {"type": "boolean", "default": True, "description": "Generate story point estimates"},
            },
            "required": ["session_id"],
        },
        handler=handle_decompose_requirements,
    )

    server.register_tool(
        name="generate_bdd",
        description="Generate BDD (Gherkin) acceptance criteria for all requirements in the session",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Intake session ID"},
            },
            "required": ["session_id"],
        },
        handler=handle_generate_bdd,
    )

    return server


def main():
    """Run the Requirements Intake MCP server."""
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
# CUI // SP-CTI
