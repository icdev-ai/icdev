#!/usr/bin/env python3
# CUI // SP-CTI
"""MCP Server for the Remote Command Gateway (Phase 28).

Provides 5 MCP tools for managing remote command access:
    1. bind_user       — Initiate or complete a user binding ceremony
    2. list_bindings   — List active/pending/revoked bindings
    3. revoke_binding  — Revoke an active binding
    4. send_command    — Execute an ICDEV command as if from a channel
    5. gateway_status  — Show gateway health, active channels, recent commands

Transport: stdio (Claude Code integration)
"""

import json
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.mcp.gateway")

# MCP protocol helpers (reuse pattern from other MCP servers)
def _read_message():
    """Read a JSON-RPC message from stdin."""
    headers = {}
    while True:
        line = sys.stdin.readline()
        if not line or line.strip() == "":
            break
        if ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip()] = val.strip()
    content_length = int(headers.get("Content-Length", 0))
    if content_length > 0:
        body = sys.stdin.read(content_length)
        return json.loads(body)
    return None


def _write_message(msg):
    """Write a JSON-RPC message to stdout."""
    body = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
    sys.stdout.flush()


def _result(id, result):
    """Build a JSON-RPC result."""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _error(id, code, message):
    """Build a JSON-RPC error."""
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Tool Handlers
# ---------------------------------------------------------------------------

def handle_bind_user(params):
    """Initiate or complete a user binding ceremony."""
    from tools.gateway.user_binder import (
        create_challenge, verify_challenge, provision_binding
    )

    action = params.get("action", "initiate")

    if action == "initiate":
        channel = params.get("channel", "")
        channel_user_id = params.get("channel_user_id", "")
        if not channel or not channel_user_id:
            return {"error": "channel and channel_user_id required"}
        code = create_challenge(channel, channel_user_id)
        return {"challenge_code": code, "ttl_minutes": 10}

    elif action == "verify":
        code = params.get("challenge_code", "")
        icdev_user_id = params.get("icdev_user_id", "")
        tenant_id = params.get("tenant_id", "")
        return verify_challenge(code, icdev_user_id, tenant_id)

    elif action == "provision":
        return provision_binding(
            channel=params.get("channel", ""),
            channel_user_id=params.get("channel_user_id", ""),
            icdev_user_id=params.get("icdev_user_id", ""),
            tenant_id=params.get("tenant_id", ""),
        )

    return {"error": f"Unknown action: {action}"}


def handle_list_bindings(params):
    """List remote user bindings."""
    from tools.gateway.user_binder import list_bindings
    channel = params.get("channel", "")
    status = params.get("status", "")
    bindings = list_bindings(channel=channel, status=status)
    return {"bindings": bindings, "count": len(bindings)}


def handle_revoke_binding(params):
    """Revoke an active binding."""
    from tools.gateway.user_binder import revoke_binding
    binding_id = params.get("binding_id", "")
    reason = params.get("reason", "")
    if not binding_id:
        return {"error": "binding_id required"}
    ok = revoke_binding(binding_id, reason)
    return {"success": ok}


def handle_send_command(params):
    """Execute an ICDEV command as if from a channel (for testing/admin)."""
    import yaml
    from tools.gateway.event_envelope import CommandEnvelope, parse_command_text
    from tools.gateway.command_router import execute_command

    command_text = params.get("command", "")
    project_id = params.get("project_id", "")
    channel = params.get("channel", "internal_chat")

    if not command_text:
        return {"error": "command required"}

    command, args = parse_command_text(command_text)
    if project_id:
        args["project_id"] = project_id

    envelope = CommandEnvelope(
        channel=channel,
        channel_user_id="mcp-user",
        raw_text=command_text,
        command=command,
        args=args,
        project_id=project_id,
        icdev_user_id="mcp-admin",
        binding_id="mcp-direct",
    )

    config_path = BASE_DIR / "args" / "remote_gateway_config.yaml"
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

    channel_config = config.get("channels", {}).get(channel, {"max_il": "IL5"})
    result = execute_command(envelope, channel_config, config)

    return {
        "success": result["success"],
        "output": result["output"],
        "filtered": result["filtered"],
        "execution_time_ms": result["execution_time_ms"],
        "audit_id": result["audit_id"],
    }


def handle_gateway_status(params):
    """Show gateway status: active channels, recent commands."""
    import sqlite3
    import yaml

    config_path = BASE_DIR / "args" / "remote_gateway_config.yaml"
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

    env_mode = config.get("environment", {}).get("mode", "connected")
    channels = config.get("channels", {})

    # Get active channels
    active = []
    for name, ch_config in channels.items():
        enabled = ch_config.get("enabled", False)
        req_internet = ch_config.get("requires_internet", False)
        available = enabled and not (env_mode == "air_gapped" and req_internet)
        active.append({
            "channel": name,
            "enabled": enabled,
            "available": available,
            "max_il": ch_config.get("max_il", "IL4"),
        })

    # Recent commands
    recent = []
    db_path = BASE_DIR / "data" / "icdev.db"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, channel, raw_command, execution_status, "
            "execution_time_ms, created_at FROM remote_command_log "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        recent = [dict(r) for r in rows]
        conn.close()
    except Exception:
        pass

    return {
        "environment_mode": env_mode,
        "channels": active,
        "recent_commands": recent,
    }


# ---------------------------------------------------------------------------
# MCP Server Main Loop
# ---------------------------------------------------------------------------

TOOLS = {
    "bind_user": {
        "description": "Initiate or complete a user binding ceremony for remote command access",
        "handler": handle_bind_user,
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["initiate", "verify", "provision"]},
                "channel": {"type": "string"},
                "channel_user_id": {"type": "string"},
                "challenge_code": {"type": "string"},
                "icdev_user_id": {"type": "string"},
                "tenant_id": {"type": "string"},
            },
        },
    },
    "list_bindings": {
        "description": "List remote user bindings (active, pending, revoked)",
        "handler": handle_list_bindings,
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "status": {"type": "string"},
            },
        },
    },
    "revoke_binding": {
        "description": "Revoke an active remote user binding",
        "handler": handle_revoke_binding,
        "inputSchema": {
            "type": "object",
            "properties": {
                "binding_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["binding_id"],
        },
    },
    "send_command": {
        "description": "Execute an ICDEV command via the remote gateway (for testing/admin)",
        "handler": handle_send_command,
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "project_id": {"type": "string"},
                "channel": {"type": "string"},
            },
            "required": ["command"],
        },
    },
    "gateway_status": {
        "description": "Show remote gateway status: active channels, environment mode, recent commands",
        "handler": handle_gateway_status,
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
}


def main():
    """MCP stdio server main loop."""
    logging.basicConfig(level=logging.WARNING)

    while True:
        msg = _read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            _write_message(_result(id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "icdev-gateway",
                    "version": "1.0.0",
                },
            }))

        elif method == "tools/list":
            tool_list = []
            for name, info in TOOLS.items():
                tool_list.append({
                    "name": name,
                    "description": info["description"],
                    "inputSchema": info["inputSchema"],
                })
            _write_message(_result(id, {"tools": tool_list}))

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if tool_name in TOOLS:
                try:
                    result = TOOLS[tool_name]["handler"](tool_args)
                    _write_message(_result(id, {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
                    }))
                except Exception as e:
                    _write_message(_error(id, -32000, str(e)))
            else:
                _write_message(_error(id, -32601, f"Unknown tool: {tool_name}"))

        elif method == "notifications/initialized":
            pass  # Acknowledgment, no response needed

        else:
            if id is not None:
                _write_message(_error(id, -32601, f"Unknown method: {method}"))


if __name__ == "__main__":
    main()
