---
ontology_id: icdev:mission:m-t3-03-write-a-goal-workflow:step:1
step_class: icdev:Lesson
---

# Write a Goal Workflow

Goals are the process definitions of ICDEV. A goal file tells the FORGE framework what to achieve, which tools to call, in what order, and what the output looks like. In this mission you'll write a goal workflow and validate it against a compact goal schema.

## What real goal files look like

Every workflow lives in `goals/` and is indexed in `goals/manifest.md` (check it before starting any
task). Real goals are Markdown prose, not a fixed header format. `goals/security_scan.md`, for example, is
organized as:

```
# Goal: Comprehensive Security Scanning
## Description
## Prerequisites
## Quality Gates
## Process
### Step 1: Run Static Application Security Testing (SAST)
### Step 2: Run Dependency Audit
...
## Success Criteria
## Edge Cases & Notes
## FORGE Layer Mapping
## Related Files
```

Tool calls appear inline in the steps as commands, e.g. `python tools/compliance/stig_checker.py ...`.
Goals are living documents, but the repo rule is: never create or modify a goal without permission.

## The compact schema this exercise validates

So that a validator has something regular to check, this exercise uses a compact form that carries
the same three facts every real goal states somewhere in its prose — tools, args, output:

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
# Tools: tools/compliance/stig_checker.py, tools/db/storage.py
```

**Args** — the YAML config file that controls behavior without editing the goal:
```
# Args: args/compliance_config.yaml
```

**Output** — where results land (DB table or file path):
```
# Output: DB table stig_findings
# Output: file reports/compliance_report.txt
```

## What You'll Build

A `GoalValidator` that parses a goal file and validates it against the compact schema:

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
4. Tool paths must follow `tools/<dir>/<name>.py` format (an exercise simplification — a few real tools sit one level deeper, e.g. `tools/compliance/xacta/xacta_export.py`)

## Success Criteria

- `parse_goal_fields()` extracts tools, args_file, output_type from goal content
- `parse_steps()` extracts numbered steps from `## Steps` sections
- `validate_tools()` checks each tool path against the `tools/path/name.py` regex
- `GoalValidator.validate()` returns `{"valid": bool, "issues": list, "parsed": dict}`
- Invalid goals return `valid=False` with descriptive issues list
- Writing a complete, valid goal produces `valid=True, issues=[]`
