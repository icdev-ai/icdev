# Feature Planning

Create a new plan to implement the `Feature` using the exact specified markdown `Plan Format`. Follow the `Instructions` to create the plan.

## Variables
issue_number: $1
run_id: $2
issue_json: $3

## Instructions

- IMPORTANT: You're writing a plan to implement a feature, NOT implementing it directly. Create a thorough plan.
- Research the codebase to understand existing patterns, architecture, and conventions.
- Create the plan in the `specs/` directory with filename: `issue-{issue_number}-icdev-{run_id}-icdev_planner-{descriptive-name}.md`
- IMPORTANT: Replace every <placeholder> in the `Plan Format` with specific values.
- Use your reasoning model: THINK HARD about feature requirements, design, and implementation.
- Follow existing patterns and conventions. Design for extensibility and maintainability.
- All generated artifacts MUST include CUI markings: `CUI // SP-CTI`
- Follow TDD: plan tests BEFORE implementation steps.
- If the feature includes UI, add a task to create an E2E test in `.claude/commands/e2e/test_<name>.md`

## Plan Format

```md
# CUI // SP-CTI
# Feature: <feature name>

## Metadata
issue_number: `{issue_number}`
run_id: `{run_id}`

## Feature Description
<describe the feature in detail, including its purpose and value>

## User Story
As a <type of user>
I want to <action/goal>
So that <benefit/value>

## Solution Statement
<describe the proposed solution approach>

## Relevant Files
<list files relevant to the feature with bullet point descriptions>

### New Files
<list any new files that need to be created>

## Implementation Plan
### Phase 1: Foundation
<foundational work needed>

### Phase 2: Core Implementation
<main implementation work>

### Phase 3: Integration & Testing
<integration with existing functionality and test creation>

## Step by Step Tasks
IMPORTANT: Execute every step in order, top to bottom.

<list step by step tasks as h3 headers plus bullet points. Start with tests (TDD).>

## Testing Strategy
### Unit Tests
<unit tests needed>

### BDD Tests
<Gherkin feature files if applicable>

### Edge Cases
<edge cases to test>

## Acceptance Criteria
<specific, measurable criteria for completion>

## Validation Commands
- `python -m py_compile <file>` - Syntax check
- `python -m pytest` - Run tests
- `ruff check .` - Lint check
- `behave` - Run BDD tests (if applicable)

## NIST 800-53 Controls
<list any NIST controls relevant to this feature>

## Notes
<additional context, future considerations>

# CUI // SP-CTI
```

## Feature
Extract the feature details from the `issue_json` variable (parse the JSON and use the title and body fields).

## Report

- IMPORTANT: Return exclusively the path to the plan file created and nothing else.
