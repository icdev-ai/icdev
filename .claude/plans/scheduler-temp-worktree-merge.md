# Plan: Temp-Worktree Merge Isolation for Kanban

## Problem
`_merge_worktree_to_main()` in `tools/genesis/reflexes/kanban.py` performs merge operations directly on the main repository working tree (`BASE_DIR`). This requires `git stash`, `git checkout`, and `git merge` on the user's active branch, which:
1. Stashes the user's uncommitted work
2. Switches the user's branch out from under them
3. Triggers the `auto_paused()` mechanism in `scheduler_control.py`, causing the kanban scheduler to stop dispatching tasks whenever a human is working interactively

## Solution
Use a **temporary git worktree** for all merge operations, completely isolating them from the main repository working tree.

## Changes

### 1. `tools/genesis/reflexes/kanban.py` — `_merge_worktree_to_main()`
- **Create** a temporary worktree at `.tmp/worktrees/.merge-{task_id}` on the default branch
- **Perform all git operations** (merge, rebase, push) inside the temp worktree
- **Never** run `git stash`, `git checkout`, or `git merge` on `BASE_DIR`
- **Always clean up** the temp worktree in a `finally` block (with `git worktree remove --force`)
- Preserve the existing logic:
  1. Check if there are commits to merge (read-only on BASE_DIR)
  2. Fast-forward merge inside temp worktree
  3. If FF fails, rebase branch onto default branch inside temp worktree, then FF merge
  4. Push from temp worktree on success
  5. On rebase conflict: abort, log, preserve branch, return False
- Remove the nested `_restore()` and `_push_main()` functions; `_push_main` becomes a simple inline call with a `cwd` parameter

### 2. `tools/kanban/scheduler_control.py` — Remove auto-pause necessity
- `should_pause()` will **only** respect the manual pause flag (`manual_paused()`)
- Remove `auto_paused()` call from `should_pause()`
- Update the module docstring to state that auto-pause is obsolete because merges use temp worktrees
- Keep `auto_paused()` and `active_interactive_sessions()` for backward compat / dashboard visibility, but they no longer block the scheduler

### 3. `tests/` — Add regression test
- Create `tests/test_kanban_merge_isolation.py`
- Verify that `_merge_worktree_to_main()`:
  - Does not modify `git status` of the main repo
  - Does not change the current branch of the main repo
  - Creates and cleans up the temp worktree
  - Successfully merges a branch with commits

### 4. Validation
- Run `pytest tests/kanban/test_scheduler_hardened.py -v`
- Run `python tools/workflow/coherence_checker.py --all --fix --gate`
- Verify scheduler starts dispatching without auto-pause blocking

## Trade-offs
| Aspect | Current | Proposed |
|--------|---------|----------|
| Main repo touched | Yes (stash/checkout/merge) | No |
| Auto-pause needed | Yes | No |
| Concurrent merges | Race condition | Safe (each temp worktree isolated) |
| Disk space | Low | +1 temp worktree per concurrent merge (~same size as task worktree) |
| Windows file-lock risk | Present on main repo | Present but isolated to temp worktree |

## References
- `feedback_scheduler_restart.md` — scheduler restart after killing python.exe
- `kanban-tasks-lock-storm.md` — idle-in-transaction backend issues
- `project_session_coordination.md` — cross-session coordination via registry
