---
name: icdev-test
description: "Run the full test suite (pytest unit + behave BDD) with coverage reporting and compliance tracking. Use when validating code quality and coverage before a release."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-test

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
Run the CLI command or use MCP tool `run_tests` MCP tool from icdev-builder:
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

### 5. Theater Detection
```bash
python -m tools.testing.theater_detector --scan tests/ --json
```
Scans for tests that pass but provide no real coverage or signal. Severity levels:
- **none** — no anti-patterns found; proceed normally
- **warn** — anti-patterns present but gate passes; surface findings in output summary
- **block** — critical anti-patterns found; **fails the gate** (do not proceed to compliance mapping)

The 8 anti-patterns detected:
1. **Empty test body** — test function contains only `pass` or a docstring with no assertions
2. **Assert True/False literal** — `assert True` or `assert False` hardcoded (never exercises real logic)
3. **Missing assertions** — test runs code but has zero `assert` / `assertEqual` / `expect` calls
4. **Mocked return always passes** — mock return value set to make the test unconditionally succeed regardless of implementation
5. **Test copies implementation** — assertion value duplicates the exact expression under test rather than an independent expected value
6. **Skipped test without reason** — `@pytest.mark.skip` or `unittest.skip` with no justification string
7. **Broad exception swallow** — bare `except Exception: pass` inside a test hides failures silently
8. **Fixture with no teardown** — stateful fixture (DB, file, network) that never cleans up, causing cross-test pollution

### 6. Compliance Mapping
Run the CLI command or use MCP tool `control_map` MCP tool from icdev-compliance:
- Map `test.execute` activity to NIST controls

### 7. Output Summary
Display:
- Unit tests: X passed, Y failed, Z skipped
- BDD tests: X scenarios passed, Y failed
- Coverage: XX% (PASS/WARN based on threshold)
- Failed test details (if any)
- Compliance: Controls satisfied by test execution

## Example
```
$icdev-test --project-dir projects/my-webapp --type all --verbose
```

## Error Handling
- If pytest not installed: suggest `pip install pytest pytest-cov`
- If behave not installed: skip BDD tests with warning
- If no tests/ directory: report "No tests found" and suggest /icdev-build
- If tests timeout (300s): report partial results