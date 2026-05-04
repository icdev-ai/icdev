# Spec: `tools/ci/workflows/icdev_document.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

Run the `icdev_documenter` agent to produce feature documentation for
the changes on a kanban run's feature branch, then commit and push.

## CLI

```
python tools/ci/workflows/icdev_document.py <issue-number> <run-id>
```

Both required. <2 args → usage line + exit 1.

## Behaviour

1. `ICDevState.load(run_id)`, `setup_logger(run_id, "icdev_document")`.
2. `VCS()` — on `ValueError` log + exit 1.
3. Read `branch_name`. Missing → log + exit 1.
4. `create_branch(branch_name)` — on failure log + exit 1.
5. Call `check_for_changes(logger)` (described below). If False, post
   "No changes to document — skipping" via the bot envelope (using
   `"ops"` as the agent label), save state, return 0.
6. Otherwise post "Generating documentation".
7. Call `execute_template(AgentTemplateRequest(agent_name=
   AGENT_DOCUMENTER, slash_command="/document", args=[run_id,
   spec_path], run_id=run_id))` where `spec_path = state.get("plan_file","")`.
8. On success the agent's `output.strip()` is the path of the generated
   doc. Commit with
   `f"{AGENT_DOCUMENTER}: document feature for issue #{issue_number}"`,
   call `finalize_git_operations`, post a "Documentation created at
   `<path>` and committed" comment.
9. On failure log + comment + exit 1.

## `check_for_changes(logger) -> bool`

Run `git diff origin/main --stat` from `PROJECT_ROOT`. Return True if
stdout is non-empty after stripping. Any subprocess exception → return
True (assume changes if we can't tell — better to do the work than
silently skip).

## Constants

* `AGENT_DOCUMENTER = "icdev_documenter"`

## Forbidden

* No `git add -A`.
* No DB writes.
* No print() for production output.
