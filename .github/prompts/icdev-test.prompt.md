---
mode: agent
description: "Run the full test suite (pytest unit + behave BDD) with coverage reporting and compliance tracking"
tools:
  - terminal
  - file_search
---

# icdev-test

1. Runs pytest unit tests with coverage measurement
2. Runs behave BDD tests (Gherkin feature files)
3. Reports pass/fail, coverage percentage, and failure details
4. Maps test execution to NIST 800-53 controls (SA-11(1))
5. Records results in audit trail

## Steps

1. **Detect Project Directory**
If `--project-dir` not specified, look for nearest project directory with tests/ folder.

2. **Run Tests**
Run the equivalent CLI command for run_tests:
- project_dir: resolved project path
- test_type: from `--type` flag or default "all"

3. **Analyze Results**
Parse test output for:
- Total tests run / passed / failed / skipped
- Coverage percentage per module

4. **Coverage Gate Check**
```bash
!cat args/project_defaults.yaml
```

5. **Compliance Mapping**
Run the equivalent CLI command for control_map:
- Map `test.execute` activity to NIST controls

6. **Output Summary**
Display:
- Unit tests: X passed, Y failed, Z skipped
- BDD tests: X scenarios passed, Y failed

7. **Output Summary**
Display:
- Unit tests: X passed, Y failed, Z skipped
- BDD tests: X scenarios passed, Y failed

## Example
```
#prompt:icdev-test --project-dir projects/my-webapp --type all --verbose
```