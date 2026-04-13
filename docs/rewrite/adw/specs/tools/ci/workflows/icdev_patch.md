# Spec: `tools/ci/workflows/icdev_patch.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

A focused single-issue patch workflow. Looks for the keyword
``icdev_patch`` in an issue body or comments, asks the patch planner to
write a tiny patch plan from that content, hands the plan to the patch
implementor, then commits and pushes.

## CLI

```
python tools/ci/workflows/icdev_patch.py <issue-number> [run-id]
```

* `issue-number` required positional.
* `run-id` optional positional. If absent, derive one via
  `tools.ci.modules.workflow_ops.ensure_run_id(issue_number, None)`.

## Constants

* `AGENT_PATCH_PLANNER = "patch_planner"`
* `AGENT_PATCH_IMPLEMENTOR = "patch_implementor"`

## Behaviour (high level)

1. Parse args; resolve `run_id`. Less than 1 arg → usage + exit 1.
2. Load state, configure logger.
3. Construct VCS; record `platform = "gitlab" if vcs.is_gitlab else
   "github"` into state via `state.update(platform=…)`.
4. `vcs.fetch_issue(issue_int)` (must succeed; on exception log + exit 1).
5. Comment "Starting patch workflow" via the bot envelope (agent="ops").
6. Determine the working branch:
   - Use `state.get("branch_name")` if set.
   - Otherwise call `classify_issue(issue_json, run_id, logger)` →
     command (default `"/patch"`); then
     `generate_branch_name(issue_json, command, run_id, logger)`. On
     error log + exit 1.
7. `create_branch(branch_name)` — on failure log + exit 1.
8. `state.update(branch_name=…, issue_class="/patch")`,
   `state.save("icdev_patch")`.
9. Build `patch_content` via `get_patch_content(issue_data, vcs,
   issue_int, logger)` (helper function — preserved as a public
   helper at module level so tests can drive it directly).
10. Comment "Creating patch plan", call `execute_template` with the
    planner's `AgentTemplateRequest`. Failure → log + exit 1. Success
    → `patch_file = response.output.strip()`,
    `state.update(plan_file=patch_file)`, save.
11. Comment "Implementing patch", call
    `implement_plan(patch_file, run_id, logger, AGENT_PATCH_IMPLEMENTOR)`.
    Failure → log + exit 1.
12. Build commit message via `create_commit(AGENT_PATCH_IMPLEMENTOR,
    issue_json, "/patch", run_id, logger)`. On error fall back to
    `f"{AGENT_PATCH_IMPLEMENTOR}: patch for issue #{issue_number}"`.
13. `commit_changes(msg)` — on failure log + exit 1.
14. `finalize_git_operations(state, logger, vcs)`.
15. Comment "Patch workflow completed".
16. `state.save("icdev_patch")`.

## `get_patch_content(issue_data, vcs, issue_number, logger) -> str`

Look for the keyword `"icdev_patch"` (case-insensitive):

1. Walk `vcs.fetch_issue_comments(issue_number)` in reverse. The first
   comment whose `body` (or `note` for GitLab) contains the keyword is
   returned verbatim.
2. If none found, check the issue body. If it contains the keyword,
   return `f"Issue #{issue_number}: {title}\n\n{body}"`.
3. Otherwise (fallback) return the same shape but log "no keyword found".

## Forbidden

* No DB writes.
* No `print()` for production output.

## Acceptance

Tests cover: missing args, missing branch (auto-classified), branch
checkout failure, planner failure, implementor failure, commit failure,
happy path with full chain, `get_patch_content` keyword found in comment,
keyword found in body, keyword absent fallback.
