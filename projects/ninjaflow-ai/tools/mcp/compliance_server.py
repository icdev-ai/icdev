#!/usr/bin/env python3
# CUI // SP-CTI
"""MCP Server: compliance_server for ninjaflow-ai

Provides tool-calling interface for Claude Code integration.
Transport: stdio
"""

import json
import sys
import logging

logger = logging.getLogger("ninjaflow-ai.mcp.compliance_server")


def handle_request(request: dict) -> dict:
    """Handle incoming MCP JSON-RPC request."""
    method = request.get("method", "")
    params = request.get("params", {})
    request_id = request.get("id")

    # Tool dispatch based on method
    handlers = {}  # Populated by tool registration

    handler = handlers.get(method)
    if handler:
        try:
            result = handler(params)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as e:
            return {
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32603, "message": str(e)},
            }

    return {
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    """Run MCP server in stdio mode."""
    logger.info("Starting compliance_server MCP server for ninjaflow-ai")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            error = {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            sys.stdout.write(json.dumps(error) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
