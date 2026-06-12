# Spec: `tools/ci/workflows/icdev_review.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

A short CI workflow script that runs the ICDEV automated code-review
agent against the spec/plan file referenced by a kanban run, posts a
status comment on the linked issue, and finalises git operations.

## CLI

```
python tools/ci/workflows/icdev_review.py <issue-number> <run-id>
```

Both arguments are required positional. With less than two arguments,
print a usage line on stdout and exit with status 1.

## Behaviour

1. Load `ICDevState.load(run_id)` and configure
   `setup_logger(run_id, "icdev_review")`.
2. Construct a `VCS()` instance — on `ValueError` log the error and
   exit 1.
3. Read `plan_file` from state.
   * If it's missing or the path doesn't exist, comment on the issue
     with the bot envelope `format_issue_message(...)` reporting "No
     plan file found — cannot review", log an error, and exit 1.
4. Otherwise post a "Starting code review" comment.
5. Call the review agent through
   `tools.ci.modules.agent.execute_template`, supplying an
   `AgentTemplateRequest` with:
     * `agent_name = "icdev_reviewer"` (constant)
     * `slash_command = "/icdev-review"`
     * `args = [plan_file]`
     * `run_id = run_id`
6. The response shape exposes `success: bool`, `output: str`, and
   `session_id: str | None`.
7. Truncate the output to a small preview (2000 characters) before
   embedding it in the issue comment.
8. Comment on the issue:
   * On success, post `## Code Review Complete\n\n<preview>`.
   * On failure, post `## Code Review Issues\n\n<preview>` and log a
     warning.
9. Run `commit_changes(...)` with a deterministic commit message.
   If the commit succeeds, call `finalize_git_operations(state,
   logger, vcs)`.
10. Save state via `state.save("icdev_review")`.

## Module-level constants

* `AGENT_REVIEWER = "icdev_reviewer"`
* `MAX_REVIEW_RETRY = 3` (declared even though the rewrite isn't
  required to use it — preserved for compatibility with callers that
  import the constant).
* `OUTPUT_PREVIEW_CHARS = 2000` (new constant — eliminate the magic
  number from the original).

## Function surface

* `run_review(plan_file: str, run_id: str, logger) -> dict`
  Returns `{"success": bool, "output": str, "session_id": str | None}`.
* `main() -> None`

## Forbidden

* No DB access.
* No LLM imports outside of the existing agent helper module.
* No `git push --force`.
* No `print()` for production output — use the logger.

## Acceptance

When the rewrite lands:

1. The CLI shape and argv positions match the original.
2. The constants `AGENT_REVIEWER` and `MAX_REVIEW_RETRY` are exposed
   at module level.
3. `run_review` returns the documented dict shape.
4. Tests cover: missing-args usage exit, missing plan-file exit,
   success path, failure path, commit-and-finalise path. Subprocess
   /agent calls are stubbed.
