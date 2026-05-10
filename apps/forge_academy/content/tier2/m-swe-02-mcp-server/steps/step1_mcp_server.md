# MCP Server Design — Build a Model Context Protocol Tool Registry

The Model Context Protocol (MCP) gives AI agents a standardized way to call tools. An MCP server exposes tools with typed schemas, handles calls, and returns structured responses. In this mission you'll build the core of an MCP tool registry — the layer that registers tools and dispatches calls.

## What You'll Build

A `MCPToolRegistry` that manages tool registration and dispatched calls:

```python
registry = MCPToolRegistry()

@registry.tool(name="get_compliance_status", description="Check control compliance status")
def get_compliance_status(control_id: str, system: str = "ICDEV-Prod") -> dict:
    return {"control": control_id, "status": "compliant", "system": system}

result = registry.call("get_compliance_status", {"control_id": "IA-2"})
# → {"result": {"control": "IA-2", "status": "compliant", "system": "ICDEV-Prod"}, "error": None}
```

## MCP Tool Schema

Each registered tool has a schema:
```python
{
    "name": "tool_name",
    "description": "What this tool does",
    "parameters": {
        "type": "object",
        "properties": {
            "param_name": {"type": "string", "description": "..."}
        },
        "required": ["param_name"]
    }
}
```

## Success Criteria

- `MCPToolRegistry.tool()` decorator registers a function as an MCP tool
- `MCPToolRegistry.list_tools()` returns all registered tool schemas
- `MCPToolRegistry.call()` dispatches a call to the right tool with kwargs
- Unknown tool names return `{"result": None, "error": "Tool 'name' not found"}`
- Exceptions in tool functions are caught and returned as `{"result": None, "error": "..."}`
- `generate_schema()` infers parameter schema from function annotations
