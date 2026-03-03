# Patch Plan

Create a **focused patch plan** to resolve a specific issue based on the `review_change_request`. Follow the `Instructions` to create a concise plan that addresses the issue with minimal, targeted changes.

## Variables

run_id: $1
review_change_request: $2
spec_path: $3 if provided, otherwise leave it blank
agent_name: $4 if provided, otherwise use 'patch_agent'

## Instructions

- IMPORTANT: You're creating a patch plan to fix a specific issue. Keep changes small, focused, and targeted.
- Read the original specification (spec) file at `spec_path` if provided to understand context.
- Use the `review_change_request` as the basis for your patch plan.
- Create the patch plan in `specs/patch/` directory with filename: `patch-icdev-{run_id}-{descriptive-name}.md`
- IMPORTANT: This is a PATCH — keep scope minimal. Only fix what's described.
- Run `git diff --stat` to understand what's been done in the codebase.
- All generated artifacts MUST include CUI markings: `CUI // SP-CTI`
- Replace every <placeholder> in the `Plan Format` with specific details.

## Plan Format

```md
# CUI // SP-CTI
# Patch: <concise patch title>

## Metadata
run_id: `{run_id}`
review_change_request: `{review_change_request}`

## Issue Summary
**Original Spec:** <spec_path>
**Issue:** <brief description of the issue>
**Solution:** <brief description of the solution>

## Files to Modify
<list only the files that need changes — be specific and minimal>

## Implementation Steps
IMPORTANT: Execute every step in order, top to bottom.

### Step 1: <specific action>
- <implementation detail>

### Step 2: <specific action>
- <implementation detail>

## Validation
- `python -m py_compile <file>` - Syntax check
- `python -m pytest` - Run tests
- `ruff check .` - Lint check

## Patch Scope
**Lines of code to change:** <estimate>
**Risk level:** <low|medium|high>
**Testing required:** <brief description>

# CUI // SP-CTI
```

## Report

- IMPORTANT: Return exclusively the path to the patch plan file created and nothing else.
