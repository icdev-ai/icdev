# Spec: `tools/ci/workflows/icdev_build.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

Take the plan file produced by `icdev_plan.py` for a kanban run and
have the implementor agent realise it on the run's feature branch.

## CLI

```
python tools/ci/workflows/icdev_build.py <issue-number> <run-id>
```

Both arguments are required positional. Less than two args → print
usage and exit 1.

## Behaviour

1. Load `ICDevState.load(run_id)` and configure
   `setup_logger(run_id, "icdev_build")`.
2. Construct `VCS()` — on `ValueError` log and exit 1.
3. Read `branch_name` from state. If missing, log and exit 1
   ("Run icdev_plan first").
4. Call `create_branch(branch_name)`. On failure log and exit 1.
5. Read `plan_file` from state. If missing or path doesn't exist, log
   and exit 1.
6. Comment on the issue: "Starting implementation" via the bot
   envelope.
7. Call `implement_plan(plan_file, run_id, logger)` from
   `tools.ci.modules.workflow_ops`. The return is an object with
   `success: bool` and `output: str`.
8. On failure, post a "Implementation failed: <output>" comment and
   exit 1.
9. On success, build a commit message using
   `create_commit(AGENT_IMPLEMENTOR, issue_json, issue_class, run_id,
   logger)`. The function returns `(message, error)`. On error, use a
   deterministic fallback `f"{AGENT_IMPLEMENTOR}: implement plan for
   issue #{issue_number}"`.
10. Run `commit_changes(commit_msg)`. On failure, log and exit 1.
11. Call `finalize_git_operations(state, logger, vcs)`.
12. Post "Implementation committed and pushed" comment.
13. Save state via `state.save("icdev_build")`.

## Issue fetch tolerance

Step 9 calls `vcs.fetch_issue(issue_number)` to populate
`issue_json`. Failures must be swallowed (use `{}` as the fallback)
because some platforms may return non-200 even when the issue exists.

## Forbidden

* No DB access.
* No `print()` for production output — logger only.
* No `git push --force` (existing rule).

## Acceptance

1. CLI shape matches the original.
2. Tests cover: missing args, missing branch, branch checkout failure,
   missing plan, agent success path, agent failure path, commit
   failure path, and the fallback commit message when
   `create_commit` errors.
