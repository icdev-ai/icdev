# Git Worktree Parallel CI/CD (Phase 41)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Git Worktree Parallel CI/CD (Phase 41)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Worktree Manager | tools/ci/modules/worktree.py | Git worktree lifecycle: create (sparse checkout), list, cleanup, status | --create, --list, --cleanup, --status | WorktreeInfo |
| GitLab Task Monitor | tools/ci/triggers/gitlab_task_monitor.py | Poll GitLab issues for {{icdev: workflow}} tags, auto-trigger workflows | --interval, --dry-run, --once | Workflow launch |

