#!/usr/bin/env python3

# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""ICDEV™ SaaS -- MCP Streamable HTTP Transport.

Implements the MCP Streamable HTTP transport (spec revision 2025-03-26),
replacing the deprecated HTTP+SSE transport.  All MCP communication flows
through a single endpoint that supports POST, GET, and DELETE.

Auth is handled by the gateway middleware -- by the time a request reaches
this blueprint, g.tenant_id, g.user_id, and g.user_role are already set.

Per-tool authorization (exa-policy-05)
--------------------------------------
This is the only MCP surface in the tree with a real authenticated principal,
so it is the only one that enforces per-tool RBAC.  ICDEV™ exposes MCP three
ways and each gets a different answer:

  stdio (``tools/mcp/unified_server.py``)
      DELIBERATELY UNENFORCED.  Those servers carry no caller identity at all,
      so any role supplied over stdio would be self-asserted by the caller --
      that is not authentication, and a check built on it is theatre.  The
      caller is the developer at the keyboard, who already has shell access to
      the whole repo.  What actually bounds that surface is the reversibility
      classifier in ``tools/agent_runtime/approval_gate.py``, the hard blocks
      in ``.claude/hooks/pre_tool_use.py``, and ``args/file_access_tiers.yaml``.
  Studio ``agent`` / ``mcp`` nodes
      ALREADY ENFORCED by ``tools/studio/executors/agent_tool_gate.py`` under
      gate AGENT-WF-001.  No second gate is added beside it.
  this module (MCP over HTTP for tenants)
      ENFORCED HERE, because the middleware has already authenticated the
      principal.  ``tools/list`` does not advertise what the caller may not
      call, and ``_dispatch_tool`` does not run it.

The decision itself is delegated to :class:`tools.security.mcp_tool_authorizer.
MCPToolAuthorizer` (D261) reading ``args/owasp_agentic_config.yaml``.  This
module deliberately does NOT keep its own role/tool matrix -- when the MCP
registry grows role and IL declarations (exa-policy-07), ``SAAS_ROLE_TO_RBAC_
ROLE`` is the one thing that goes away.

Mode is read from ``ICDEV_SAAS_MCP_AUTHZ_MODE`` and defaults to ``monitor``:
would-be denials are logged to the append-only platform audit trail and the
call still proceeds, so the policy can be measured against real tenant traffic
before it starts refusing.  Set it to ``enforce`` to make denials binding.
Monitor is the shipped default because the D261 matrix predates the SaaS role
vocabulary and does not yet cover ``viewer`` or ``auditor`` -- see
``SAAS_ROLE_TO_RBAC_ROLE``.

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

from tools.logging.icdev_logger import get_logger  # noqa: E402

from flask import Blueprint, Response, g, jsonify, request  # noqa: E402

logger = get_logger("saas.mcp_http")

# ---------------------------------------------------------------------------
# OAuth 2.1 / Elicitation / Tasks — Phase 55, D345-D346
# ---------------------------------------------------------------------------
from tools.saas.mcp_oauth import MCPElicitationHandler, MCPOAuthVerifier, MCPTaskManager  # noqa: E402

_oauth_verifier: Optional[MCPOAuthVerifier] = None
_elicitation_handler = MCPElicitationHandler()
_task_manager = MCPTaskManager()


def get_oauth_verifier() -> MCPOAuthVerifier:
    """Lazy-init the OAuth verifier singleton."""
    global _oauth_verifier
    if _oauth_verifier is None:
        _oauth_verifier = MCPOAuthVerifier()
    return _oauth_verifier


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
        expired = [sid for sid, s in _sessions.items() if now - s["last_active"] > SESSION_TTL_SECONDS]
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
    payload = json.dumps(
        {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
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
                payload = json.dumps(
                    {
                        "type": event_type,
                        "data": data,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
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
        "description": "Create a new ICDEV™-managed project",
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
# Per-tool authorization -- exa-policy-05, consuming MCPToolAuthorizer (D261)
# ---------------------------------------------------------------------------
#: Env var selecting the authorization mode.
AUTHZ_MODE_ENV = "ICDEV_SAAS_MCP_AUTHZ_MODE"

#: Log the decision, then do what the caller asked anyway.
AUTHZ_MODE_MONITOR = "monitor"

#: Log the decision and refuse when it is a deny.
AUTHZ_MODE_ENFORCE = "enforce"

#: Shipped default. See the module docstring for why this is not ``enforce``.
DEFAULT_AUTHZ_MODE = AUTHZ_MODE_MONITOR

#: JSON-RPC error code for an authorization refusal.  The reserved range stops
#: at -32600, so this is in the implementation-defined server-error band.  It is
#: deliberately a protocol error rather than an ``isError: true`` content blob:
#: "you may not call this at all" is not a tool result.
JSONRPC_UNAUTHORIZED = -32003

#: SaaS tenant roles (``tools/saas/models.py::UserRole``) are not the D261 role
#: vocabulary in ``args/owasp_agentic_config.yaml::mcp_authorization``.  Map,
#: do not fork -- a second matrix is exactly what this surface was told not to
#: grow.  ``viewer`` and ``auditor`` have no D261 equivalent and are left
#: unmapped on purpose: MCPToolAuthorizer treats an unknown role as
#: ``default_policy`` (deny), which is the safe direction, and monitor mode is
#: how we find out whether any real tenant traffic depends on them before that
#: becomes binding.  exa-policy-07 replaces this map with registry-declared
#: roles.
SAAS_ROLE_TO_RBAC_ROLE = {
    "tenant_admin": "admin",
    "admin": "admin",
    "developer": "developer",
    "compliance_officer": "isso",
    "isso": "isso",
    "pm": "pm",
    "co": "co",
}

_authorizer = None
_authorizer_lock = threading.Lock()


class MCPAuthorizationError(PermissionError):
    """Raised when an authenticated caller may not use the requested tool."""

    def __init__(self, decision: dict):
        self.decision = decision
        super().__init__(decision.get("reason", "Tool not authorized for role"))


def get_authz_mode() -> str:
    """Return the active authorization mode.

    Read per call rather than cached at import so an operator can flip the
    mode without a restart, and so tests can exercise both paths.
    """
    mode = (os.environ.get(AUTHZ_MODE_ENV) or DEFAULT_AUTHZ_MODE).strip().lower()
    return mode if mode in (AUTHZ_MODE_MONITOR, AUTHZ_MODE_ENFORCE) else DEFAULT_AUTHZ_MODE


def get_authorizer():
    """Lazy-init the shared MCPToolAuthorizer singleton."""
    global _authorizer
    if _authorizer is None:
        with _authorizer_lock:
            if _authorizer is None:
                from tools.security.mcp_tool_authorizer import MCPToolAuthorizer

                _authorizer = MCPToolAuthorizer()
    return _authorizer


def authorize_tool(tool_name: str, user_role: Optional[str]) -> dict:
    """Decide whether ``user_role`` may use ``tool_name`` on this surface.

    Args:
        tool_name: MCP tool name from the registry.
        user_role: SaaS tenant role, as set on ``g.user_role`` by the auth
            middleware.  An empty/None role is treated as an unknown role and
            gets the configured default policy -- it is never treated as admin.

    Returns:
        Decision dict with:
            allowed   -- what the policy says.
            enforced  -- whether that verdict binds (False in monitor mode).
            mode      -- the active authorization mode.
            role      -- the SaaS role as presented.
            rbac_role -- the D261 role it mapped to.
            tool      -- the tool name.
            reason    -- MCPToolAuthorizer's explanation.
    """
    saas_role = (user_role or "").strip().lower()
    # An unmapped role is passed through verbatim so MCPToolAuthorizer reports
    # it as unknown and applies default_policy, rather than being silently
    # upgraded to something that happens to be in the matrix.
    rbac_role = SAAS_ROLE_TO_RBAC_ROLE.get(saas_role, saas_role)
    verdict = get_authorizer().authorize(rbac_role, tool_name)
    mode = get_authz_mode()
    return {
        "allowed": bool(verdict.get("allowed")),
        "enforced": mode == AUTHZ_MODE_ENFORCE,
        "mode": mode,
        "role": saas_role,
        "rbac_role": rbac_role,
        "tool": tool_name,
        "reason": verdict.get("reason", ""),
    }


def _write_authz_audit(decision: dict, tenant_id: str, user_id: str, action: str) -> None:
    """Append an authorization decision to the platform audit trail.

    Mirrors ``tools/saas/auth/middleware.py::_log_auth_event`` exactly -- same
    table, same column list -- so this cannot drift from the live schema.
    Best-effort: an audit-trail outage must not turn into a tenant-visible
    500 on a call the policy already allowed.
    """
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        conn.execute(
            """
            INSERT INTO audit_platform (tenant_id, user_id, event_type, action, details, ip_address, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id or None,
                user_id or None,
                "mcp.authz",
                action,
                json.dumps(decision),
                (request.remote_addr or "unknown") if request else "unknown",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # pragma: no cover - audit outage must not block
        logger.debug("Could not write MCP authz audit row: %s", exc)


def log_authz_decision(decision: dict, tenant_id: str, user_id: str, surface: str) -> None:
    """Log an authorization decision. Denials are recorded in both modes.

    In monitor mode the denial is a *would-be* denial: it is logged and
    audited, and the caller is let through anyway.  That record is the whole
    point of monitor mode -- it is the evidence used to decide whether the
    matrix is right before ``enforce`` makes it binding.
    """
    if decision.get("allowed"):
        return
    action = "mcp.tool.denied" if decision.get("enforced") else "mcp.tool.would_deny"
    logger.warning(
        "MCP authz %s [%s] tenant=%s role=%s->%s tool=%s surface=%s reason=%s",
        "DENY" if decision.get("enforced") else "WOULD-DENY",
        decision.get("mode"),
        tenant_id or "-",
        decision.get("role") or "-",
        decision.get("rbac_role") or "-",
        decision.get("tool"),
        surface,
        decision.get("reason"),
    )
    _write_authz_audit(dict(decision, surface=surface), tenant_id, user_id, action)


def authorized_tools(user_role: Optional[str], tenant_id: str = "", user_id: str = "") -> list:
    """Return the registry entries ``user_role`` may be offered.

    Offer-time half of the check.  A tool the caller cannot use is not named
    in ``tools/list``, so a client never builds a call it will only be refused
    for.  In monitor mode nothing is hidden -- the would-be omissions are
    logged instead, because silently shrinking a tenant's tool list is itself
    the behaviour change we are trying to measure first.
    """
    offered = []
    for entry in TOOL_REGISTRY:
        decision = authorize_tool(entry["name"], user_role)
        if decision["allowed"]:
            offered.append(entry)
            continue
        log_authz_decision(decision, tenant_id, user_id, "tools/list")
        if not decision["enforced"]:
            offered.append(entry)
    return offered


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------
def _load_tool_func(tool_entry: dict) -> Callable:
    """Dynamically import and return the tool function."""
    import importlib

    mod = importlib.import_module(tool_entry["module"])
    return getattr(mod, tool_entry["function"])


def _dispatch_tool(
    name: str,
    arguments: dict,
    tenant_id: str,
    user_role: Optional[str] = None,
    user_id: str = "",
) -> Any:
    """Route an MCP tool call to the corresponding Python function.

    Injects db_path for tenant isolation via the tenant_db_adapter.

    Authorization is checked here rather than at the call site because this is
    the single chokepoint through which a tool actually executes -- a future
    caller that forgets to pre-check still cannot dispatch past it.

    Args:
        name: MCP tool name from the registry.
        arguments: Tool arguments from the JSON-RPC params.
        tenant_id: Authenticated tenant ID.
        user_role: Authenticated caller role (``g.user_role``).
        user_id: Authenticated user ID, for the audit row.

    Returns:
        Tool result (dict or list).

    Raises:
        ValueError: If the tool is not found.
        MCPAuthorizationError: If the caller's role may not use the tool and
            the surface is in ``enforce`` mode.
    """
    entry = _TOOL_MAP.get(name)
    if not entry:
        raise ValueError("Unknown tool: {}".format(name))

    decision = authorize_tool(name, user_role)
    if not decision["allowed"]:
        log_authz_decision(decision, tenant_id, user_id, "tools/call")
        if decision["enforced"]:
            raise MCPAuthorizationError(decision)

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
def _handle_request(
    rpc_msg: dict,
    tenant_id: str,
    session_id: str,
    user_role: Optional[str] = None,
    user_id: str = "",
) -> dict:
    """Process a single JSON-RPC 2.0 request and return a response.

    Supported methods:
        initialize      -- MCP handshake, creates session
        ping            -- Health check
        tools/list      -- List available tools (filtered by role)
        tools/call      -- Execute a tool (authorized by role)

    Args:
        rpc_msg: Parsed JSON-RPC request body.
        tenant_id: Authenticated tenant ID.
        session_id: Current MCP session ID (empty for initialize).
        user_role: Authenticated caller role (``g.user_role``).
        user_id: Authenticated user ID, for authorization audit rows.

    Returns:
        JSON-RPC 2.0 response dict.
    """
    rpc_id = rpc_msg.get("id")
    method = rpc_msg.get("method", "")
    params = rpc_msg.get("params", {})

    # ----- initialize -----
    if method == "initialize":
        return _jsonrpc_success(
            rpc_id,
            {
                "protocolVersion": MCP_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            },
        )

    # ----- ping -----
    if method == "ping":
        return _jsonrpc_success(rpc_id, {"status": "pong"})

    # ----- tools/list -----
    if method == "tools/list":
        tools = []
        for t in authorized_tools(user_role, tenant_id, user_id):
            tools.append(
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"],
                }
            )
        return _jsonrpc_success(rpc_id, {"tools": tools})

    # ----- tools/call -----
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if not tool_name:
            return _jsonrpc_error(rpc_id, -32602, "Missing required param: name")

        try:
            result = _dispatch_tool(tool_name, arguments, tenant_id, user_role, user_id)
            # Broadcast completion event to notification streams
            if session_id:
                broadcast_event(
                    session_id,
                    "tool.completed",
                    {
                        "tool": tool_name,
                        "status": "success",
                    },
                )
            # MCP tools/call returns content array
            content = []
            if isinstance(result, dict):
                content.append(
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2, default=str),
                    }
                )
            elif isinstance(result, str):
                content.append({"type": "text", "text": result})
            else:
                content.append(
                    {
                        "type": "text",
                        "text": json.dumps(result, default=str),
                    }
                )
            return _jsonrpc_success(rpc_id, {"content": content, "isError": False})
        except MCPAuthorizationError as exc:
            # A refusal, not a tool failure -- surfaced as a JSON-RPC error so a
            # client can tell "you may not" apart from "it broke", and so the
            # refusal is not mistaken for a result the tool produced.
            return _jsonrpc_error(
                rpc_id,
                JSONRPC_UNAUTHORIZED,
                "Tool not authorized for role: {}".format(tool_name),
                {
                    "tool": tool_name,
                    "role": exc.decision.get("role"),
                    "reason": exc.decision.get("reason"),
                },
            )
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            if session_id:
                broadcast_event(
                    session_id,
                    "tool.failed",
                    {
                        "tool": tool_name,
                        "error": str(exc),
                    },
                )
            return _jsonrpc_success(
                rpc_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )

    # ----- elicitation/create (D346) -----
    if method == "elicitation/create":
        tool_name = params.get("toolName", "")
        question = params.get("question", "")
        options = params.get("options")
        input_type = params.get("inputType", "text")
        if not tool_name or not question:
            return _jsonrpc_error(rpc_id, -32602, "Missing required: toolName, question")
        elicitation = _elicitation_handler.create_elicitation(
            tool_name=tool_name,
            question=question,
            options=options,
            input_type=input_type,
        )
        if session_id:
            broadcast_event(session_id, "elicitation.created", elicitation)
        return _jsonrpc_success(rpc_id, elicitation)

    # ----- elicitation/respond (D346) -----
    if method == "elicitation/respond":
        elicitation_id = params.get("elicitationId", "")
        response_text = params.get("response", "")
        if not elicitation_id:
            return _jsonrpc_error(rpc_id, -32602, "Missing required: elicitationId")
        resolved = _elicitation_handler.resolve_elicitation(elicitation_id, response_text)
        if "error" in resolved:
            return _jsonrpc_error(rpc_id, -32602, resolved["error"])
        if session_id:
            broadcast_event(session_id, "elicitation.resolved", resolved)
        return _jsonrpc_success(rpc_id, resolved)

    # ----- tasks/create (D346) -----
    if method == "tasks/create":
        tool_name = params.get("toolName", "")
        tool_params = params.get("params", {})
        if not tool_name:
            return _jsonrpc_error(rpc_id, -32602, "Missing required: toolName")
        task = _task_manager.create_task(tool_name, tool_params)
        if session_id:
            broadcast_event(session_id, "task.created", task)
        return _jsonrpc_success(rpc_id, task)

    # ----- tasks/get (D346) -----
    if method == "tasks/get":
        task_id = params.get("taskId", "")
        if not task_id:
            return _jsonrpc_error(rpc_id, -32602, "Missing required: taskId")
        task = _task_manager.get_task(task_id)
        if "error" in task:
            return _jsonrpc_error(rpc_id, -32602, task["error"])
        return _jsonrpc_success(rpc_id, task)

    # ----- tasks/list (D346) -----
    if method == "tasks/list":
        status_filter = params.get("status")
        tasks = _task_manager.list_tasks(status=status_filter)
        return _jsonrpc_success(rpc_id, {"tasks": tasks, "total": len(tasks)})

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
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": (
                        "Not Acceptable: Accept header must include both application/json and text/event-stream"
                    ),
                },
            }
        ),
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
    user_role = getattr(g, "user_role", None) or ""
    session_id = request.headers.get("Mcp-Session-Id", "")

    # Determine if single message or batch
    is_batch = isinstance(data, list)
    messages = data if is_batch else [data]

    # Validate JSON-RPC 2.0 for all messages
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return Response(
                json.dumps(
                    _jsonrpc_error(
                        msg.get("id") if isinstance(msg, dict) else None,
                        -32600,
                        "Invalid Request: jsonrpc must be '2.0'",
                    )
                ),
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
                json.dumps(
                    _jsonrpc_error(
                        None,
                        -32600,
                        "Invalid or expired session. Send initialize to start a new session.",
                    )
                ),
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
            result = _handle_request(rpc_msg, tenant_id, new_session_id, user_role, user_id)
            responses.append(result)
            continue

        # For non-initialize, require session (unless it's being created in this batch)
        effective_session = new_session_id or session_id
        if not effective_session and not has_initialize:
            responses.append(
                _jsonrpc_error(
                    rpc_msg.get("id"),
                    -32600,
                    "No session. Send initialize first.",
                )
            )
            continue

        result = _handle_request(rpc_msg, tenant_id, effective_session, user_role, user_id)
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
            yield "event: connected\ndata: {}\n\n".format(
                json.dumps(
                    {
                        "server": SERVER_NAME,
                        "version": SERVER_VERSION,
                        "session_id": session_id[:12] + "...",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )

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
                    yield ": heartbeat {}\n\n".format(datetime.now(timezone.utc).isoformat())
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
@mcp_bp.route("/oauth/verify", methods=["POST"])
def mcp_oauth_verify():
    """POST /mcp/v1/oauth/verify -- Verify an OAuth/API token (D345).

    Accepts JSON body with ``token`` field.  Returns verification result
    including user info and scopes.  Useful for MCP clients that need
    to pre-verify tokens before establishing a session.
    """
    data = request.get_json(force=True, silent=True)
    if not data or "token" not in data:
        return Response(
            json.dumps({"error": "Missing 'token' in request body"}),
            status=400,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )
    verifier = get_oauth_verifier()
    result = verifier.verify_token(data["token"])
    status_code = 200 if result.get("verified") else 401
    return Response(
        json.dumps(result, default=str),
        status=status_code,
        content_type="application/json",
        headers={"X-Classification": "CUI // SP-CTI"},
    )


@mcp_bp.route("/oauth/token", methods=["POST"])
def mcp_oauth_generate():
    """POST /mcp/v1/oauth/token -- Generate offline HMAC token (D345).

    For air-gapped environments.  Requires existing authentication
    (gateway middleware sets g.user_id).  Returns an HMAC-signed token.
    """
    user_id = getattr(g, "user_id", None) or ""
    if not user_id:
        return Response(
            json.dumps({"error": "Authentication required"}),
            status=401,
            content_type="application/json",
            headers={"X-Classification": "CUI // SP-CTI"},
        )
    data = request.get_json(force=True, silent=True) or {}
    verifier = get_oauth_verifier()
    token = verifier.generate_offline_token(
        user_id=user_id,
        email=getattr(g, "user_email", data.get("email", "")),
        role=getattr(g, "user_role", data.get("role", "developer")),
        scopes=data.get("scopes", ["mcp:read", "mcp:write"]),
        tenant_id=getattr(g, "tenant_id", data.get("tenant_id")),
        ttl_seconds=int(data.get("ttl_seconds", 3600)),
    )
    return jsonify(
        {
            "token": token,
            "token_type": "hmac",
            "expires_in": int(data.get("ttl_seconds", 3600)),
            "classification": "CUI // SP-CTI",
        }
    )


@mcp_bp.route("/elicitations", methods=["GET"])
def mcp_list_elicitations():
    """GET /mcp/v1/elicitations -- List pending elicitations (D346)."""
    pending = _elicitation_handler.get_pending()
    return jsonify(
        {
            "elicitations": pending,
            "total": len(pending),
            "classification": "CUI // SP-CTI",
        }
    )


@mcp_bp.route("/tasks", methods=["GET"])
def mcp_list_tasks_endpoint():
    """GET /mcp/v1/tasks -- List MCP tasks (D346)."""
    status_filter = request.args.get("status")
    tasks = _task_manager.list_tasks(status=status_filter)
    return jsonify(
        {
            "tasks": tasks,
            "total": len(tasks),
            "classification": "CUI // SP-CTI",
        }
    )


@mcp_bp.route("/tools", methods=["GET"])
def mcp_list_tools():
    """GET /mcp/v1/tools -- List available MCP tools (convenience endpoint).

    Not part of the Streamable HTTP spec, but useful for tool discovery
    without a full MCP session.  Filtered by role for the same reason
    ``tools/list`` is: a discovery endpoint that advertises what the caller
    may not call would just route around the ``tools/list`` filter.
    """
    tenant_id = getattr(g, "tenant_id", None) or ""
    user_id = getattr(g, "user_id", None) or ""
    user_role = getattr(g, "user_role", None) or ""
    tools = []
    for t in authorized_tools(user_role, tenant_id, user_id):
        tools.append(
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            }
        )
    return jsonify(
        {
            "tools": tools,
            "total": len(tools),
            "classification": "CUI // SP-CTI",
        }
    )
