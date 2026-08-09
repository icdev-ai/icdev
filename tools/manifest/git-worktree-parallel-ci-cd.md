# Git Worktree Parallel CI/CD (Phase 41)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Git Worktree Parallel CI/CD (Phase 41)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Worktree Manager | tools/ci/modules/worktree.py | Git worktree lifecycle: create (sparse checkout), list, cleanup, status | --create, --list, --cleanup, --status | WorktreeInfo |
| Worktree Paths | tools/git/worktree_paths.py | Single source of truth for WHERE a worktree may live; disjoint roots per actor, session-namespaced CLI paths, enforced by pre_tool_use | --path ACTOR SLUG, --check PATH, --audit, --json | Path / sanctioned bool / audit report |
| GitLab Task Monitor | tools/ci/triggers/gitlab_task_monitor.py | Poll GitLab issues for {{icdev: workflow}} tags, auto-trigger workflows | --interval, --dry-run, --once | Workflow launch |
| Manifest Merge Rehearsal | tools/git/manifest_merge_rehearsal.py | kax-conflict-03. Scripted two-branch git rehearsal that MEASURES which `tools/manifest/` layout survives the observed edit pattern (unrelated tasks each registering a new tool under the same topic). Builds a throwaway repo per scenario, cuts N branches, merges them back, and reports conflict / entries-kept / duplicate-entries. Layouts: `table-row` (baseline), `heading-block`, `union` (`.gitattributes merge=union`), `per-tool-file`. Runs both merge paths — `worktree` (local `git merge`) and `merge-tree` (bare `git merge-tree --write-tree`, the plumbing a forge runs server-side to compute mergeability), because a layout that is clean only in the worktree path still shows a PR as conflicted. Measured 2026-08-08: table-row and heading-block CONFLICT in both paths; union and per-tool-file are clean in both at 2/3/5 branches. Backs the `merge=union` decision recorded in `.gitattributes`. | --layout, --mode {worktree,merge-tree,both}, --branches, --keep, --json | Per-scenario conflict report + `conflict_free_layouts` |

