#!/usr/bin/env python3
# CUI // SP-CTI
"""MCP-A2A Bridge Server — exposes Unified MCP tools over HTTP (A2A protocol).

This bridge allows A2A agents to invoke MCP tools via standard HTTP JSON-RPC.
It wraps the UnifiedMCPServer's tool registry and exposes each tool as an A2A
skill endpoint.

Usage:
    python tools/mcp/a2a_bridge_server.py --port 9000

Endpoints:
    GET  /.well-known/agent.json  → Agent card with all MCP tools as skills
    POST /tasks/send              → JSON-RPC 2.0 task dispatch to MCP tools
    GET  /tasks/<id>             → Task status
    POST /tasks/<id>/cancel       → Cancel task
    GET  /health                  → Health check

The bridge is designed to run as a sidecar alongside the main dashboard or
as a standalone service in docker-compose.
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("mcp.a2a_bridge")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


try:
    from flask import Flask, jsonify, request
except ImportError:
    Flask = None  # Handled at runtime


class MCPA2ABridge:
    """Bridge between MCP tool registry and A2A HTTP protocol."""

    def __init__(
        self,
        host: str = "0.0.0.0",  # nosec B104 — bridge server binds all interfaces by design; override via constructor arg
        port: int = 9000,
        db_path: Optional[Path] = None,
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
        tls_ca: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.db_path = db_path or DB_PATH
        self.tls_cert = tls_cert
        self.tls_key = tls_key
        self.tls_ca = tls_ca
        self.name = "ICDEV™ MCP Gateway"
        self.description = "Unified MCP tool gateway exposed over A2A HTTP"
        self.version = "1.0.0"

        # In-memory task cache
        self._tasks: Dict[str, dict] = {}
        self._executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="mcp-bridge-")

        # Lazy-load MCP server to get tool registry
        self._mcp_server = None
        self._tool_registry: Dict[str, dict] = {}
        self._load_registry()

        if Flask is None:
            raise ImportError("Flask is required. Install with: pip install flask")

        self.app = Flask(self.name)
        self._register_routes()

    def _load_registry(self) -> None:
        """Load the MCP tool registry without starting the stdio server."""
        try:
            from tools.mcp.tool_registry import TOOL_REGISTRY
            self._tool_registry = TOOL_REGISTRY
            logger.info(f"Loaded {len(TOOL_REGISTRY)} tools from MCP registry")
        except Exception as exc:
            logger.warning(f"Failed to load MCP tool registry: {exc}")
            self._tool_registry = {}

    def _get_mcp_server(self):
        """Lazy-init the unified MCP server."""
        if self._mcp_server is None:
            from tools.mcp.unified_server import create_server
            self._mcp_server = create_server()
            logger.info("Initialized unified MCP server")
        return self._mcp_server

    def _tool_to_skill(self, tool_name: str, entry: dict) -> dict:
        """Convert an MCP tool registry entry to an A2A skill card."""
        return {
            "id": tool_name,
            "name": entry.get("description", tool_name).split(".")[0],  # First sentence as name
            "description": entry.get("description", f"MCP tool: {tool_name}"),
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        }

    def get_agent_card(self) -> dict:
        """Build the Agent Card JSON for /.well-known/agent.json."""
        skills = []
        for tool_name, entry in self._tool_registry.items():
            skills.append(self._tool_to_skill(tool_name, entry))

        return {
            "name": self.name,
            "description": self.description,
            "url": f"http://{self.host}:{self.port}",
            "version": self.version,
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
            },
            "authentication": {
                "schemes": ["api_key"],
            },
            "skills": skills,
        }

    def _register_routes(self) -> None:
        """Register Flask HTTP routes."""

        @self.app.route("/.well-known/agent.json", methods=["GET"])
        def agent_card():
            return jsonify(self.get_agent_card())

        @self.app.route("/tasks/send", methods=["POST"])
        def send_task():
            return self._handle_send_task()

        @self.app.route("/tasks/<task_id>", methods=["GET"])
        def get_task(task_id):
            return self._handle_get_task(task_id)

        @self.app.route("/tasks/<task_id>/cancel", methods=["POST"])
        def cancel_task(task_id):
            return self._handle_cancel_task(task_id)

        @self.app.route("/health", methods=["GET"])
        def health():
            return jsonify({
                "status": "healthy",
                "agent_id": "mcp-gateway",
                "tools_registered": len(self._tool_registry),
            })

    def _handle_send_task(self):
        """Handle POST /tasks/send (JSON-RPC 2.0 envelope)."""
        body = request.get_json(silent=True)
        if not body:
            return self._jsonrpc_error(None, -32700, "Parse error: invalid JSON")

        rpc_id = body.get("id")

        if body.get("jsonrpc") != "2.0":
            return self._jsonrpc_error(rpc_id, -32600, "Invalid JSON-RPC version")

        method = body.get("method")
        if method != "tasks/send":
            return self._jsonrpc_error(rpc_id, -32601, f"Unknown method: {method}")

        params = body.get("params", {})
        skill_id = params.get("skill_id")
        input_data = params.get("input_data", {})
        task_id = params.get("id", str(uuid.uuid4()))
        metadata = params.get("metadata", {})
        sync_mode = params.get("sync", False)

        if not skill_id:
            return self._jsonrpc_error(rpc_id, -32602, "Missing required param: skill_id")

        if skill_id not in self._tool_registry:
            return self._jsonrpc_error(rpc_id, -32602, f"Unknown skill/tool: {skill_id}")

        # Create task record
        task = {
            "id": task_id,
            "skill_id": skill_id,
            "status": "submitted",
            "input_data": input_data,
            "output_data": None,
            "metadata": metadata,
            "created_at": _utcnow_iso(),
            "updated_at": _utcnow_iso(),
            "history": [{"status": "submitted", "message": "Queued for processing", "timestamp": _utcnow_iso()}],
        }
        self._tasks[task_id] = task

        if sync_mode:
            self._execute_task_sync(task_id, skill_id, input_data)
        else:
            task["status"] = "working"
            task["history"].append({"status": "working", "message": "Processing async", "timestamp": _utcnow_iso()})
            self._executor.submit(self._execute_task_async, task_id, skill_id, input_data)

        return jsonify({"jsonrpc": "2.0", "id": rpc_id, "result": task})

    def _execute_task_sync(self, task_id: str, skill_id: str, input_data: dict) -> None:
        """Execute an MCP tool synchronously."""
        task = self._tasks[task_id]
        try:
            task["status"] = "working"
            task["updated_at"] = _utcnow_iso()
            task["history"].append({"status": "working", "message": "Invoking MCP tool", "timestamp": _utcnow_iso()})

            result = self._invoke_mcp_tool(skill_id, input_data)

            task["output_data"] = result
            task["status"] = "completed"
            task["updated_at"] = _utcnow_iso()
            task["history"].append({"status": "completed", "message": "Tool completed", "timestamp": _utcnow_iso()})
        except Exception as e:
            logger.exception(f"MCP tool invocation failed: {skill_id}")
            task["output_data"] = {"error": str(e)}
            task["status"] = "failed"
            task["updated_at"] = _utcnow_iso()
            task["history"].append({"status": "failed", "message": f"Error: {str(e)}", "timestamp": _utcnow_iso()})

    def _execute_task_async(self, task_id: str, skill_id: str, input_data: dict) -> None:
        """Execute an MCP tool asynchronously in a worker thread."""
        self._execute_task_sync(task_id, skill_id, input_data)

    def _invoke_mcp_tool(self, tool_name: str, args: dict) -> dict:
        """Invoke an MCP tool via the unified server."""
        mcp = self._get_mcp_server()
        tool_info = mcp._tools.get(tool_name)
        if not tool_info:
            return {"error": f"Tool not found: {tool_name}"}

        handler = tool_info["handler"]
        try:
            result = handler(args)
            # Normalize result
            if result is None:
                return {"status": "ok", "result": None}
            if isinstance(result, dict):
                return result
            return {"status": "ok", "result": result}
        except Exception as e:
            logger.exception(f"Tool handler error: {tool_name}")
            return {"error": str(e), "tool": tool_name}

    def _handle_get_task(self, task_id: str):
        """Handle GET /tasks/<task_id>."""
        task = self._tasks.get(task_id)
        if not task:
            return jsonify({"error": f"Task not found: {task_id}"}), 404
        return jsonify(task)

    def _handle_cancel_task(self, task_id: str):
        """Handle POST /tasks/<task_id>/cancel."""
        task = self._tasks.get(task_id)
        if not task:
            return jsonify({"error": f"Task not found: {task_id}"}), 404
        if task["status"] in ("completed", "failed", "canceled"):
            return jsonify({"error": f"Task already in terminal state: {task['status']}"}), 400
        task["status"] = "canceled"
        task["updated_at"] = _utcnow_iso()
        task["history"].append({"status": "canceled", "message": "Canceled by request", "timestamp": _utcnow_iso()})
        return jsonify(task)

    @staticmethod
    def _jsonrpc_error(rpc_id, code: int, message: str):
        return jsonify({
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": code, "message": message},
        }), 400 if code != -32700 else 422

    def run(self, debug: bool = False) -> None:
        ssl_ctx = None
        if self.tls_cert and self.tls_key:
            if os.path.exists(self.tls_cert) and os.path.exists(self.tls_key):
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_ctx.load_cert_chain(self.tls_cert, self.tls_key)
                if self.tls_ca and os.path.exists(self.tls_ca):
                    ssl_ctx.verify_mode = ssl.CERT_REQUIRED
                    ssl_ctx.load_verify_locations(self.tls_ca)
                    logger.info("Mutual TLS enabled (client cert required)")
                else:
                    logger.info("TLS enabled (server-side only)")
            else:
                logger.warning(f"TLS cert/key not found ({self.tls_cert}, {self.tls_key}). Starting without TLS.")

        logger.info(f"Starting MCP-A2A Bridge on {self.host}:{self.port}")
        logger.info(f"Exposing {len(self._tool_registry)} MCP tools as A2A skills")
        self.app.run(host=self.host, port=self.port, debug=debug, ssl_context=ssl_ctx)


def _utcnow_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP-A2A Bridge Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")  # nosec B104 — server binds all interfaces by design; override via --host
    parser.add_argument("--port", type=int, default=9000, help="Bind port")
    parser.add_argument("--tls-cert", help="TLS certificate path")
    parser.add_argument("--tls-key", help="TLS private key path")
    parser.add_argument("--tls-ca", help="TLS CA cert for mutual TLS")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    bridge = MCPA2ABridge(
        host=args.host,
        port=args.port,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        tls_ca=args.tls_ca,
    )
    bridge.run(debug=args.debug)


if __name__ == "__main__":
    main()
