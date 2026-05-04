# Spec: `tools/ci/workflows/icdev_plan.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

Kick off a kanban run: fetch the linked issue, classify it, generate a
feature branch name, create the branch, ask the planner agent for an
implementation plan, commit the plan, and push.

## CLI

```
python tools/ci/workflows/icdev_plan.py <issue-number> [run-id]
```

* `issue-number` required; missing → usage + exit 1.
* `run-id` optional; resolved via `ensure_run_id(issue_number, run_id)`.

## Public functions

### `check_env_vars(logger) -> None`

Probe LLM availability for the `code_generation` function in the
following order:

1. If `shutil.which(os.getenv("CLAUDE_CODE_PATH","claude"))` returns a
   path, log it and return.
2. Otherwise import `tools.llm.router.LLMRouter` and call
   `get_provider_for_function("code_generation")`. If a provider comes
   back, log it and return.
3. Otherwise emit a multi-line ERROR explaining the three remediation
   options (install Claude Code, configure a provider in
   `args/llm_config.yaml`, or set `ANTHROPIC_API_KEY`) plus any
   `hints` derived from `ANTHROPIC_API_KEY` / AWS / Ollama env vars,
   then `sys.exit(1)`.

A graceful import failure of `tools.llm.router` also logs an error and
exits 1.

### `main()`

1. Parse args; <1 arg → usage + exit 1.
2. `ensure_run_id(...)`, load state, set up logger.
3. Call `check_env_vars(logger)`.
4. `VCS()` — record `platform = "gitlab" if is_gitlab else "github"`,
   save state. On `ValueError` log + exit 1.
5. Fetch issue via `vcs.fetch_issue(int(issue_number))`. On exception
   log + exit 1.
6. Comment "Starting planning phase".
7. `classify_issue(issue_json, run_id, logger)` — on error post a
   failure comment + exit 1. Otherwise update state and post the
   classification.
8. `generate_branch_name(issue_json, issue_command, run_id, logger)` —
   on error log + exit 1.
9. `create_branch(branch_name)` — on error log + exit 1.
10. Update state with `branch_name`, save, comment.
11. Call `build_plan(issue_json, issue_command, run_id, logger)`. On
    failure post a failure comment + exit 1.
12. The plan response's `output.strip()` is a path. If the path is
    empty or doesn't exist, log + exit 1.
13. Update state with `plan_file`, save.
14. `create_commit(AGENT_PLANNER, issue_json, issue_command, run_id,
    logger)`. On error log + exit 1.
15. `commit_changes(msg)`. On error log + exit 1.
16. `finalize_git_operations(state, logger, vcs)`.
17. Comment "Planning phase completed", save state.

## Forbidden

* No DB writes.
* No `print()` for production output.
