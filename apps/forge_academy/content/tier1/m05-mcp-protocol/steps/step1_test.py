# Auto-grader for M05 Step 1: MCP Protocol

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    server = setup_compliance_server()
    client = MCPClient(server)

    tools = client.discover_tools()
    result = client.call_tool("check_compliance", {
        "system_id": "test-001",
        "control_ids": ["AC-2", "IA-2", "AC-17"],
    })

    print(f"Tools: {len(tools)}")
    print(f"Compliance: {result.get('compliance_rate')}")
    print(f"Non-compliant: {result.get('non_compliant')}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# discover_tools validation
assert tools is not None, "discover_tools() returned None — implement the method"
assert isinstance(tools, list), f"discover_tools() must return a list, got {type(tools)}"
assert len(tools) >= 1, "At least 1 tool should be discovered"

tool = tools[0]
assert "name" in tool, "Each tool must have a 'name' field"
assert "description" in tool, "Each tool must have a 'description' field"
assert tool["name"] == "check_compliance", f"Expected 'check_compliance' tool, got {tool['name']}"

# call_tool validation
assert result is not None, "call_tool() returned None — implement the method"
assert isinstance(result, dict), f"call_tool() must return a parsed dict, got {type(result)}"

assert "compliance_rate" in result, "Result must include 'compliance_rate'"
assert "non_compliant" in result, "Result must include 'non_compliant' list"
assert "system_id" in result, "Result must include 'system_id'"

# IA-2 and AC-17 should be non-compliant
non_compliant = result.get("non_compliant", [])
assert "IA-2" in non_compliant, f"IA-2 should be non-compliant. Got non_compliant: {non_compliant}"
assert "AC-17" in non_compliant, f"AC-17 should be non-compliant. Got non_compliant: {non_compliant}"

# AC-2 should be compliant (not in non_compliant list)
assert "AC-2" not in non_compliant, f"AC-2 should be compliant. Got non_compliant: {non_compliant}"

# Must print tool discovery info
assert len(output) > 20, "Print tool names during discover_tools() and call results"

print(f"PASS: MCP protocol implemented. Tool discovery + call working. Compliance check: {result.get('compliance_rate')}")
