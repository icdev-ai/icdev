---
ontology_id: icdev:mission:m-t3-03-write-a-goal-workflow:step:1
step_class: icdev:Lesson
---

# Write a Goal Workflow

Goals are the process definitions of ICDEV. A goal file tells the FORGE framework what to achieve, which tools to call, in what order, and what the output looks like. In this mission you'll write a real goal workflow and validate it against the FORGE schema.

## Goal File Structure

Every goal in `goals/` follows this pattern:

```
# Goal Name
# Tools: tools/path/tool1.py, tools/path/tool2.py
# Args: args/config.yaml
# Output: DB table table_name | file path/to/output

Short description of what this goal achieves.

## Steps
1. step_description — tool_name (arg1, arg2)
2. step_description — tool_name (result_from_step_1)

## Expected Output
Describe what a successful run produces.
```

## The Three Goal Fields

**Tools** — the Python scripts this goal orchestrates, in order of first use:
```
# Tools: tools/compliance/scanner.py, tools/db/storage.py
```

**Args** — the YAML config file that controls behavior without editing the goal:
```
# Args: args/compliance_config.yaml
```

**Output** — where results land (DB table or file path):
```
# Output: DB table audit_findings
# Output: file reports/compliance_report.txt
```

## What You'll Build

A `GoalValidator` that parses a goal file and validates it against the FORGE schema:

```python
validator = GoalValidator()
result = validator.validate(goal_content)
# → {"valid": True, "issues": [], "parsed": {"tools": [...], "steps": [...], "output_type": "db"}}
```

## Validation Rules

A valid goal must have:
1. At least one `# Tools:` line with ≥1 tool path
2. At least one `## Steps` section with ≥1 numbered step
3. An `# Output:` line (either `DB table` or `file`)
4. Tool paths must follow `tools/<dir>/<name>.py` format

## Success Criteria

- `parse_goal_fields()` extracts tools, args_file, output_type from goal content
- `parse_steps()` extracts numbered steps from `## Steps` sections
- `validate_tools()` checks each tool path against the `tools/path/name.py` regex
- `GoalValidator.validate()` returns `{"valid": bool, "issues": list, "parsed": dict}`
- Invalid goals return `valid=False` with descriptive issues list
- Writing a complete, valid goal produces `valid=True, issues=[]`
