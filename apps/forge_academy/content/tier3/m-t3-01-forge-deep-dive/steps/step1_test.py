
# Auto-grader for T3 M1 Step 1: FORGE Goal Tracer

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    tracer = GoalTracer()
    result1 = tracer.trace("compliance_scan")
    result2 = tracer.trace("memory_write")
    result_missing = tracer.trace("nonexistent_goal_xyz")
    batch = tracer.trace_multiple(["compliance_scan", "memory_write", "nonexistent_goal_xyz"])

    print(f"compliance_scan tools: {result1.get('tool_count')}")
    print(f"memory_write tools: {result2.get('tool_count')}")
    print(f"missing goal has error: {'error' in result_missing}")
    print(f"batch count: {len(batch)}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# parse_goal_file tests
sample_content = """
# Goal
# Tools: tools/compliance/scanner.py, tools/db/storage.py
# Args: args/compliance_config.yaml
# Output: DB table audit_findings
"""
parsed = parse_goal_file(sample_content)
assert parsed is not None, "parse_goal_file() returned None"
assert isinstance(parsed, dict), f"parse_goal_file() must return dict"
assert "tools" in parsed, "parsed must have 'tools'"
assert "args_file" in parsed, "parsed must have 'args_file'"
assert "output_type" in parsed, "parsed must have 'output_type'"
assert len(parsed["tools"]) >= 1, f"Should find ≥1 tool in sample, got {parsed['tools']}"
assert any("scanner" in t or "storage" in t or "tools/" in t for t in parsed["tools"]), \
    f"Expected tool paths, got: {parsed['tools']}"
assert parsed["args_file"] is not None, "Should find args file in sample"
assert "compliance" in parsed["args_file"] or "args/" in parsed["args_file"], \
    f"Args file should be an args/ path, got: {parsed['args_file']}"
assert parsed["output_type"] == "db", \
    f"'DB table' in content → output_type='db', got '{parsed['output_type']}'"

file_content = "# Output: file logs/output.txt\n# Tools: tools/memory/memory_write.py\n"
file_parsed = parse_goal_file(file_content)
assert file_parsed["output_type"] == "file", \
    f"'file' in content → output_type='file', got '{file_parsed['output_type']}'"

# resolve_tools tests
from pathlib import Path
test_tools = ["tools/compliance/scanner.py", "tools/nonexistent_tool_xyz.py"]
resolved = resolve_tools(test_tools, Path("tools"))
assert resolved is not None, "resolve_tools() returned None"
assert isinstance(resolved, list), f"resolve_tools() must return list"
assert len(resolved) == len(test_tools), f"Must resolve all {len(test_tools)} tools"
for r in resolved:
    assert "path" in r and "exists" in r and "name" in r, \
        f"Each resolved tool must have path/exists/name, got: {r}"
assert isinstance(resolved[0]["exists"], bool), "exists must be bool"
# nonexistent tool must exist=False
nonexistent = [r for r in resolved if "nonexistent" in r["path"]]
assert nonexistent[0]["exists"] is False, "nonexistent tool should have exists=False"

# GoalTracer.trace tests
assert result1 is not None, "trace('compliance_scan') returned None"
assert result1.get("goal") == "compliance_scan", f"goal field should be 'compliance_scan'"
assert "tools" in result1, "trace result must have 'tools'"
assert "tool_count" in result1, "trace result must have 'tool_count'"
assert "output_type" in result1, "trace result must have 'output_type'"
assert result1.get("tool_count") >= 1, "compliance_scan should have ≥1 tool"
assert result1.get("output_type") == "db", \
    f"compliance_scan output_type should be 'db', got {result1.get('output_type')}"

assert result_missing.get("error") or result_missing.get("tools") == [], \
    "Nonexistent goal should return error or empty tools"

# trace_multiple tests
assert isinstance(batch, list), "trace_multiple must return list"
assert len(batch) >= 2, f"Should successfully trace 2 goals, got {len(batch)}"
goal_names_in_batch = [r["goal"] for r in batch]
assert "compliance_scan" in goal_names_in_batch, "compliance_scan should be in batch"
assert "memory_write" in goal_names_in_batch, "memory_write should be in batch"
# Error goals should be excluded
assert all("error" not in r for r in batch), \
    "trace_multiple should exclude error results"

assert len(output) > 20, "Print trace results in main block"

print("PASS: FORGE Goal Tracer complete. parse_goal_file + resolve_tools + GoalTracer.trace all verified.")
