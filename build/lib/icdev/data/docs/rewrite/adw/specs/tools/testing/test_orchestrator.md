# Spec: `tools/testing/test_orchestrator.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

The end-to-end testing orchestrator. Drives the full ICDEV test gauntlet
for one project: syntax → quality → unit → BDD → SAST → E2E → security
gate → compliance gate → coherence → agentic → acceptance V&V → summary.
Used by `tools/ci/workflows/icdev_test.py` and as a standalone CLI.

## CLI

```
python tools/testing/test_orchestrator.py \
    --project-dir <path> [--project-id <id>] [--skip-e2e] \
    [--skip-sandbox] [--skip-security] [--skip-compliance] \
    [--skip-acceptance] [--plan PATH] [--base-url URL] \
    [--pages PATH ...] [--json]
```

`--project-dir` required. Other flags toggle individual phases.

## Constants

* `MAX_TEST_RETRY_ATTEMPTS = 4`
* `MAX_E2E_TEST_RETRY_ATTEMPTS = 2`

## Public functions (full list — preserved for `tools/ci/workflows/icdev_test.py`)

### Quality checks
* `run_py_compile(project_dir, logger) -> TestResult`
* `run_ruff(project_dir, logger) -> TestResult`
* `run_sandbox_isolation(project_dir, logger) -> TestResult`
* `run_bandit(project_dir, logger) -> TestResult`

### Test runners
* `run_pytest(project_dir, logger) -> tuple[list[TestResult], int, int]`
* `run_behave(project_dir, logger) -> tuple[list[TestResult], int, int]`

### Drivers
* `run_tests_with_resolution(project_dir, run_id=None, logger=None,
  max_attempts=MAX_TEST_RETRY_ATTEMPTS, skip_sandbox=False) ->
  tuple[list[TestResult], int, int]`
  Runs the full quality + unit + BDD pipeline with up to `max_attempts`
  retries. Stop early on success.
* `run_e2e_tests(run_id, logger, attempt=1) -> list[E2ETestResult]`
* `run_e2e_tests_with_resolution(run_id, logger,
  max_attempts=MAX_E2E_TEST_RETRY_ATTEMPTS) -> tuple[..., int, int]`

### Discovery / mode
* `_detect_e2e_mode() -> str`
* `discover_e2e_tests() -> list[str]`

### Gates
* `evaluate_security_gate(project_dir, logger=None) -> GateEvaluation`
* `evaluate_compliance_gate(project_id, project_dir=None, logger=None)
  -> GateEvaluation`

### Reporting
* `generate_summary(unit_results, bdd_results, e2e_results,
  security_gate, compliance_gate, logger) -> str`

### CLI
* `main(argv=None) -> int`

## Behavioral notes

* Every quality check returns a `TestResult` even when a tool is
  missing — failures degrade to a "skipped" pass with the appropriate
  comment.
* `run_pytest` uses `pytest --no-header --tb=short` and parses verbose
  stdout for `PASSED|FAILED|ERROR` lines (no JUnit dependency).
* `run_behave` writes its JSON output to
  `<project_dir>/behave-results.json` and parses scenarios into
  `TestResult` rows.
* `run_tests_with_resolution` accepts an optional `run_id` and
  `logger` so callers that don't have them can pass `None` and let the
  driver build defaults via `make_run_id()` and a noop logger.
* `evaluate_security_gate` runs a SAST probe (via
  `tools.security.sast_runner.run_sast`), a secret-detection probe
  (`tools.security.secret_detector.scan_directory`), and an opt-in
  OpenClaw bridge gate (only when `ICDEV_OPENCLAW_ENABLED` is truthy).
  Every probe degrades gracefully on `ImportError`.
* `evaluate_compliance_gate` runs a CUI marking probe via
  `tools.compliance.cui_marker.verify_directory`. Same graceful
  degradation.

## Bugs to fix during rewrite

1. **Dead-code bug at line 968** of the original:
   `[r for r in all_results if r.test_type == "security"]` is a list
   comprehension whose result is discarded. Either remove it or assign
   it to `security_results` and feed it into the report. The rewrite
   removes the dead expression.

2. **`run_tests_with_resolution` signature accepts `run_id, logger`
   positionally but the orchestrator's `main()` always passes them.**
   The signature must remain backward-compatible — keep them as
   keyword args with sane defaults so other callers (`icdev_test.py`)
   that use kwargs still work.

3. **`evaluate_compliance_gate` and `evaluate_security_gate` accept
   `logger` as their final positional arg.** Keep that signature.

## Forbidden

* No DB writes outside the audit-trail call (which is best-effort and
  swallows every exception).
* No `print()` for production output other than the CLI banners and
  `--json` payload.
* No `git push --force`.

## Acceptance

Tests cover (with mocked subprocess + filesystem):

1. Module-level constants exposed.
2. `run_py_compile` returns success on a tmp dir with no source.
3. `run_pytest` swallows `FileNotFoundError` and returns empty.
4. `run_behave` skips cleanly when `features/` is absent.
5. `run_tests_with_resolution` happy path through stubbed subroutines.
6. `_detect_e2e_mode` returns "mcp" when native is unavailable.
7. `evaluate_security_gate` produces a GateEvaluation with at least one
   `GateResult`.
8. `evaluate_security_gate` swallows missing-import errors.
9. `evaluate_compliance_gate` swallows missing-import errors.
10. `generate_summary` produces a string containing the expected
    headings.
11. `main([])` exits non-zero (missing required `--project-dir`).
12. `main(["--project-dir", tmp])` runs through the full pipeline with
    every external function stubbed.
