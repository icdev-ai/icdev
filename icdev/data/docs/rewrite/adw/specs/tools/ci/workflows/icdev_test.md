# Spec: `tools/ci/workflows/icdev_test.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

Run the unit/integration test suite for a kanban run with retry-and-
resolve, optionally run E2E tests, evaluate the security and compliance
gates, post a summary on the linked issue, and attempt self-recovery
on failure.

## CLI

```
python tools/ci/workflows/icdev_test.py <issue-number> <run-id> [--skip-e2e]
```

## Constants

* `AGENT_TESTER = "icdev_tester"`
* `MAX_TEST_RETRY = 4`
* `MAX_E2E_RETRY = 2`

## Public functions

### `run_test_suite(run_id, logger, skip_e2e=False) -> dict`

Drives the test orchestrator under `tools/testing/test_orchestrator.py`.
Returns a dict with these keys (all may be None when skipped):

* `unit_tests`: list returned by `run_tests_with_resolution(...)`.
* `e2e_tests`: result returned by `run_e2e_tests_with_resolution(...)`,
  or `{"error": True, "reason": <str>}` on exception.
* `security_gate`: result of `evaluate_security_gate(<repo>)`.
* `compliance_gate`: result of `evaluate_compliance_gate(<repo>)`.
* `all_passed`: bool, True iff every present component passed.

Each gate exception must be logged at WARNING and degrade to "pass not
proven" rather than crashing the whole workflow.

### `format_test_summary(results) -> str`

Markdown body for the issue comment. Sections in order:

```
## Test Results

**Unit/Integration Tests:** <p>/<t> passed
**E2E Tests:** Completed     (or "Skipped (<reason>)")
**Security Gate:** PASS|FAIL
**Compliance Gate:** PASS|FAIL

**Overall:** PASS|FAIL
```

### `main()`

1. Args validation; <2 args → usage + exit 1.
2. Load state, configure logger.
3. Build VCS; on `ValueError` log + exit 1.
4. Comment "Starting test suite".
5. Call `run_test_suite(run_id, logger, skip_e2e)`.
6. Comment the formatted summary.
7. `commit_changes("icdev_tester: test results for issue #N")` →
   `finalize_git_operations(state, logger, vcs)` if commit succeeded.
8. If overall passed, comment "Test phase completed — all passed".
9. Otherwise attempt self-recovery via
   `tools.ci.core.recovery_engine.RecoveryEngine().attempt_recovery(
       phase="test", failure_text=json.dumps(results, default=str),
       run_id=..., issue_number=..., state=...
   )`.
   * On import error, log info and skip.
   * On any other exception, log warning.
   * If `recovery_result.recovered`, comment with the recovery message
     including `recovery_result.attempts` and `fixed_files`.
   * Otherwise comment with the engine's `format_escalation_message`
     and exit 1.
10. `state.save("icdev_test")`.

## Forbidden

* No DB writes.
* No `print()` for production output.

## Acceptance

Tests cover:

* Missing args → exit 1.
* `format_test_summary` for: all passed; unit fail only; gate fail.
* `run_test_suite` happy path with stubbed orchestrator + gates.
* `run_test_suite` swallows gate exceptions and continues.
* `main` happy path → exit 0.
* `main` failure path with recovery succeeded → exit 0.
* `main` failure path with recovery unavailable → exit 1.
