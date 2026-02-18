# CUI // SP-CTI
# Parallel CI/CD Goal — Git Worktree Isolation + GitLab Task Board

## Purpose
Enable parallel, conflict-free CI/CD execution through git worktree isolation
and automated GitLab task board monitoring with tag-based workflow routing.

## Trigger
- GitLab issue created with `{{icdev: workflow}}` tag
- Manual worktree creation via `/init_worktree`
- Webhook/poll trigger with worktree isolation enabled

## Workflow

### 1. Task Detection
GitLab Task Monitor (`tools/ci/triggers/gitlab_task_monitor.py`) polls
open issues with the `icdev` label every 20 seconds.

### 2. Tag Extraction
Parse issue body for `{{icdev: workflow}}` tags:
| Tag | Workflow |
|-----|---------|
| `{{icdev: intake}}` | RICOAS intake session |
| `{{icdev: build}}` | TDD build (RED->GREEN->REFACTOR) |
| `{{icdev: sdlc}}` | Full SDLC pipeline |
| `{{icdev: comply}}` | Compliance artifact generation |
| `{{icdev: secure}}` | Security scanning |
| `{{icdev: modernize}}` | Legacy app modernization |

### 3. Worktree Isolation
For each claimed task:
- Create isolated worktree: `trees/<task-id>/`
- Sparse checkout: only target directory
- CUI classification marker
- Separate git branch: `icdev-<task-id>`

### 4. Workflow Execution
- Spawn workflow as detached subprocess
- Track in `gitlab_task_claims` table
- Update GitLab issue with progress comments

### 5. Cleanup
- After workflow completes, cleanup worktree
- Update claim status to completed/failed
- Remove `icdev-processing` label

## Tools Used
| Tool | Purpose |
|------|---------|
| `worktree.py` | Git worktree lifecycle (create, list, cleanup, status) |
| `gitlab_task_monitor.py` | GitLab issue polling + tag routing |

## Database Tables
- `ci_worktrees` -- Worktree state tracking
- `gitlab_task_claims` -- Issue claim dedup

## Success Criteria
- Multiple workflows run in parallel without git conflicts
- Each worktree has classification marker
- GitLab issues auto-claimed and routed to correct workflow
- Worktrees cleaned up after task completion
