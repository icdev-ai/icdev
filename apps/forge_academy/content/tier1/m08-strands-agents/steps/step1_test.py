# Auto-grader for M08 Step 1: Amazon Strands Agents

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    # Verify @tool decorator registered tools
    assert "scan_system" in _REGISTERED_TOOLS, "@tool decorator did not register scan_system"
    assert "create_remediation_plan" in _REGISTERED_TOOLS, "@tool did not register create_remediation_plan"
    assert "notify_isso" in _REGISTERED_TOOLS, "@tool did not register notify_isso"

    agent = StrandsAgent("You are a compliance analyst.")
    result = agent.run("Run a full compliance assessment for ICDEV-Prod and create a remediation plan.")

    if result:
        print(f"Tools called: {result.get('tools_called')}")
        print(f"Steps: {result.get('steps')}")
        for r in result.get('results', []):
            print(f"Result: {str(r)[:60]}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# @tool decorator validation
assert len(_REGISTERED_TOOLS) == 3, f"Expected 3 tools registered, got {len(_REGISTERED_TOOLS)}"

for tool_name, tool_def in _REGISTERED_TOOLS.items():
    assert "description" in tool_def, f"Tool {tool_name} missing description (from docstring)"
    assert "inputSchema" in tool_def, f"Tool {tool_name} missing inputSchema"
    props = tool_def["inputSchema"].get("properties", {})
    assert len(props) >= 1, f"Tool {tool_name} inputSchema has no properties — inspect signature"

# scan_system schema
scan_def = _REGISTERED_TOOLS["scan_system"]
props = scan_def["inputSchema"]["properties"]
assert "system_name" in props, "scan_system schema missing 'system_name'"
assert "scan_depth" in props, "scan_system schema missing 'scan_depth'"

# Agent run validation
assert result is not None, "agent.run() returned None — implement the run() method"
assert isinstance(result, dict), f"agent.run() must return a dict, got {type(result)}"

required_keys = {"task", "tools_called", "results", "steps"}
missing = required_keys - set(result.keys())
assert not missing, f"Result missing keys: {missing}"

tools_called = result.get("tools_called", [])
assert len(tools_called) >= 2, f"Expected ≥2 tool calls, got {len(tools_called)}: {tools_called}"
assert "scan_system" in tools_called, "scan_system must be called for assessment task"
assert "create_remediation_plan" in tools_called, "create_remediation_plan must be called"

results_list = result.get("results", [])
assert len(results_list) == len(tools_called), "results list length must match tools_called"

# scan result
scan_result = results_list[0] if results_list else {}
assert isinstance(scan_result, dict), "scan_system result must be a dict"
assert scan_result.get("cat1_findings") == 2, f"ICDEV-Prod full scan should find 2 CAT1, got {scan_result.get('cat1_findings')}"

assert "→ Calling" in output or "calling" in output.lower(), \
    "Print each step: '→ Calling <tool_name>(<args>)'"

print(f"PASS: Strands @tool decorator working. {len(tools_called)} tools executed by agent.")
