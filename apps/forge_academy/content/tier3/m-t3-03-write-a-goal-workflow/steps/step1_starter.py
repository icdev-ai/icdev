
"""
Tier 3 Mission 3: Write a Goal Workflow
Goal: Build a GoalValidator that parses and validates ICDEV goal files.

FORGE Goal Contract:
  - Must have ≥1 tool reference (tools/<dir>/<name>.py)
  - Must have a ## Steps section with ≥1 numbered step
  - Must have an # Output: line (DB table or file)
  - Tool paths follow: tools/category/name.py format
"""

import re

# ── Sample Goal Content ───────────────────────────────────────────────────────

VALID_GOAL = """
# Compliance Evidence Goal
# Tools: tools/compliance/scanner.py, tools/db/storage.py
# Args: args/compliance_config.yaml
# Output: DB table audit_findings

Run a STIG compliance scan and store evidence in the audit database.

## Steps
1. Initialize scanner — scanner.py (target_system, control_ids)
2. Run STIG checks — scanner.py (xccdf_benchmark)
3. Persist findings — storage.py (scan_results, table="audit_findings")

## Expected Output
Rows inserted into audit_findings table with control_id, status, and evidence fields.
"""

INVALID_GOAL_NO_TOOLS = """
# My Goal
# Output: DB table results

## Steps
1. Do something

A goal with no tool references.
"""

INVALID_GOAL_NO_STEPS = """
# My Goal
# Tools: tools/compliance/scanner.py
# Output: file reports/output.txt

No steps section here.
"""

INVALID_GOAL_BAD_PATH = """
# My Goal
# Tools: scanner.py, run_it.sh
# Output: DB table results

## Steps
1. Scan — scanner.py

Tools without proper tools/ prefix paths.
"""


# ── Step 1: Field Parser ──────────────────────────────────────────────────────

def parse_goal_fields(content: str) -> dict:
    """TODO: Extract header fields from a goal file.

    Extract:
    1. tools: all paths matching r'tools/[\w/]+\.py' found anywhere in content
    2. args_file: first path matching r'args/[\w_/]+\.yaml' (or None)
    3. output_type: "db" if "DB table" in content, "file" if "file " in content, else "unknown"

    Return:
    {
        "tools": [list of tool paths],
        "args_file": "args/..." or None,
        "output_type": "db" | "file" | "unknown",
    }
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Step Parser ───────────────────────────────────────────────────────

def parse_steps(content: str) -> list[str]:
    """TODO: Extract numbered steps from a goal file.

    Find lines that look like numbered steps: start with a digit, period, space.
    Pattern: r'^\s*\d+\.\s+(.+)$' (multiline)

    Return list of step description strings (the part after "N. ").
    Return empty list if no steps found.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: Tool Validator ────────────────────────────────────────────────────

def validate_tools(tool_paths: list[str]) -> list[str]:
    """TODO: Validate that tool paths follow the FORGE convention.

    A valid tool path matches: r'^tools/[\w]+/[\w]+\.py$'
    (tools/category/name.py — exactly two path segments under tools/)

    For each path that does NOT match, add an issue string:
        f"Invalid tool path: '{path}' — must be tools/<category>/<name>.py"

    Return list of issue strings (empty list if all paths are valid).
    """
    # YOUR CODE HERE
    pass


# ── Step 4: GoalValidator ─────────────────────────────────────────────────────

class GoalValidator:
    """Validates ICDEV goal files against the FORGE schema."""

    def validate(self, content: str) -> dict:
        """TODO: Validate a goal file and return a structured result.

        Steps:
        1. Call parse_goal_fields(content) → fields
        2. Call parse_steps(content) → steps
        3. Call validate_tools(fields["tools"]) → tool_issues
        4. Check required conditions, appending to issues list:
           - If len(fields["tools"]) == 0: append "Goal must reference at least one tool"
           - If len(steps) == 0: append "Goal must have a ## Steps section with ≥1 numbered step"
           - If fields["output_type"] == "unknown": append "Goal must have an # Output: line (DB table or file)"
           - Add all tool_issues
        5. Return:
           {
               "valid": len(issues) == 0,
               "issues": issues,
               "parsed": {
                   "tools": fields["tools"],
                   "steps": steps,
                   "args_file": fields["args_file"],
                   "output_type": fields["output_type"],
                   "tool_count": len(fields["tools"]),
                   "step_count": len(steps),
               },
           }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    validator = GoalValidator()

    print("=== FORGE Goal Validator ===\n")

    r1 = validator.validate(VALID_GOAL)
    print(f"Valid goal: valid={r1['valid']}, tools={r1['parsed']['tool_count']}, steps={r1['parsed']['step_count']}")

    r2 = validator.validate(INVALID_GOAL_NO_TOOLS)
    print(f"No-tools goal: valid={r2['valid']}, issues={r2['issues']}")

    r3 = validator.validate(INVALID_GOAL_NO_STEPS)
    print(f"No-steps goal: valid={r3['valid']}, issues={r3['issues']}")

    r4 = validator.validate(INVALID_GOAL_BAD_PATH)
    print(f"Bad-path goal: valid={r4['valid']}, issues={r4['issues']}")
