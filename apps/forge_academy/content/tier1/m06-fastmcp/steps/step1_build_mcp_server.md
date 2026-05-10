# Build Your First MCP Server with FastMCP

FastMCP is a Python framework that removes all the boilerplate from building MCP servers. Where vanilla MCP requires writing JSON-RPC handlers, schema dicts, and transport logic, FastMCP reduces it to a decorator and a function signature.

## The entire server in 12 lines

```python
from fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def check_stig(control_id: str) -> dict:
    """Check STIG compliance status for a control."""
    return {"control": control_id, "status": "compliant"}

if __name__ == "__main__":
    mcp.run()  # stdio transport by default
```

FastMCP reads the function signature and docstring to auto-generate the MCP tool schema — no manual JSON writing required.

## How FastMCP differs from raw MCP

| Aspect | Raw MCP | FastMCP |
|--------|---------|---------|
| Schema definition | Manual JSON dict | Inferred from type hints |
| Tool registration | `server.register_tool(...)` | `@mcp.tool()` decorator |
| Transport setup | Boilerplate stdio/HTTP code | `mcp.run()` |
| Error handling | Manual JSON-RPC errors | Automatic exception → error response |
| Testing | Requires protocol client | Direct Python function calls |

## ICDEV integration

ICDEV wraps its compliance, RAG, and agent tools as FastMCP servers. When Claude Code connects to the `tools/mcp/` server, it discovers every registered tool automatically through the `tools/list` endpoint. You never have to tell the model what tools exist — it discovers them at session start.

## Your task

Implement a FastMCP-style compliance server using the same decorator pattern. Since we can't install FastMCP in the sandbox, you'll build the decorator machinery yourself — understanding *how* FastMCP works under the hood.
