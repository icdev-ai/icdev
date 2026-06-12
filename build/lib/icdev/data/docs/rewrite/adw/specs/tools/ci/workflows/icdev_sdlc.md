# Spec: `tools/ci/workflows/icdev_sdlc.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

The end-to-end SDLC orchestrator. Chains the per-phase workflow scripts
together (plan → build → test → e2e → coherence → review → comply) and
falls back to a multi-agent DAG orchestrator when invoked with
``--orchestrated``.

## CLI

```
python tools/ci/workflows/icdev_sdlc.py <issue-number> [run-id] [--orchestrated]
```

* `issue-number` required positional.
* `run-id` optional positional. Resolved via `ensure_run_id`.
* `--orchestrated` (flag) — try the DAG orchestrator first, fall back
  to sequential on import failure.

## Constants

* `RECOVERABLE_PHASES = {"Test", "Build"}` — phases that may attempt
  self-recovery on subprocess failure.

## Public functions

### `run_phase(phase_name, script_name, issue_number, run_id, extra_args=None) -> bool`

* Build a subprocess command:
  `[sys.executable, <this_dir>/<script_name>.py, issue_number, run_id, *extra_args]`.
* Print a header banner with the phase name.
* Run the subprocess from `PROJECT_ROOT`, capturing stdout + stderr.
* Echo stdout to stdout and stderr to stderr.
* On `returncode == 0`, return True.
* Otherwise:
  - Print a "<phase> phase FAILED" line.
  - If the phase is in `RECOVERABLE_PHASES`, call
    `_attempt_phase_recovery(...)`. If recovery succeeds, return True.
  - Otherwise return False.

### `_attempt_phase_recovery(phase_name, failure_output, run_id, issue_number) -> bool`

* Lazy-import the recovery engine and `ICDevState`. Any `ImportError`
  → return False.
* Map the phase name through `{"Test": "test", "Build": "compile"}`,
  defaulting to `phase_name.lower()`.
* Construct an `ICDevState.load(run_id)` and call
  `engine.attempt_recovery(parser_phase, failure_output, run_id,
  issue_number, state)`.
* On `recovered`, print summary and return True.
* On non-recovery, attempt to comment on the issue with
  `engine.format_escalation_message(...)` (best-effort, all
  exceptions swallowed) and return False.
* Any other exception during the recovery flow → return False.

### `run_orchestrated(issue_number, run_id) -> bool`

* Lazy import `tools.agent.team_orchestrator.TeamOrchestrator`.
* Build a multi-line `task_desc` string describing the SDLC phases and
  their dependencies (plan → build → test, review parallel after
  build).
* `orchestrator.decompose_task(task_desc, project_id=...)` → workflow.
* `orchestrator.execute_workflow(workflow, timeout=1200)`.
* Return True iff `workflow.status == "completed"`.
* `ImportError` or any other exception → print + return False.

### `main()`

1. Parse args; <1 → usage banner + exit 1.
2. Resolve `run_id`; print CUI marking + header.
3. If `--orchestrated`, call `run_orchestrated`. On success, print
   completion banner and return. On failure, fall through to
   sequential.
4. Sequential pipeline:
   - Plan → Build → Test (each must succeed; failure aborts with
     exit 1).
   - E2E — non-blocking warning on failure.
   - Coherence — wrapped in try/except so import failure is non-fatal;
     auto-fix on; log only.
   - Review → Comply (each must succeed; failure aborts).
5. Print success banner with run id and issue number.

## Forbidden

* No DB writes.
* `print()` is the canonical output mechanism for this script (it's a
  CLI orchestrator and writes a banner that is the user's primary
  feedback). The other workflow scripts use loggers because they're
  silent helpers; this one is the user-facing entry point.

## Acceptance

Tests cover:

* Missing args → exit 1.
* Phase mapping in `_attempt_phase_recovery`.
* `run_phase` happy path.
* `run_phase` failure with non-recoverable phase.
* `run_phase` failure with recoverable phase + recovery success.
* `run_orchestrated` returns False on import failure.
* End-to-end `main()` happy path with all phases stubbed.
