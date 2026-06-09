---
name: icdev-build
description: "Build code using true TDD (RED → GREEN → REFACTOR) with automatic test generation and compliance tracking. Use when implementing new features or bug fixes that require test-driven development."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-build

## What This Does
Implements the full TDD cycle for a feature:
1. **RED** — Generate failing tests from feature description
2. **GREEN** — Generate minimal code to make tests pass
3. **REFACTOR** — Clean up code while keeping tests green
4. All artifacts get CUI markings and NIST 800-53 control mappings

## Steps

### 1. Load Project Context
```bash
!cat args/project_defaults.yaml
```
Read the project's existing code structure to understand patterns.

### 2. RED Phase — Write Failing Tests
Run the CLI command or use MCP tool `write_tests` MCP tool from icdev-builder:
- Feature description: `$ARGUMENTS`
- Generates Gherkin BDD feature file (.feature) with scenarios
- Generates pytest unit tests with assertions
- Generates behave step definitions

Verify tests fail:
Run the CLI command or use MCP tool `run_tests` MCP tool from icdev-builder to confirm RED state.

### 3. GREEN Phase — Generate Implementation Code
Run the CLI command or use MCP tool `generate_code` MCP tool from icdev-builder:
- Analyzes failing test files to determine required code
- Generates minimal implementation to make tests pass
- Applies CUI markings to all generated files

Verify tests pass:
Run the CLI command or use MCP tool `run_tests` MCP tool to confirm GREEN state.

### 4. REFACTOR Phase — Clean Up
Run the CLI command or use MCP tool `format` MCP tool from icdev-builder:
- Run black + isort (Python) or prettier (JS)

Run the CLI command or use MCP tool `lint` MCP tool from icdev-builder:
- Run bandit (security) + pylint/eslint (quality)
- Fix any issues found

Self-green the change before commit (review-until-green loop):
- `python tools/quality/review_loop.py --json` — runs ICDEV's gates (ruff +
  changed-files coherence + SIPA) over the diff, applies deterministic
  autofixes, and iterates until every blocking gate passes or the cap is hit.
- Address any `fix_brief` findings it reports, then re-run until green.
- This is the same loop the autonomous pipeline runs pre-PR (git_ops preflight)
  and the pre-commit hook runs on staged files — running it here means the
  build lands green by hand or autonomously.

Re-run tests to confirm nothing broke.

### 5. Compliance Mapping
Run the CLI command or use MCP tool `control_map` MCP tool from icdev-compliance:
- Map `code.commit` activity to NIST controls (SA-11, CM-3)

### 6. Output Summary
Display:
- Tests written (count, types)
- Code generated (files, lines)
- Test results (pass/fail, coverage %)
- Lint results (issues found/fixed)
- Next steps

## Example
```
$icdev-build "User authentication with JWT tokens, login/logout endpoints, password hashing with bcrypt"
```

## Error Handling
- If GREEN phase fails after 3 attempts: show failing tests and ask for guidance
- If lint finds critical security issues: block and require fix before proceeding
- If coverage drops below threshold (80%): warn but continue