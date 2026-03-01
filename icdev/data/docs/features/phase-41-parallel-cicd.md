# Phase 41 — Parallel CI/CD

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 41 |
| Title | Parallel CI/CD — Git Worktree Task Isolation |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 39 (Observability & Operations), Phase 11 (CI/CD Integration) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV's CI/CD integration (Phase 11) processes issues sequentially — each workflow (plan, build, test, review) runs on the main working directory with a single git branch. When multiple GitLab issues require concurrent processing, workflows block each other, leading to bottlenecks and increased cycle time. In DoD environments where multiple development teams submit work simultaneously via GitLab task boards, sequential processing is a critical limitation.

Git worktrees provide a mechanism for parallel, conflict-free execution by creating isolated working directory copies that share the same repository history but operate on independent branches. Combined with sparse checkout (only checking out the target directory for each task), worktrees minimize disk usage while providing full isolation. Each worktree gets its own CUI classification marker, ensuring classification awareness even in parallel execution.

Additionally, GitLab task boards serve as the primary work intake mechanism for many DoD teams. Automated detection of ICDEV workflow tags in GitLab issues, combined with worktree-based isolation, enables a fully automated parallel CI/CD pipeline where multiple tasks progress concurrently without git conflicts or branch collisions.

---

## 2. Goals

1. Enable parallel, conflict-free CI/CD workflow execution through git worktree isolation with sparse checkout
2. Automate GitLab task board monitoring with `{{icdev: workflow}}` tag-based routing to ICDEV workflows
3. Create per-task isolated branches and worktrees with CUI classification markers
4. Track worktree and task claim state in the database for deduplication and lifecycle management
5. Provide worktree lifecycle commands (create, list, cleanup, status) for manual and automated use
6. Clean up worktrees automatically after workflow completion or failure

---

## 3. Architecture

```
GitLab Task Board
  Issue #1: {{icdev: build}} ──┐
  Issue #2: {{icdev: sdlc}}  ──┤
  Issue #3: {{icdev: comply}} ──┤
                                ↓
          gitlab_task_monitor.py (polls every 20s)
                │
          ┌─────┼──────────┐
          ↓     ↓          ↓
    Claim #1  Claim #2  Claim #3
    (dedup)   (dedup)   (dedup)
          │     │          │
          ↓     ↓          ↓
    worktree  worktree  worktree
    trees/    trees/    trees/
    task-1/   task-2/   task-3/
    (sparse)  (sparse)  (sparse)
          │     │          │
          ↓     ↓          ↓
    Build    SDLC      Comply
    workflow  workflow  workflow
    (detached subprocess)
          │     │          │
          ↓     ↓          ↓
    Cleanup  Cleanup   Cleanup
    (auto)   (auto)    (auto)
```

The GitLab task monitor polls open issues with the `icdev` label every 20 seconds. It parses issue bodies for `{{icdev: workflow}}` tags and maps them to ICDEV workflow commands. For each unclaimed task, it creates a database claim (deduplication), provisions an isolated worktree with sparse checkout of the target directory, and spawns the mapped workflow as a detached subprocess. Task progress is reported back to GitLab as issue comments. After completion, worktrees are cleaned up and claims updated.

---

## 4. Requirements

### 4.1 Git Worktree Isolation

#### REQ-41-001: Worktree Creation
The system SHALL create isolated git worktrees in `trees/<task-id>/` with a dedicated branch `icdev-<task-id>` for each task.

#### REQ-41-002: Sparse Checkout
The system SHALL use git sparse checkout to include only the target directory for each task, minimizing disk usage and checkout time.

#### REQ-41-003: CUI Classification Marker
Each worktree SHALL include a CUI classification marker file indicating the classification level of the work being performed.

#### REQ-41-004: Worktree Cleanup
The system SHALL automatically clean up worktrees after workflow completion or failure, removing the working directory and pruning the worktree reference.

### 4.2 GitLab Task Board Integration

#### REQ-41-005: Issue Polling
The GitLab task monitor SHALL poll open issues with the `icdev` label every 20 seconds (configurable).

#### REQ-41-006: Tag-Based Routing
The system SHALL parse issue bodies for `{{icdev: workflow}}` tags and route to the corresponding ICDEV workflow: intake, build, sdlc, comply, secure, modernize.

#### REQ-41-007: Task Claim Deduplication
The system SHALL track claimed tasks in the `gitlab_task_claims` table to prevent duplicate processing of the same issue.

#### REQ-41-008: Progress Reporting
The system SHALL update GitLab issues with progress comments during workflow execution and final status on completion.

### 4.3 Workflow Execution

#### REQ-41-009: Detached Subprocess Execution
Workflows SHALL be spawned as detached subprocesses within their respective worktrees, enabling true parallel execution.

#### REQ-41-010: Lifecycle Tracking
Worktree state (created, active, completed, failed, cleaned) SHALL be tracked in the `ci_worktrees` table.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `ci_worktrees` | Worktree lifecycle tracking — task ID, branch name, path, status (created/active/completed/failed/cleaned), creation time, cleanup time |
| `gitlab_task_claims` | Issue claim deduplication — issue ID, workflow type, claim status, claimed_at, completed_at, worker ID |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/ci/modules/worktree.py` | Git worktree lifecycle management — create, list, cleanup, status with sparse checkout and CUI markers |
| `tools/ci/triggers/gitlab_task_monitor.py` | GitLab issue polling, tag extraction, task claiming, workflow spawning |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D32 | Git worktrees with sparse checkout for task isolation | Zero-conflict parallelism, per-task branches, classification markers, minimal disk usage |
| D33 | GitLab tags `{{icdev: workflow}}` for task routing | Mirrors existing VCS abstraction, uses tag syntax familiar to GitLab users, declarative routing |

---

## 8. Security Gate

**Parallel CI/CD Gate:**
- Each worktree must have a CUI classification marker matching the project classification level
- Task claims must be deduplicated — no duplicate processing of the same GitLab issue
- Worktrees must be cleaned up after workflow completion (no stale worktrees with CUI data)
- GitLab issue comments must include `[ICDEV-BOT]` identifier to prevent bot loops
- All workflow spawning must be logged to the audit trail

---

## 9. Commands

```bash
# Worktree management
python tools/ci/modules/worktree.py --create --task-id test-123 --target-dir src/ --json
python tools/ci/modules/worktree.py --list --json
python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-test-123
python tools/ci/modules/worktree.py --status --worktree-name icdev-test-123

# GitLab task board monitor
python tools/ci/triggers/gitlab_task_monitor.py                    # Start monitor (polls every 20s)
python tools/ci/triggers/gitlab_task_monitor.py --dry-run          # Preview without spawning
python tools/ci/triggers/gitlab_task_monitor.py --once             # Single poll and exit

# Configuration
# args/worktree_config.yaml — Sparse checkout, cleanup policy, GitLab polling, tag-to-workflow mapping
```
