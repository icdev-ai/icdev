from __future__ import annotations
"""
Tier 2 SWE/Architect Mission 2: MCP Server Design
Goal: Build an MCPToolRegistry that registers tools and dispatches calls.
"""

import inspect
from typing import Callable, Any


# ── Step 1: Schema Generator ──────────────────────────────────────────────────

PYTHON_TO_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def generate_schema(func: Callable) -> dict:
    """TODO: Generate a JSON Schema parameter definition from a function's annotations.

    For each parameter in the function signature (excluding 'self'):
    1. Get the annotation type name (e.g., "str", "int") using annotation.__name__
       If no annotation, use "string" as default
    2. Map to JSON type using PYTHON_TO_JSON_TYPE (default "string" if not in map)
    3. A parameter is "required" if it has no default value (inspect.Parameter.empty)

    Return:
    {
        "type": "object",
        "properties": {
            "param_name": {"type": "string"|"integer"|...},
            ...
        },
        "required": ["param_without_default", ...],
    }
    """
    # YOUR CODE HERE
    pass


# ── Step 2: MCPToolRegistry ───────────────────────────────────────────────────

class MCPToolRegistry:
    """Registers and dispatches MCP tool calls."""

    def __init__(self):
        self._tools: dict = {}  # name → {"func": callable, "schema": dict}

    def tool(self, name: str = None, description: str = ""):
        """TODO: Decorator that registers a function as an MCP tool.

        Usage:
            @registry.tool(name="my_tool", description="Does something")
            def my_tool(param: str) -> dict:
                ...

        If name is None, use the function's __name__.

        Store in self._tools[tool_name]:
        {
            "func": the original function,
            "name": tool_name,
            "description": description,
            "schema": generate_schema(func),
        }

        Return the original function unchanged (standard decorator pattern).
        """
        def decorator(func):
            # YOUR CODE HERE
            return func
        return decorator

    def list_tools(self) -> list[dict]:
        """TODO: Return a list of all registered tool schemas.

        For each tool in self._tools:
        {
            "name": tool_name,
            "description": description,
            "parameters": schema (from generate_schema),
        }

        Return list (order doesn't matter).
        """
        # YOUR CODE HERE
        pass

    def call(self, tool_name: str, arguments: dict = None) -> dict:
        """TODO: Call a registered tool with the given arguments.

        1. If tool_name not in self._tools:
           return {"result": None, "error": f"Tool '{tool_name}' not found"}
        2. Call self._tools[tool_name]["func"](**arguments)
        3. If successful: return {"result": result, "error": None}
        4. If any exception: return {"result": None, "error": str(exception)}

        Use arguments = {} if arguments is None.
        """
        # YOUR CODE HERE
        pass

    def get_schema(self, tool_name: str) -> dict | None:
        """TODO: Return the schema for a single tool, or None if not found."""
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    registry = MCPToolRegistry()

    @registry.tool(name="get_compliance_status", description="Check control compliance")
    def get_compliance_status(control_id: str, system: str = "ICDEV-Prod") -> dict:
        return {"control": control_id, "status": "compliant", "system": system}

    @registry.tool(name="list_findings", description="List open findings")
    def list_findings(system: str, severity: str = "all") -> list:
        return [{"id": "V-220706", "severity": "medium", "system": system}]

    print(f"Registered tools: {len(registry.list_tools())}")

    result = registry.call("get_compliance_status", {"control_id": "IA-2"})
    print(f"IA-2 status: {result}")

    err = registry.call("unknown_tool", {})
    print(f"Unknown tool error: {err}")

    schema = registry.get_schema("get_compliance_status")
    print(f"Schema: {schema}")
