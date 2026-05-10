# Auto-grader for T3 M3 Step 1: Write a Goal Workflow — GoalValidator

# ── Test: parse_goal_fields ───────────────────────────────────────────────────

sample = """
# Tools: tools/compliance/scanner.py, tools/db/storage.py
# Args: args/compliance_config.yaml
# Output: DB table audit_findings
"""

fields = parse_goal_fields(sample)
assert fields is not None, "parse_goal_fields() returned None"
assert isinstance(fields, dict), f"parse_goal_fields() must return dict, got {type(fields)}"
assert "tools" in fields, "Result must have 'tools' key"
assert "args_file" in fields, "Result must have 'args_file' key"
assert "output_type" in fields, "Result must have 'output_type' key"

assert len(fields["tools"]) == 2, \
    f"Should find 2 tools, got {len(fields['tools'])}: {fields['tools']}"
assert any("scanner" in t for t in fields["tools"]), "scanner.py should be in tools"
assert any("storage" in t for t in fields["tools"]), "storage.py should be in tools"
assert fields["args_file"] is not None, "Should find args_file"
assert "compliance_config" in fields["args_file"], \
    f"args_file should contain 'compliance_config', got {fields['args_file']}"
assert fields["output_type"] == "db", \
    f"'DB table' → output_type='db', got '{fields['output_type']}'"

# File output type
file_sample = "# Output: file reports/output.txt\n# Tools: tools/memory/memory_write.py\n"
file_fields = parse_goal_fields(file_sample)
assert file_fields["output_type"] == "file", \
    f"'file' in content → output_type='file', got '{file_fields['output_type']}'"

# No tools
notool_sample = "# Output: DB table results\nNo tools here."
notool_fields = parse_goal_fields(notool_sample)
assert isinstance(notool_fields["tools"], list), "tools must be a list even when empty"
assert len(notool_fields["tools"]) == 0, "No tools in content → empty list"

# No args
noargs_sample = "# Tools: tools/scan/scanner.py\n# Output: DB table t\n"
noargs_fields = parse_goal_fields(noargs_sample)
assert noargs_fields["args_file"] is None, \
    f"No args/ path → args_file=None, got '{noargs_fields['args_file']}'"

# ── Test: parse_steps ────────────────────────────────────────────────────────

steps_content = """
## Steps
1. Initialize scanner — scanner.py (target, controls)
2. Run STIG checks — scanner.py (benchmark)
3. Store findings — storage.py (results)

## Expected Output
Rows inserted into audit_findings.
"""

steps = parse_steps(steps_content)
assert steps is not None, "parse_steps() returned None"
assert isinstance(steps, list), f"parse_steps() must return list, got {type(steps)}"
assert len(steps) == 3, f"Should find 3 steps, got {len(steps)}: {steps}"
assert any("scanner" in s.lower() or "initialize" in s.lower() for s in steps), \
    f"First step should mention scanner or initialize, got: {steps}"

# No steps → empty list
nosteps = parse_steps("# Tools: tools/x/y.py\nNo numbered steps here.\n")
assert isinstance(nosteps, list), "parse_steps() must return list for content with no steps"
assert len(nosteps) == 0, f"No numbered steps → [], got {nosteps}"

# ── Test: validate_tools ──────────────────────────────────────────────────────

# Valid paths
valid_paths = ["tools/compliance/scanner.py", "tools/db/storage.py"]
issues_ok = validate_tools(valid_paths)
assert isinstance(issues_ok, list), "validate_tools() must return list"
assert len(issues_ok) == 0, f"Valid paths should have no issues, got: {issues_ok}"

# Invalid paths
bad_paths = ["scanner.py", "tools/run_it.sh", "tools/compliance/scanner.py"]
issues_bad = validate_tools(bad_paths)
assert len(issues_bad) == 2, \
    f"2 invalid paths should produce 2 issues, got {len(issues_bad)}: {issues_bad}"
assert any("scanner.py" in i for i in issues_bad), \
    f"Issue should mention bare 'scanner.py', got: {issues_bad}"

# ── Test: GoalValidator.validate() — valid goal ───────────────────────────────

validator = GoalValidator()
r_valid = validator.validate(VALID_GOAL)
assert r_valid is not None, "validate() returned None"
assert isinstance(r_valid, dict), f"validate() must return dict"
assert "valid" in r_valid, "Result must have 'valid' key"
assert "issues" in r_valid, "Result must have 'issues' key"
assert "parsed" in r_valid, "Result must have 'parsed' key"

assert r_valid["valid"] is True, \
    f"VALID_GOAL should validate, got valid=False, issues={r_valid['issues']}"
assert len(r_valid["issues"]) == 0, \
    f"Valid goal should have no issues, got: {r_valid['issues']}"

parsed = r_valid["parsed"]
assert parsed.get("tool_count") >= 1, "valid goal must have ≥1 tool"
assert parsed.get("step_count") >= 1, "valid goal must have ≥1 step"
assert parsed.get("output_type") in ("db", "file"), "valid goal must have known output_type"

# ── Test: GoalValidator.validate() — invalid: no tools ───────────────────────

r_notools = validator.validate(INVALID_GOAL_NO_TOOLS)
assert r_notools["valid"] is False, "No-tools goal should be invalid"
assert len(r_notools["issues"]) >= 1, "No-tools goal should have ≥1 issue"
assert any("tool" in i.lower() for i in r_notools["issues"]), \
    f"Issues should mention 'tool', got: {r_notools['issues']}"

# ── Test: GoalValidator.validate() — invalid: no steps ───────────────────────

r_nosteps = validator.validate(INVALID_GOAL_NO_STEPS)
assert r_nosteps["valid"] is False, "No-steps goal should be invalid"
assert any("step" in i.lower() for i in r_nosteps["issues"]), \
    f"Issues should mention 'step', got: {r_nosteps['issues']}"

# ── Test: GoalValidator.validate() — invalid: bad tool paths ─────────────────

r_bad = validator.validate(INVALID_GOAL_BAD_PATH)
assert r_bad["valid"] is False, "Bad-path goal should be invalid"
assert len(r_bad["issues"]) >= 1, "Bad paths should produce ≥1 issue"

print("PASS: GoalValidator complete. parse_goal_fields + parse_steps + validate_tools + GoalValidator.validate all verified.")
