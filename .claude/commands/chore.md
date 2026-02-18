# Chore Planning

Create a new plan to resolve the `Chore` using the exact specified markdown `Plan Format`. Follow the `Instructions` to create the plan.

## Variables
issue_number: $1
run_id: $2
issue_json: $3

## Instructions

- IMPORTANT: You're writing a plan to resolve a chore, NOT resolving it directly. Create a focused plan.
- Research the codebase and put together a plan to accomplish the chore.
- Create the plan in the `specs/` directory with filename: `issue-{issue_number}-icdev-{run_id}-icdev_planner-{descriptive-name}.md`
- IMPORTANT: Replace every <placeholder> in the `Plan Format` with specific values.
- Be thorough and precise so we don't miss anything or waste time with second-round changes.
- All generated artifacts MUST include CUI markings: `CUI // SP-CTI`

## Plan Format

```md
# CUI // SP-CTI
# Chore: <chore name>

## Metadata
issue_number: `{issue_number}`
run_id: `{run_id}`

## Chore Description
<describe the chore in detail>

## Relevant Files
<list files relevant to the chore with bullet point descriptions>

### New Files
<list any new files if needed>

## Step by Step Tasks
IMPORTANT: Execute every step in order, top to bottom.

<list step by step tasks as h3 headers plus bullet points>

## Validation Commands
- `python -m py_compile <file>` - Syntax check
- `python -m pytest` - Run tests
- `ruff check .` - Lint check

## Notes
<additional context>

# CUI // SP-CTI
```

## Chore
Extract the chore details from the `issue_json` variable (parse the JSON and use the title and body fields).

## Report

- IMPORTANT: Return exclusively the path to the plan file created and nothing else.
