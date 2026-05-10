"""
Step 1: What is MCP?
Goal: Implement a minimal MCP-style server and client using Python dicts.
This simulates the MCP protocol flow without external dependencies.
"""

import json
from typing import Any


# ── MCP Server (the tool provider) ──────────────────────────────────────────

class MCPServer:
    """Minimal MCP server — registers tools and handles JSON-RPC style calls."""

    def __init__(self, name: str):
        self.name = name
        self._tools: dict[str, dict] = {}
        self._handlers: dict[str, callable] = {}

    def register_tool(self, name: str, description: str, schema: dict, handler: callable):
        """Register a tool that clients can discover and call."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": schema,
        }
        self._handlers[name] = handler

    def handle_request(self, request: dict) -> dict:
        """Process an MCP JSON-RPC request. Returns a response dict."""
        method = request.get("method", "")

        if method == "tools/list":
            return {"tools": list(self._tools.values())}

        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name")
            args = params.get("arguments", {})

            if tool_name not in self._handlers:
                return {"error": f"Tool '{tool_name}' not found"}

            try:
                result = self._handlers[tool_name](**args)
                return {"content": [{"type": "text", "text": json.dumps(result)}]}
            except Exception as e:
                return {"error": str(e)}

        return {"error": f"Unknown method: {method}"}


# ── Tool implementations ─────────────────────────────────────────────────────

def check_compliance(system_id: str, control_ids: list) -> dict:
    """Check compliance status for given control IDs."""
    CONTROL_STATUS = {
        "AC-2": {"status": "compliant", "evidence": "Account management procedures documented"},
        "AC-17": {"status": "non-compliant", "evidence": "Remote access MFA not enforced"},
        "AU-2": {"status": "compliant", "evidence": "Audit events configured per baseline"},
        "IA-2": {"status": "non-compliant", "evidence": "MFA not implemented for privileged accounts"},
        "SC-28": {"status": "compliant", "evidence": "Data-at-rest encryption enabled"},
    }
    results = {}
    for cid in control_ids:
        results[cid] = CONTROL_STATUS.get(cid, {"status": "unknown", "evidence": "Not assessed"})
    non_compliant = [k for k, v in results.items() if v["status"] == "non-compliant"]
    return {
        "system_id": system_id,
        "controls_checked": len(control_ids),
        "non_compliant": non_compliant,
        "compliance_rate": f"{(1 - len(non_compliant)/max(len(control_ids),1))*100:.0f}%",
        "details": results,
    }


# ── MCP Client ───────────────────────────────────────────────────────────────

class MCPClient:
    """Minimal MCP client — discovers tools and calls them."""

    def __init__(self, server: MCPServer):
        self._server = server
        self._available_tools: list[dict] = []

    def discover_tools(self) -> list[dict]:
        """TODO: Call the server's tools/list method and store the result.

        Return the list of available tools.
        Print each tool name and description.
        """
        # YOUR CODE HERE
        pass

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """TODO: Call a specific tool on the server using tools/call.

        Build the proper request dict and send it to the server.
        Parse the response and return the parsed content.
        Print: "Calling {tool_name} with {arguments}"
        """
        # YOUR CODE HERE
        pass


# ── Wire it together ─────────────────────────────────────────────────────────

def setup_compliance_server() -> MCPServer:
    """Create and configure the compliance MCP server."""
    server = MCPServer("icdev-compliance-server")
    server.register_tool(
        name="check_compliance",
        description="Check NIST 800-53 compliance status for a system's controls",
        schema={
            "type": "object",
            "properties": {
                "system_id": {"type": "string", "description": "System identifier (e.g. 'sys-001')"},
                "control_ids": {"type": "array", "items": {"type": "string"},
                                "description": "List of control IDs to check (e.g. ['AC-2', 'IA-2'])"},
            },
            "required": ["system_id", "control_ids"],
        },
        handler=check_compliance,
    )
    return server


# Test
if __name__ == "__main__":
    server = setup_compliance_server()
    client = MCPClient(server)

    print("=== Tool Discovery ===")
    tools = client.discover_tools()
    print(f"Found {len(tools)} tool(s)")

    print("\n=== Tool Call ===")
    result = client.call_tool("check_compliance", {
        "system_id": "icdev-prod-001",
        "control_ids": ["AC-2", "IA-2", "AC-17", "AU-2"],
    })
    print(f"Compliance rate: {result.get('compliance_rate')}")
    print(f"Non-compliant: {result.get('non_compliant')}")
