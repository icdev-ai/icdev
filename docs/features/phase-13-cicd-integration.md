# Phase 13 — CI/CD Integration

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 13 |
| Title | CI/CD Integration |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 12 (Integration Testing), Phase 5 (TDD Workflow) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV's power as an agentic development system is limited if it can only be triggered manually via CLI commands. Gov/DoD development teams use issue trackers (GitHub Issues, GitLab Issues) as their primary work management interface. Developers create issues, write requirements in issue bodies, and expect automated pipelines to plan, build, test, and review code based on those issues. Without CI/CD integration, ICDEV sits outside the team's natural workflow.

Furthermore, Gov/DoD programs often mandate either GitHub (via GitHub Enterprise) or GitLab (via GitLab Ultimate on-prem or GitLab.com SaaS) as their VCS platform. ICDEV must support both equally without requiring teams to switch platforms. A unified VCS abstraction layer allows the same workflow commands (`/icdev_plan`, `/icdev_sdlc`) to work identically on either platform, with platform-specific details (PR vs MR, `gh` vs `glab` CLI) handled transparently.

Bot loop prevention is critical in webhook-driven systems. Without explicit guards, an ICDEV bot comment could trigger another webhook event, which triggers another bot action, creating an infinite loop that consumes CI/CD minutes and floods the issue with duplicate comments. The `[ICDEV-BOT]` identifier in all bot comments provides a simple, reliable mechanism to break this cycle.

---

## 2. Goals

1. Receive webhook events from both GitHub (`/gh-webhook`) and GitLab (`/gl-webhook`) via a Flask webhook server with HMAC-SHA256 (GitHub) and secret token (GitLab) verification
2. Poll issues every 20 seconds via a cron-based poll trigger that processes new issues and issues with `icdev` comments
3. Classify workflow requests from issue body or comments (`/icdev_plan`, `/icdev_build`, `/icdev_sdlc`, etc.) and route to appropriate workflow orchestrators
4. Execute the full SDLC pipeline autonomously: Plan (classify, branch, plan, commit, push), Build (load state, implement, commit, push), Test (suite, gates, commit, push), Review (vs spec, patches, commit, push)
5. Auto-detect GitHub vs GitLab from `git remote get-url origin` and use the appropriate CLI (`gh` or `glab`) transparently through a unified VCS abstraction
6. Prevent bot loops by including `[ICDEV-BOT]` in all bot comments and ignoring comments containing this identifier in webhook handlers
7. Manage persistent state across workflow phases via `agents/{run_id}/icdev_state.json`, supporting piping between scripts for chaining
8. Generate standardized branch names (`<type>-issue-<num>-icdev-<id>-<name>`) and commit messages (`<agent>: <type>: <message>`) via Claude Code slash commands

---

## 3. Architecture

```
+-----------------------------------------------------------+
|              Trigger Layer                                 |
|                                                           |
|  +-------------------+  +-------------------+             |
|  | Webhook Server    |  | Poll Trigger      |             |
|  | (Flask)           |  | (20s interval)    |             |
|  |                   |  |                   |             |
|  | POST /gh-webhook  |  | Polls issues for  |             |
|  | POST /gl-webhook  |  | new or 'icdev'    |             |
|  | HMAC + token auth |  | comments          |             |
|  +--------+----------+  +--------+----------+             |
|           |                      |                        |
+-----------+----------------------+------------------------+
            |                      |
            v                      v
+-----------------------------------------------------------+
|              Workflow Classification                       |
|                                                           |
|  /icdev_plan  /icdev_build  /icdev_test  /icdev_review    |
|  /icdev_sdlc  /icdev_plan_build  /icdev_plan_build_test   |
+----------------------------+------------------------------+
                             |
                             v
+-----------------------------------------------------------+
|              Core Modules                                 |
|                                                           |
|  +------+  +-------+  +-------+  +----------+  +------+  |
|  | VCS  |  | Agent |  | State |  | Git Ops  |  | Wkfl |  |
|  | gh/  |  | Exec  |  | JSON  |  | Branch/  |  | Ops  |  |
|  | glab |  | CC CLI|  | file  |  | Commit/  |  | Class|  |
|  +------+  +-------+  +-------+  | Push/PR  |  +------+  |
|                                   +----------+            |
+-----------------------------------------------------------+
                             |
                             v
+-----------------------------------------------------------+
|              Workflow Orchestrators                        |
|                                                           |
|  icdev_plan.py   --> Classify -> Branch -> Plan -> Commit |
|  icdev_build.py  --> Load State -> Implement -> Commit    |
|  icdev_test.py   --> Test Suite -> Gates -> Commit        |
|  icdev_review.py --> Review vs Spec -> Patches -> Commit  |
|  icdev_sdlc.py   --> Plan -> Build -> Test -> Review      |
+-----------------------------------------------------------+
```

### Platform Auto-Detection

```
git remote get-url origin
    |
    +-- contains "github.com" --> GitHub mode (gh CLI)
    |
    +-- everything else       --> GitLab mode (glab CLI)
```

### Bot Loop Prevention

```
Bot posts comment with [ICDEV-BOT] marker
    |
    v
Webhook receives event for new comment
    |
    v
Check: does comment contain [ICDEV-BOT]?
    |
    +-- Yes --> Ignore (break loop)
    +-- No  --> Process normally
```

---

## 4. Requirements

### 4.1 Trigger Mechanisms

#### REQ-13-001: Webhook Server
The system SHALL provide a Flask-based webhook server that receives POST events from GitHub at `/gh-webhook` and GitLab at `/gl-webhook`.

#### REQ-13-002: Webhook Authentication
GitHub webhooks SHALL be validated via HMAC-SHA256 using `WEBHOOK_SECRET` environment variable. GitLab webhooks SHALL be validated via secret token using `GITLAB_WEBHOOK_TOKEN`.

#### REQ-13-003: Poll Trigger
The system SHALL provide a polling trigger that checks for new issues or issues with `icdev` comments every 20 seconds, with graceful shutdown via SIGINT/SIGTERM.

#### REQ-13-004: Bot Loop Prevention
All bot-generated comments SHALL include the `[ICDEV-BOT]` identifier. Webhook handlers SHALL ignore events where the triggering comment contains this identifier.

### 4.2 VCS Abstraction

#### REQ-13-005: Platform Auto-Detection
The system SHALL auto-detect GitHub vs GitLab from `git remote get-url origin` and route to the appropriate CLI (`gh` or `glab`).

#### REQ-13-006: Unified VCS Module
The system SHALL provide a unified VCS abstraction module (`tools/ci/modules/vcs.py`) that handles platform-specific operations (PR vs MR, issue comments, labels) transparently.

### 4.3 Workflow Commands

#### REQ-13-007: Workflow Command Parsing
The system SHALL parse workflow commands from issue body or comments: `/icdev_plan`, `/icdev_build run_id:<id>`, `/icdev_test run_id:<id>`, `/icdev_review run_id:<id>`, `/icdev_sdlc`, and compound variants.

#### REQ-13-008: Build Requires Prior Plan
The `/icdev_build` command SHALL require a `run_id` parameter referencing a completed plan phase. Invocation without `run_id` SHALL be rejected.

### 4.4 State Management

#### REQ-13-009: Persistent Workflow State
Workflow state SHALL persist across phases via `agents/{run_id}/icdev_state.json`, containing run_id, issue_number, branch_name, plan_file, issue_class, platform, and project_id.

#### REQ-13-010: State Piping
Workflow scripts SHALL support piping state between scripts via stdin/stdout for chaining compound workflows.

### 4.5 Code Operations

#### REQ-13-011: Standardized Branch Names
The system SHALL generate branch names in the format `<type>-issue-<num>-icdev-<id>-<name>` via the `/generate_branch_name` Claude Code command.

#### REQ-13-012: Standardized Commit Messages
The system SHALL generate commit messages in the format `<agent>: <type>: <message>` via the `/commit` Claude Code command.

### 4.6 Security

#### REQ-13-013: Safe Subprocess Environment
The system SHALL filter sensitive environment variables before spawning Claude Code CLI subprocesses.

#### REQ-13-014: Webhook Response Handling
The system SHALL always return HTTP 200 to webhook endpoints to prevent retry storms from the VCS platform.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `audit_trail` | Append-only CI/CD event records (plan, build, test, review completions) |
| `ci_worktrees` | Git worktree records for parallel CI/CD task isolation (Phase 41 extension) |
| `gitlab_task_claims` | GitLab task polling claims to prevent duplicate processing |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/ci/triggers/webhook_server.py` | Flask webhook server for GitHub (/gh-webhook) and GitLab (/gl-webhook) events |
| `tools/ci/triggers/poll_trigger.py` | Cron-based issue polling every 20 seconds |
| `tools/ci/modules/vcs.py` | Unified VCS abstraction for GitHub (gh) and GitLab (glab) CLI |
| `tools/ci/modules/agent.py` | Claude Code CLI subprocess invocation |
| `tools/ci/modules/state.py` | Persistent state management at agents/{run_id}/icdev_state.json |
| `tools/ci/modules/git_ops.py` | Branch creation, commit, push, PR/MR creation |
| `tools/ci/modules/workflow_ops.py` | Issue classification, branch naming, commit message formatting |
| `tools/ci/workflows/icdev_plan.py` | Plan phase: Classify, Branch, Plan, Commit, Push |
| `tools/ci/workflows/icdev_build.py` | Build phase: Load State, Implement, Commit, Push |
| `tools/ci/workflows/icdev_test.py` | Test phase: Test Suite, Gates, Commit, Push |
| `tools/ci/workflows/icdev_review.py` | Review phase: Review vs Spec, Patches, Commit, Push |
| `tools/ci/workflows/icdev_sdlc.py` | Full SDLC: Plan, Build, Test, Review (sequential or DAG-based) |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D31 | HMAC-SHA256 event signing for webhook verification | Tamper detection without PKI overhead; secret via environment variable |
| D32 | Git worktrees with sparse checkout for task isolation | Zero-conflict parallelism, per-task branches, classification markers (Phase 41 extension) |
| D33 | GitLab tags `{{icdev: workflow}}` for task routing | Mirrors Notion pattern, uses existing VCS abstraction |
| D35 | Agent executor stores JSONL output in agents/ dir | Auditable, replayable, consistent with observability patterns |

---

## 8. Security Gate

**CI/CD Integration Gate:**
- Webhook authentication required (HMAC-SHA256 for GitHub, secret token for GitLab)
- Bot loop prevention active (all bot comments include `[ICDEV-BOT]`)
- Safe subprocess environment filters sensitive env vars before Claude Code CLI invocation
- `stdin=subprocess.DEVNULL` prevents Claude Code CLI from hanging
- Webhook endpoints always return HTTP 200 to prevent retry storms
- `/icdev_build` rejected without prior plan `run_id` (prevents unplanned builds)

---

## 9. Commands

```bash
# Trigger mechanisms
python tools/ci/triggers/webhook_server.py       # Start webhook server
python tools/ci/triggers/poll_trigger.py          # Start issue polling (20s)

# Individual workflow phases
python tools/ci/workflows/icdev_plan.py 123       # Plan for issue #123
python tools/ci/workflows/icdev_build.py 123 abc1234  # Build (requires run-id)
python tools/ci/workflows/icdev_test.py 123 abc1234   # Test
python tools/ci/workflows/icdev_review.py 123 abc1234 # Review

# Compound workflows
python tools/ci/workflows/icdev_sdlc.py 123       # Full SDLC: Plan -> Build -> Test -> Review
python tools/ci/workflows/icdev_sdlc.py 123 --orchestrated  # DAG-based parallel SDLC
python tools/ci/workflows/icdev_plan_build.py 123  # Plan + Build

# Git worktree parallel CI/CD (Phase 41 extension)
python tools/ci/modules/worktree.py --create --task-id test-123 --target-dir src/ --json
python tools/ci/modules/worktree.py --list --json
python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-test-123

# GitLab task board monitor (Phase 41 extension)
python tools/ci/triggers/gitlab_task_monitor.py
python tools/ci/triggers/gitlab_task_monitor.py --dry-run
python tools/ci/triggers/gitlab_task_monitor.py --once
```

**CUI // SP-CTI**
