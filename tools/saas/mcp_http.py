#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV SaaS -- MCP Streamable HTTP Transport.

Implements the MCP Streamable HTTP transport (spec revision 2025-03-26),
replacing the deprecated HTTP+SSE transport.  All MCP communication flows
through a single endpoint that supports POST, GET, and DELETE.

Auth is handled by the gateway middleware -- by the time a request reaches
this blueprint, g.tenant_id, g.user_id, and g.user_role are already set.

Single endpoint: /mcp/v1/
    POST   -- Client sends JSON-RPC request(s), server responds with JSON
              or SSE stream.  Notification-only bodies receive 202 Accepted.
    GET    -- Open SSE stream for server-initiated notifications.
    DELETE -- Terminate the MCP session.

Session lifecycle:
    1. Client POSTs ``initialize`` request (no Mcp-Session-Id yet).
    2. Server creates session, returns Mcp-Session-Id in response header.
    3. All subsequent requests MUST include the Mcp-Session-Id header.
    4. Client sends DELETE when finished; server cleans up session state.
    5. Sessions expire after 30 min of inactivity (configurable).

Usage:
    from tools.saas.mcp_http import mcp_bp
    app.register_blueprint(mcp_bp)
"""

import json
import logging
import os
import queue
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, Response, g, jsonify, request

logger = logging.getLogger("saas.mcp_http")

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
mcp_bp = Blueprint("mcp_v1", __name__, url_prefix="/mcp/v1")

# ---------------------------------------------------------------------------
# MCP protocol constants
# ---------------------------------------------------------------------------
MCP_VERSION = "2025-03-26"
SERVER_NAME = "icdev-saas"
SERVER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Session store
# ---------------------------------------------------------------------------
SESSION_TTL_SECONDS = int(os.environ.get("MCP_SESSION_TTL", "1800"))  # 30 min

_sessions: Dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _create_session(tenant_id: str, user_id: str) -> str:
    """Create a new MCP session and return its ID."""
    session_id = secrets.token_hex(32)  # 64-char hex string
    with _sessions_lock:
        _sessions[session_id] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "created_at": time.time(),
            "last_active": time.time(),
            "notification_queues": [],
        }
    logger.info("MCP session created: %s... (tenant=%s)", session_id[:12], tenant_id)
    return session_id


def _get_session(session_id: str) -> Optional[dict]:
    """Get a session by ID, or None if expired / not found."""
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        if time.time() - session["last_active"] > SESSION_TTL_SECONDS:
            _destroy_session_locked(session_id)
            return None
        session["last_active"] = time.time()
        return session


def _destroy_session(session_id: str) -> bool:
    """Destroy a session. Returns True if it existed."""
    with _sessions_lock:
        return _destroy_session_locked(session_id)


def _destroy_session_locked(session_id: str) -> bool:
    """Destroy a session (caller must hold _sessions_lock)."""
    session = _sessions.pop(session_id, None)
    if session is None:
        return False
    # Signal all notification streams to close
    for q in session.get("notification_queues", []):
        try:
            q.put_nowait(None)
        except queue.Full:
            pass
    logger.info("MCP session destroyed: %s...", session_id[:12])
    return True


def _reap_expired_sessions() -> int:
    """Remove expired sessions. Returns count of reaped sessions."""
    now = time.time()
    reaped = 0
    with _sessions_lock:
        expired = [
            sid for sid, s in _sessions.items()
            if now - s["last_active"] > SESSION_TTL_SECONDS
        ]
        for sid in expired:
            _destroy_session_locked(sid)
            reaped += 1
    if reaped:
        logger.info("Reaped %d expired MCP sessions", reaped)
    return reaped


# ---------------------------------------------------------------------------
# Notification broadcasting
# ---------------------------------------------------------------------------
def _register_notification_stream(session_id: str) -> Optional[queue.Queue]:
    """Register a GET notification stream for a session."""
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            return None
        q = queue.Queue(maxsize=256)
        session["notification_queues"].append(q)
        return q


def _unregister_notification_stream(session_id: str, q: queue.Queue) -> None:
    """Unregister a notification stream."""
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is not None:
            try:
                session["notification_queues"].remove(q)
            except ValueError:
                pass


def broadcast_event(session_id: str, event_type: str, data: dict) -> None:
    """Broadcast an SSE event to all notification streams for a session."""
    payload = json.dumps({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    with _sessions_lock:
        session = _sessions.get(session_id)
        if session is None:
            return
        for q in session.get("notification_queues", []):
            try:
                q.put_nowait(("event", event_type, payload))
            except queue.Full:
                logger.warning(
                    "Notification queue full for session %s..., dropping event",
                    session_id[:12],
                )


# Also provide tenant-level broadcast for tool completion events
def _broadcast_to_tenant(tenant_id: str, event_type: str, data: dict) -> None:
    """Broadcast an event to ALL sessions belonging to a tenant."""
    with _sessions_lock:
        for sid, session in _sessions.items():
            if session["tenant_id"] == tenant_id:
                payload = json.dumps({
                    "type": event_type,
                    "data": data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                for q in session.get("notification_queues", []):
                    try:
                        q.put_nowait(("event", event_type, payload))
                    except queue.Full:
                        pass


# ---------------------------------------------------------------------------
# Tool registry -- maps MCP tool names to Python functions
# ---------------------------------------------------------------------------
TOOL_REGISTRY = [
    {
        "name": "project_create",
        "description": "Create a new ICDEV-managed project",
        "module": "tools.project.project_create",
        "function": "create_project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "type": {"type": "string", "description": "Project type"},
                "classification": {"type": "string", "default": "CUI"},
                "impact_level": {"type": "string", "default": "IL4"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "project_list",
        "description": "List all projects",
        "module": "tools.project.project_list",
        "function": "list_projects",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status_filter": {"type": "string"},
            },
        },
    },
    {
        "name": "project_status",
        "description": "Get detailed project status",
        "module": "tools.project.project_status",
        "function": "get_project_status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "ssp_generate",
        "description": "Generate System Security Plan (SSP)",
        "module": "tools.compliance.ssp_generator",
        "function": "generate_ssp",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "poam_generate",
        "description": "Generate Plan of Action & Milestones (POA&M)",
        "module": "tools.compliance.poam_generator",
        "function": "generate_poam",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "stig_check",
        "description": "Run STIG compliance check",
        "module": "tools.compliance.stig_checker",
        "function": "run_stig_check",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "project_dir": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "sbom_generate",
        "description": "Generate Software Bill of Materials (SBOM)",
        "module": "tools.compliance.sbom_generator",
        "function": "generate_sbom",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "project_dir": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "nist_lookup",
        "description": "Look up a NIST 800-53 control",
        "module": "tools.compliance.nist_lookup",
        "function": "lookup_control",
        "inputSchema": {
            "type": "object",
            "properties": {
                "control": {"type": "string", "description": "Control ID (e.g. AC-2)"},
            },
            "required": ["control"],
        },
    },
    {
        "name": "fips199_categorize",
        "description": "Run FIPS 199 security categorization",
        "module": "tools.compliance.fips199_categorizer",
        "function": "categorize_project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "fips200_validate",
        "description": "Validate FIPS 200 minimum security requirements",
        "module": "tools.compliance.fips200_validator",
        "function": "validate_fips200",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "project_dir": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "sast_scan",
        "description": "Run static application security testing (SAST)",
        "module": "tools.security.sast_runner",
        "function": "run_sast",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
            },
            "required": ["project_dir"],
        },
    },
    {
        "name": "dependency_audit",
        "description": "Audit project dependencies for vulnerabilities",
        "module": "tools.security.dependency_auditor",
        "function": "audit_python",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_dir": {"type": "string"},
            },
            "required": ["project_dir"],
        },
    },
    # Phase 48 — AI Transparency & Accountability
    {
        "name": "ai_transparency_audit",
        "description": "Run cross-framework AI transparency audit (OMB, NIST AI, GAO)",
        "module": "tools.compliance.ai_transparency_audit",
        "function": "run_transparency_audit",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project ID"},
                "project_dir": {"type": "string", "description": "Optional project directory"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "model_card_generate",
        "description": "Generate model card per OMB M-26-04",
        "module": "tools.compliance.model_card_generator",
        "function": "generate_model_card",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "model_name": {"type": "string", "description": "Name of the AI model"},
            },
            "required": ["project_id", "model_name"],
        },
    },
    {
        "name": "system_card_generate",
        "description": "Generate system card for AI system",
        "module": "tools.compliance.system_card_generator",
        "function": "generate_system_card",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "ai_inventory_register",
        "description": "Register AI use case in inventory (OMB M-25-21)",
        "module": "tools.compliance.ai_inventory_manager",
        "function": "register_ai_component",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "name": {"type": "string", "description": "AI component name"},
                "purpose": {"type": "string", "description": "Purpose of the AI component"},
                "risk_level": {"type": "string", "description": "minimal_risk, high_impact, or safety_impacting"},
            },
            "required": ["project_id", "name"],
        },
    },
    {
        "name": "confabulation_check",
        "description": "Check output for confabulation indicators (NIST AI 600-1)",
        "module": "tools.security.confabulation_detector",
        "function": "check_output",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "text": {"type": "string", "description": "Text to check for confabulation"},
            },
            "required": ["project_id", "text"],
        },
    },
    {
        "name": "fairness_assess",
        "description": "Run fairness and bias compliance assessment (OMB M-26-04)",
        "module": "tools.compliance.fairness_assessor",
        "function": "assess_fairness",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "gao_evidence_build",
        "description": "Build GAO audit evidence package (GAO-21-519SP)",
        "module": "tools.compliance.gao_evidence_builder",
        "function": "build_evidence",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
]

# Build lookup dict
_TOOL_MAP: Dict[str, dict] = {t["name"]: t for t in TOOL_REGISTRY}


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------
def _load_tool_func(tool_entry: dict) -> Callable:
    """Dynamically import and return the tool function."""
    import importlib
    mod = importlib.import_module(tool_entry["module"])
    return getattr(mod, tool_entry["function"])


def _dispatch_tool(name: str, arguments: dict, tenant_id: str) -> Any:
    """Route an MCP tool call to the corresponding Python function.

    Injects db_path for tenant isolation via the tenant_db_adapter.

    Args:
        name: MCP tool name from the registry.
        arguments: Tool arguments from the JSON-RPC params.
        tenant_id: Authenticated tenant ID.

    Returns:
        Tool result (dict or list).

    Raises:
        ValueError: If the tool is not found.
    """
    entry = _TOOL_MAP.get(name)
    if not entry:
        raise ValueError("Unknown tool: {}".format(name))

    tool_func = _load_tool_func(entry)

    # Use tenant_db_adapter for tools that need DB isolation
    from tools.saas.tenant_db_adapter import call_tool_with_tenant_db
    try:
        result = call_tool_with_tenant_db(tool_func, tenant_id, **arguments)
    except TypeError:
        # Some tools (like sast_runner) don't accept db_path
        result = tool_func(**arguments)

    return result


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------
def _jsonrpc_success(rpc_id, result):
    """Build a JSON-RPC 2.0 success response."""
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _jsonrpc_error(rpc_id, code, message, data=None):
    """Build a JSON-RPC 2.0 error response."""
    error = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": error}


def _is_request(msg: dict) -> bool:
    """Return True if the JSON-RPC message is a request (has 'id' + 'method')."""
    return "id" in msg and "method" in msg


def _is_notification(msg: dict) -> bool:
    """Return True if the message is a notification (has 'method' but no 'id')."""
    return "method" in msg and "id" not in msg


def _is_response(msg: dict) -> bool:
    """Return True if the message is a response (has 'id' but no 'method')."""
    return "id" in msg and "method" not in msg


# ---------------------------------------------------------------------------
# JSON-RPC method handler
# ---------------------------------------------------------------------------
def _handle_request(rpc_msg: dict, tenant_id: str, session_id: str) -> dict:
    """Process a single JSON-RPC 2.0 request and return a response.

    Supported methods:
        initialize      -- MCP handshake, creates session
        ping            -- Health check
        tools/list      -- List available tools
        tools/call      -- Execute a tool

    Args:
        rpc_msg: Parsed JSON-RPC request body.
        tenant_id: Authenticated tenant ID.
        session_id: Current MCP session ID (empty for initialize).

    Returns:
        JSON-RPC 2.0 response dict.
    """
    rpc_id = rpc_msg.get("id")
    method = rpc_msg.get("method", "")
    params = rpc_msg.get("params", {})

    # ----- initialize -----
    if method == "initialize":
        return _jsonrpc_success(rpc_id, {
            "protocolVersion": MCP_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        })

    # ----- ping -----
    if method == "ping":
        return _jsonrpc_success(rpc_id, {"status": "pong"})

    # ----- tools/list -----
    if method == "tools/list":
        tools = []
        for t in TOOL_REGISTRY:
            tools.append({
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            })
        return _jsonrpc_success(rpc_id, {"tools": tools})

    # ----- tools/call -----
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not tool_name:
            return _jsonrpc_error(rpc_id, -32602, "Missing required param: name")

        try:
            result = _dispatch_tool(tool_name, arguments, tenant_id)
            # Broadcast completion event to notification streams
            if session_id:
                broadcast_event(session_id, "tool.completed", {
                    "tool": tool_name,
                    "status": "success",
                })
            # MCP tools/call returns content array
            content = []
            if isinstance(result, dict):
                content.append({
                    "type": "text",
                    "text": json.dumps(result, indent=2, default=str),
                })
            elif isinstance(result, str):
                content.append({"type": "text", "text": result})
            else:
                content.append({
                    "type": "text",
                    "text": json.dumps(result, default=str),
                })
            return _jsonrpc_success(rpc_id, {"content": content, "isError": False})
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            if session_id:
                broadcast_event(session_id, "tool.failed", {
                    "tool": tool_name,
                    "error": str(exc),
                })
            return _jsonrpc_success(rpc_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })

    # ----- unknown method -----
    return _jsonrpc_error(rpc_id, -32601, "Method not found: {}".format(method))


# ---------------------------------------------------------------------------
# Accept header validation
# ---------------------------------------------------------------------------
def _validate_accept_header() -> Optional[Response]:
    """Validate the Accept header per the Streamable HTTP spec.

    Clients MUST include both application/json and text/event-stream
    in their Accept header.  Returns an error Response if invalid,
    or None if acceptable.
    """
    accept = request.headers.get("Accept", "")
    # Be permissive: accept if both types are present, or if wildcard */*
    if "*/*" in accept:
        return None
    has_json = "application/json" in accept
    has_sse = "text/event-stream" in accept
    if has_json and has_sse:
        return None
    # Return 406 Not Acceptable
    return Response(
        json.dumps({
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32600,
                "message": (
                    "Not Acceptable: Accept header must include both "
                    "application/json and text/event-stream"
                ),
            },
        }),
        status=406,
        content_type="application/json",
        headers={"X-Classification": "CUI // SP-CTI"},
    )


# ---------------------------------------------------------------------------
# Flask routes -- single MCP endpoint
# ---------------------------------------------------------------------------

@mcp_bp.route("/", methods=["POST"])
def mcp_post():
    """POST /mcp/v1/ -- Handle JSON-RPC messages (Streamable HTTP).

    The body may contain:
    - A single JSON-RPC request (has 'id' + 'method')
    - A single JSON-RPC notification (has 'method', no 'id')
    - A single JSON-RPC response (has 'id', no 'method')
    - A batch array of the above

    Behavior:
    - If body contains only notifications/responses -> 202 Accepted
    - If body contains request(s) -> JSON response(s)
    - ``initialize`` request creates session and returns Mcp-Session-Id
    """
    # Validate Accept header
    accept_error = _validate_accept_header()
    if accept_error is not None:
        return accept_error

    # Parse body
    try:
        data = request.get_json(force=True, silent=True)
        if data is None:
            return Response(
                json.dumps(_jsonrpc_error(None, -32700, "Parse error")),
                status=400,
                content_type="application/json",
                headers={"X-Classification": "CUI // SP-CTI"},
            )
    except Exception:
        return Response(
            json.dumps(_jsonrpc_error(None, -32700, "Parse error")),
            status=400,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )

    tenant_id = getattr(g, "tenant_id", None) or ""
    user_id = getattr(g, "user_id", None) or ""
    session_id = request.headers.get("Mcp-Session-Id", "")

    # Determine if single message or batch
    is_batch = isinstance(data, list)
    messages = data if is_batch else [data]

    # Validate JSON-RPC 2.0 for all messages
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return Response(
                json.dumps(_jsonrpc_error(
                    msg.get("id") if isinstance(msg, dict) else None,
                    -32600,
                    "Invalid Request: jsonrpc must be '2.0'",
                )),
                status=400,
                content_type="application/json",
                headers={"X-Classification": "CUI // SP-CTI"},
            )

    # Separate requests from notifications/responses
    requests_list = []
    has_initialize = False
    for msg in messages:
        if _is_request(msg):
            requests_list.append(msg)
            if msg.get("method") == "initialize":
                has_initialize = True

    # Session validation -- initialize doesn't need a session
    if not has_initialize and session_id:
        session = _get_session(session_id)
        if session is None:
            return Response(
                json.dumps(_jsonrpc_error(
                    None, -32600,
                    "Invalid or expired session. Send initialize to start a new session.",
                )),
                status=400,
                content_type="application/json",
                headers={"X-Classification": "CUI // SP-CTI"},
            )
        # Verify tenant matches
        if session["tenant_id"] != tenant_id:
            return Response(
                json.dumps(_jsonrpc_error(None, -32600, "Session/tenant mismatch")),
                status=403,
                content_type="application/json",
                headers={"X-Classification": "CUI // SP-CTI"},
            )

    # If body has only notifications/responses, return 202 Accepted
    if not requests_list:
        return Response("", status=202, headers={"X-Classification": "CUI // SP-CTI"})

    # Process requests
    responses = []
    new_session_id = None

    for rpc_msg in requests_list:
        method = rpc_msg.get("method", "")

        # Handle initialize: create session
        if method == "initialize":
            new_session_id = _create_session(tenant_id, user_id)
            result = _handle_request(rpc_msg, tenant_id, new_session_id)
            responses.append(result)
            continue

        # For non-initialize, require session (unless it's being created in this batch)
        effective_session = new_session_id or session_id
        if not effective_session and not has_initialize:
            responses.append(_jsonrpc_error(
                rpc_msg.get("id"), -32600,
                "No session. Send initialize first.",
            ))
            continue

        result = _handle_request(rpc_msg, tenant_id, effective_session)
        responses.append(result)

    # Build response
    response_headers = {
        "X-Classification": "CUI // SP-CTI",
        "Content-Type": "application/json",
    }
    if new_session_id:
        response_headers["Mcp-Session-Id"] = new_session_id

    if is_batch:
        body = json.dumps(responses)
    else:
        body = json.dumps(responses[0]) if responses else "{}"

    return Response(body, status=200, headers=response_headers)


@mcp_bp.route("/", methods=["GET"])
def mcp_get():
    """GET /mcp/v1/ -- Server-initiated notifications SSE stream.

    Opens an SSE stream for receiving server-to-client notifications
    (e.g., tool.completed, compliance.changed).  Requires valid
    Mcp-Session-Id header.

    Sends heartbeat comments every 30 seconds to keep the connection alive.
    """
    session_id = request.headers.get("Mcp-Session-Id", "")
    if not session_id:
        return Response(
            json.dumps({"error": "Mcp-Session-Id header required"}),
            status=400,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )

    tenant_id = getattr(g, "tenant_id", None) or ""
    session = _get_session(session_id)
    if session is None:
        return Response(
            json.dumps({"error": "Invalid or expired session"}),
            status=400,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )

    if session["tenant_id"] != tenant_id:
        return Response(
            json.dumps({"error": "Session/tenant mismatch"}),
            status=403,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )

    notification_q = _register_notification_stream(session_id)
    if notification_q is None:
        return Response(
            json.dumps({"error": "Session not found"}),
            status=400,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )

    def generate():
        try:
            # Initial connection event
            yield "event: connected\ndata: {}\n\n".format(json.dumps({
                "server": SERVER_NAME,
                "version": SERVER_VERSION,
                "session_id": session_id[:12] + "...",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

            while True:
                try:
                    msg = notification_q.get(timeout=30)
                    if msg is None:
                        # Session destroyed
                        break
                    event_kind, event_type, payload = msg
                    yield "event: {}\ndata: {}\n\n".format(event_type, payload)
                except queue.Empty:
                    # Heartbeat comment to keep connection alive
                    yield ": heartbeat {}\n\n".format(
                        datetime.now(timezone.utc).isoformat())
        except GeneratorExit:
            pass
        finally:
            _unregister_notification_stream(session_id, notification_q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Classification": "CUI // SP-CTI",
        },
    )


@mcp_bp.route("/", methods=["DELETE"])
def mcp_delete():
    """DELETE /mcp/v1/ -- Terminate MCP session.

    Destroys the session identified by the Mcp-Session-Id header.
    Closes all associated notification streams.
    """
    session_id = request.headers.get("Mcp-Session-Id", "")
    if not session_id:
        return Response(
            json.dumps({"error": "Mcp-Session-Id header required"}),
            status=400,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )

    tenant_id = getattr(g, "tenant_id", None) or ""

    # Validate session belongs to this tenant before destroying
    session = _get_session(session_id)
    if session is None:
        # Session already gone or expired -- treat as success
        return Response("", status=204, headers={"X-Classification": "CUI // SP-CTI"})

    if session["tenant_id"] != tenant_id:
        return Response(
            json.dumps({"error": "Session/tenant mismatch"}),
            status=403,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )

    _destroy_session(session_id)
    return Response("", status=204, headers={"X-Classification": "CUI // SP-CTI"})


# ---------------------------------------------------------------------------
# Convenience endpoint: GET /mcp/v1/tools
# ---------------------------------------------------------------------------
@mcp_bp.route("/tools", methods=["GET"])
def mcp_list_tools():
    """GET /mcp/v1/tools -- List available MCP tools (convenience endpoint).

    Not part of the Streamable HTTP spec, but useful for tool discovery
    without a full MCP session.
    """
    tools = []
    for t in TOOL_REGISTRY:
        tools.append({
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        })
    return jsonify({
        "tools": tools,
        "total": len(tools),
        "classification": "CUI // SP-CTI",
    })
