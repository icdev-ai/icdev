# Auto-grader for M06 Step 1: FastMCP Server

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    tools = mcp.list_tools()
    result1 = mcp.call_tool("scan_stig_control", {"control_id": "V-220706", "system_name": "Test"})
    result2 = mcp.call_tool("list_open_findings", {"system_name": "Test", "severity_filter": "CAT1"})
    result3 = mcp.call_tool("generate_poam_entry", {"finding_id": "V-220706", "system_name": "Test", "responsible_party": "ISSO"})
    for t in tools:
        print(f"Tool: {t['name']}")
    print(f"scan result: {result1}")
    print(f"findings: {result2['total_open']}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# Tool registration
assert isinstance(tools, list), f"list_tools() must return a list, got {type(tools)}"
assert len(tools) == 3, f"Expected 3 tools registered, got {len(tools)}"

tool_names = {t["name"] for t in tools}
assert "scan_stig_control" in tool_names, "scan_stig_control not registered"
assert "list_open_findings" in tool_names, "list_open_findings not registered"
assert "generate_poam_entry" in tool_names, "generate_poam_entry not registered"

# Schema auto-generation
for t in tools:
    assert "description" in t, f"Tool {t['name']} missing description"
    assert "inputSchema" in t, f"Tool {t['name']} missing inputSchema — infer from type hints"
    props = t["inputSchema"].get("properties", {})
    assert len(props) >= 1, f"Tool {t['name']} schema has no properties — inspect the signature"

# scan_stig_control: check schema has correct params
scan_tool = next(t for t in tools if t["name"] == "scan_stig_control")
props = scan_tool["inputSchema"]["properties"]
assert "control_id" in props, "scan_stig_control schema must include 'control_id' parameter"
assert "system_name" in props, "scan_stig_control schema must include 'system_name' parameter"

# Tool call results
assert result1.get("status") == "FAIL", f"V-220706 should FAIL, got {result1.get('status')}"
assert result1.get("severity") == "CAT1", f"V-220706 should be CAT1, got {result1.get('severity')}"

assert result2.get("total_open") == 1, \
    f"CAT1 filter should return 1 finding, got {result2.get('total_open')}"

assert isinstance(result3, str) and "POA&M" in result3, \
    "generate_poam_entry must return a string containing 'POA&M'"

assert len(output) > 20, "Print tool names and call results"

print(f"PASS: FastMCP decorator pattern working. {len(tools)} tools registered with auto-generated schemas.")
