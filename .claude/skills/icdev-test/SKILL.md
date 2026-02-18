---
name: icdev-test
description: Run the full test suite (pytest unit + behave BDD) with coverage reporting and compliance tracking
context: fork
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# /icdev-test — Run Test Suite

## Usage
```
/icdev-test [--project-dir <path>] [--type unit|bdd|all] [--verbose] [--no-coverage]
```

## What This Does
1. Runs pytest unit tests with coverage measurement
2. Runs behave BDD tests (Gherkin feature files)
3. Reports pass/fail, coverage percentage, and failure details
4. Maps test execution to NIST 800-53 controls (SA-11(1))
5. Records results in audit trail

## Steps

### 1. Detect Project Directory
If `--project-dir` not specified, look for nearest project directory with tests/ folder.

### 2. Run Tests
Use the `run_tests` MCP tool from icdev-builder:
- project_dir: resolved project path
- test_type: from `--type` flag or default "all"
- verbose: from `--verbose` flag
- coverage: true unless `--no-coverage`

### 3. Analyze Results
Parse test output for:
- Total tests run / passed / failed / skipped
- Coverage percentage per module
- Failing test details with error messages

### 4. Coverage Gate Check
```bash
!cat args/project_defaults.yaml
```
Check coverage against threshold (default 80%). Warn if below.

### 5. Compliance Mapping
Use the `control_map` MCP tool from icdev-compliance:
- Map `test.execute` activity to NIST controls

### 6. Output Summary
Display:
- Unit tests: X passed, Y failed, Z skipped
- BDD tests: X scenarios passed, Y failed
- Coverage: XX% (PASS/WARN based on threshold)
- Failed test details (if any)
- Compliance: Controls satisfied by test execution

## Example
```
/icdev-test --project-dir projects/my-webapp --type all --verbose
```

## Error Handling
- If pytest not installed: suggest `pip install pytest pytest-cov`
- If behave not installed: skip BDD tests with warning
- If no tests/ directory: report "No tests found" and suggest /icdev-build
- If tests timeout (300s): report partial results
