#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV SaaS -- MCP-over-SSE Transport.

Converts ICDEV's stdio MCP servers to HTTP SSE for remote SaaS clients.
Implements JSON-RPC 2.0 dispatch for MCP tool calls with tenant isolation.

Auth is handled by the gateway middleware -- by the time a request reaches
this blueprint, g.tenant_id, g.user_id, and g.user_role are already set.

Endpoints:
    POST /mcp/v1/         -- Receive JSON-RPC request, return JSON response
    GET  /mcp/v1/sse      -- SSE event stream for real-time notifications
    GET  /mcp/v1/tools    -- List available MCP tools (convenience)

Usage:
    from tools.saas.mcp_sse import mcp_bp
    app.register_blueprint(mcp_bp)
"""

import json
import logging
import queue
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, Response, g, jsonify, request

logger = logging.getLogger("saas.mcp_sse")

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
mcp_bp = Blueprint("mcp_v1", __name__, url_prefix="/mcp/v1")

# ---------------------------------------------------------------------------
# MCP protocol constants
# ---------------------------------------------------------------------------
MCP_VERSION = "2024-11-05"
SERVER_NAME = "icdev-saas"
SERVER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# SSE client registry (per-tenant notification queues)
# ---------------------------------------------------------------------------
_sse_clients: Dict[str, List[queue.Queue]] = {}
_sse_lock = threading.Lock()


def _register_sse_client(tenant_id: str) -> queue.Queue:
    """Register a new SSE client queue for a tenant."""
    q = queue.Queue(maxsize=256)
    with _sse_lock:
        if tenant_id not in _sse_clients:
            _sse_clients[tenant_id] = []
        _sse_clients[tenant_id].append(q)
    return q


def _unregister_sse_client(tenant_id: str, q: queue.Queue) -> None:
    """Unregister an SSE client queue."""
    with _sse_lock:
        if tenant_id in _sse_clients:
            try:
                _sse_clients[tenant_id].remove(q)
            except ValueError:
                pass
            if not _sse_clients[tenant_id]:
                del _sse_clients[tenant_id]


def _broadcast_event(tenant_id: str, event_type: str, data: dict) -> None:
    """Broadcast an SSE event to all connected clients for a tenant."""
    payload = json.dumps({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    with _sse_lock:
        clients = _sse_clients.get(tenant_id, [])
        for q in clients:
            try:
                q.put_nowait(("event", event_type, payload))
            except queue.Full:
                logger.warning("SSE queue full for tenant %s, dropping event",
                               tenant_id)


# ---------------------------------------------------------------------------
# Tool registry -- maps MCP tool names to Python functions
# ---------------------------------------------------------------------------
# Tools are lazily imported to avoid import-time side effects.
# Each entry: { "name": str, "description": str, "module": str, "function": str,
#               "input_schema": dict }

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
# JSON-RPC handler
# ---------------------------------------------------------------------------
def handle_jsonrpc(request_data: dict, tenant_id: str) -> dict:
    """Process a JSON-RPC 2.0 request and return a response.

    Supported methods:
        initialize      -- MCP handshake
        ping            -- Health check
        tools/list      -- List available tools
        tools/call      -- Execute a tool

    Args:
        request_data: Parsed JSON-RPC request body.
        tenant_id: Authenticated tenant ID.

    Returns:
        JSON-RPC 2.0 response dict.
    """
    rpc_id = request_data.get("id")
    method = request_data.get("method", "")
    params = request_data.get("params", {})

    def _success(result):
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    def _err(code, message, data=None):
        error = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": rpc_id, "error": error}

    # ----- initialize -----
    if method == "initialize":
        return _success({
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
        return _success({"status": "pong"})

    # ----- tools/list -----
    if method == "tools/list":
        tools = []
        for t in TOOL_REGISTRY:
            tools.append({
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            })
        return _success({"tools": tools})

    # ----- tools/call -----
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not tool_name:
            return _err(-32602, "Missing required param: name")

        try:
            result = _dispatch_tool(tool_name, arguments, tenant_id)
            # Broadcast completion event to SSE clients
            _broadcast_event(tenant_id, "tool.completed", {
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
            return _success({"content": content, "isError": False})
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            _broadcast_event(tenant_id, "tool.failed", {
                "tool": tool_name,
                "error": str(exc),
            })
            return _success({
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })

    # ----- unknown method -----
    return _err(-32601, "Method not found: {}".format(method))


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@mcp_bp.route("/", methods=["POST"])
def mcp_rpc_endpoint():
    """POST /mcp/v1/ -- Handle JSON-RPC requests."""
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }), 400

        # Validate JSON-RPC structure
        if data.get("jsonrpc") != "2.0":
            return jsonify({
                "jsonrpc": "2.0",
                "id": data.get("id"),
                "error": {"code": -32600, "message": "Invalid Request: jsonrpc must be '2.0'"},
            }), 400

        tenant_id = getattr(g, "tenant_id", None) or ""
        response = handle_jsonrpc(data, tenant_id)
        return jsonify(response)

    except Exception as exc:
        logger.error("MCP RPC error: %s", exc)
        return jsonify({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32603, "message": "Internal error: {}".format(str(exc))},
        }), 500


@mcp_bp.route("/sse", methods=["GET"])
def mcp_sse_stream():
    """GET /mcp/v1/sse -- Server-Sent Events stream for real-time notifications.

    Sends heartbeat pings every 30 seconds to keep the connection alive.
    Events are tenant-scoped; each client only receives events for their tenant.
    """
    tenant_id = getattr(g, "tenant_id", None) or ""

    def generate():
        client_q = _register_sse_client(tenant_id)
        try:
            # Send initial connection event
            yield "event: connected\ndata: {}\n\n".format(json.dumps({
                "server": SERVER_NAME,
                "version": SERVER_VERSION,
                "tenant_id": tenant_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }))

            while True:
                try:
                    msg = client_q.get(timeout=30)
                    if msg is None:
                        break
                    event_kind, event_type, payload = msg
                    yield "event: {}\ndata: {}\n\n".format(event_type, payload)
                except queue.Empty:
                    # Heartbeat to keep connection alive
                    yield ": heartbeat {}\n\n".format(
                        datetime.now(timezone.utc).isoformat())
        except GeneratorExit:
            pass
        finally:
            _unregister_sse_client(tenant_id, client_q)

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


@mcp_bp.route("/tools", methods=["GET"])
def mcp_list_tools():
    """GET /mcp/v1/tools -- List available MCP tools (convenience endpoint)."""
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
