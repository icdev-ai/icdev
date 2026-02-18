# Bug Planning

Create a new plan to resolve the `Bug` using the exact specified markdown `Plan Format`. Follow the `Instructions` to create the plan.

## Variables
issue_number: $1
run_id: $2
issue_json: $3

## Instructions

- IMPORTANT: You're writing a plan to resolve a bug, NOT fixing it directly. Create a thorough plan in the `Plan Format` below.
- Research the codebase to understand the bug, reproduce it, and put together a fix plan.
- Create the plan in the `specs/` directory with filename: `issue-{issue_number}-icdev-{run_id}-icdev_planner-{descriptive-name}.md`
- IMPORTANT: Replace every <placeholder> in the `Plan Format` with specific values.
- Use your reasoning model: THINK HARD about the bug, its root cause, and the steps to fix it properly.
- Be surgical — solve the bug at hand, don't fall off track. Minimal changes.
- All generated artifacts MUST include CUI markings: `CUI // SP-CTI`
- If the bug affects UI, add a task to create an E2E test in `.claude/commands/e2e/test_<name>.md`
- Start research by reading the project's README or relevant goal files.

## Plan Format

```md
# CUI // SP-CTI
# Bug: <bug name>

## Metadata
issue_number: `{issue_number}`
run_id: `{run_id}`

## Bug Description
<describe the bug in detail, including symptoms and expected vs actual behavior>

## Root Cause Analysis
<analyze and explain the root cause of the bug>

## Solution Statement
<describe the proposed solution approach to fix the bug>

## Relevant Files
<find and list the files relevant to the bug, describe why in bullet points>

### New Files
<list any new files that need to be created>

## Step by Step Tasks
IMPORTANT: Execute every step in order, top to bottom.

<list step by step tasks as h3 headers plus bullet points>

## Validation Commands
<list commands to validate the bug is fixed with zero regressions>

- `python -m py_compile <file>` - Syntax check
- `python -m pytest` - Run tests
- `ruff check .` - Lint check

## NIST 800-53 Controls
<list any NIST controls relevant to this fix, e.g., SI-2 for flaw remediation>

## Notes
<additional context>

# CUI // SP-CTI
```

## Bug
Extract the bug details from the `issue_json` variable (parse the JSON and use the title and body fields).

## Report

- IMPORTANT: Return exclusively the path to the plan file created and nothing else.
