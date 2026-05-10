# Auto-grader for M04 Step 1: The Agent Loop

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    result1 = run_agent("Look up STIG V-220706 and give me a summary of the findings.", max_steps=5)
    result2 = run_agent("Calculate the risk score for 3 open CAT1 findings.", max_steps=5)
    print(f"Result1: {result1}")
    print(f"Result2: {result2}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# Basic contract
assert result1 is not None, "run_agent() returned None — implement the loop"
assert isinstance(result1, str), f"run_agent() must return a string, got {type(result1)}"
assert len(result1) > 10, "Result too short — return the final tool output"

# Step 1 execution
assert "Step" in output or "step" in output or "calling" in output, \
    "Print each step as it executes: 'Step N: calling tool_name(args) → result'"

# The STIG lookup should have been called
assert "V-220706" in output or "ssh" in output.lower() or "permitrootlogin" in output.lower(), \
    "Agent should call stig_lookup for the STIG V-220706 task — check decide_tool routing"

# The result should include summary content
assert any(kw in result1.lower() for kw in ["finding", "cat", "summary", "critical", "stig", "ssh"]), \
    f"Result should contain security findings content, got: {result1[:100]}"

# Risk calculation path
assert result2 is not None, "run_agent() returned None for risk calculation task"
risk_hit = any(kw in result2.lower() for kw in ["risk", "score", "critical", "high", "cat1", "medium"])
assert risk_hit, f"Risk calculation result should reference risk level, got: {result2[:100]}"

print(f"PASS: Agent loop working. Two tasks executed successfully. Multi-step reasoning verified.")
