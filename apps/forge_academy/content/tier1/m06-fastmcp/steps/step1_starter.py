
"""
Step 1: Build Your First MCP Server with FastMCP
Goal: Implement a FastMCP-style server using decorator pattern.
We rebuild the core mechanics without the external dependency.
"""

from typing import Callable, Any


class FastMCP:
    """Minimal FastMCP-style server — decorator-based tool registration."""

    def __init__(self, name: str):
        self.name = name
        self._tools: dict[str, dict] = {}
        self._handlers: dict[str, Callable] = {}

    def tool(self):
        """TODO: Implement the @mcp.tool() decorator.

        When applied to a function:
        1. Extract the function name as the tool name
        2. Extract the docstring as the tool description
        3. Inspect the function signature to build an inputSchema dict
           (each parameter → {"type": "string"} unless annotated otherwise)
        4. Register the tool in self._tools and self._handlers
        5. Return the original function unchanged (decorator should be transparent)

        Type annotation mapping: str→"string", int→"integer", float→"number",
                                  bool→"boolean", list→"array", dict→"object"
        """
        def decorator(func: Callable) -> Callable:
            # YOUR CODE HERE
            pass
            return func
        return decorator

    def list_tools(self) -> list:
        """Return list of registered tools (MCP tools/list response)."""
        return list(self._tools.values())

    def call_tool(self, name: str, arguments: dict) -> Any:
        """Call a registered tool by name with arguments."""
        if name not in self._handlers:
            raise ValueError(f"Tool '{name}' not registered")
        return self._handlers[name](**arguments)


# ── Tool implementations ─────────────────────────────────────────────────────

mcp = FastMCP("icdev-compliance-mcp")


@mcp.tool()
def scan_stig_control(control_id: str, system_name: str) -> dict:
    """Scan a specific STIG control and return compliance status."""
    findings = {
        "V-220706": {"status": "FAIL", "severity": "CAT1", "fix": "Set PermitRootLogin no"},
        "V-220707": {"status": "PASS", "severity": "CAT2", "fix": None},
        "V-220708": {"status": "FAIL", "severity": "CAT2", "fix": "Set MaxAuthTries 3"},
    }
    result = findings.get(control_id, {"status": "NOT_REVIEWED", "severity": "N/A", "fix": None})
    return {"control": control_id, "system": system_name, **result}


@mcp.tool()
def list_open_findings(system_name: str, severity_filter: str) -> dict:
    """List all open STIG findings for a system, optionally filtered by severity."""
    all_findings = [
        {"id": "V-220706", "severity": "CAT1", "status": "FAIL"},
        {"id": "V-220708", "severity": "CAT2", "status": "FAIL"},
        {"id": "V-220710", "severity": "CAT3", "status": "FAIL"},
    ]
    if severity_filter and severity_filter.upper() != "ALL":
        filtered = [f for f in all_findings if f["severity"] == severity_filter.upper()]
    else:
        filtered = all_findings
    return {
        "system": system_name,
        "total_open": len(filtered),
        "findings": filtered,
        "filter_applied": severity_filter or "none",
    }


@mcp.tool()
def generate_poam_entry(finding_id: str, system_name: str, responsible_party: str) -> str:
    """Generate a POA&M entry draft for a specific finding."""
    return (
        f"POA&M Entry — {finding_id}\n"
        f"System: {system_name}\n"
        f"Responsible Party: {responsible_party}\n"
        f"Scheduled Completion: 30 days (CAT I)\n"
        f"Milestones: 1) Identify affected systems  2) Apply fix  3) Validate and document"
    )


# Test
if __name__ == "__main__":
    print("=== Tool Discovery ===")
    tools = mcp.list_tools()
    print(f"Registered {len(tools)} tools:")
    for t in tools:
        params = list(t.get("inputSchema", {}).get("properties", {}).keys())
        print(f"  - {t['name']}({', '.join(params)}): {t['description'][:50]}")

    print("\n=== Tool Calls ===")
    result1 = mcp.call_tool("scan_stig_control", {"control_id": "V-220706", "system_name": "ICDEV-Prod"})
    print(f"scan_stig_control: {result1}")

    result2 = mcp.call_tool("list_open_findings", {"system_name": "ICDEV-Prod", "severity_filter": "CAT1"})
    print(f"list_open_findings (CAT1 only): {result2['total_open']} findings")
