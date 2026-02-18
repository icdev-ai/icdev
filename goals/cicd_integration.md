# CUI // SP-CTI
# Goal: CI/CD Integration (GitHub + GitLab)

## Purpose
Enable ICDEV to receive webhook events and poll issues from both GitHub and GitLab,
classify workflow requests, and execute the full SDLC pipeline autonomously.

## Architecture

### Trigger Layer
Two trigger mechanisms (choose one or both):
1. **Webhook Server** (`tools/ci/triggers/webhook_server.py`) — Flask server that receives
   POST events from GitHub (`/gh-webhook`) and GitLab (`/gl-webhook`)
2. **Poll Trigger** (`tools/ci/triggers/poll_trigger.py`) — Cron-based polling every 20
   seconds that checks for new issues or issues with `icdev` comments

### Core Modules
- **VCS** (`tools/ci/modules/vcs.py`) — Unified abstraction for GitHub (`gh` CLI) and GitLab (`glab` CLI)
- **Agent Executor** (`tools/ci/modules/agent.py`) — Invokes Claude Code CLI as subprocess
- **State** (`tools/ci/modules/state.py`) — Persistent state at `agents/{run_id}/icdev_state.json`
- **Git Ops** (`tools/ci/modules/git_ops.py`) — Branch, commit, push with dual-platform PR/MR
- **Workflow Ops** (`tools/ci/modules/workflow_ops.py`) — Issue classification, branch naming, commit messages

### Workflow Orchestrators
| Workflow | Script | Phases |
|----------|--------|--------|
| `icdev_plan` | `tools/ci/workflows/icdev_plan.py` | Classify → Branch → Plan → Commit → Push |
| `icdev_build` | `tools/ci/workflows/icdev_build.py` | Load state → Implement → Commit → Push |
| `icdev_test` | `tools/ci/workflows/icdev_test.py` | Test suite → Gates → Commit → Push |
| `icdev_review` | `tools/ci/workflows/icdev_review.py` | Review vs spec → Patches → Commit → Push |
| `icdev_sdlc` | `tools/ci/workflows/icdev_sdlc.py` | Plan → Build → Test → Review |
| `icdev_plan_build` | Combined Plan + Build |
| `icdev_plan_build_test` | Combined Plan + Build + Test |
| `icdev_plan_build_test_review` | Combined Plan + Build + Test + Review |

### Claude Code Commands (Slash Commands)
| Command | File | Purpose |
|---------|------|---------|
| `/classify_issue` | `.claude/commands/classify_issue.md` | Classify issue as /chore, /bug, /feature, /patch |
| `/classify_workflow` | `.claude/commands/classify_workflow.md` | Extract ICDEV workflow command from text |
| `/generate_branch_name` | `.claude/commands/generate_branch_name.md` | Generate standardized branch names |
| `/implement` | `.claude/commands/implement.md` | Implement a plan with CUI markings |
| `/commit` | `.claude/commands/commit.md` | Generate git commit messages |
| `/pull_request` | `.claude/commands/pull_request.md` | Create PR (GitHub) or MR (GitLab) |

## Workflow Triggering

### Via Webhook
Users trigger workflows by including commands in issue body or comments:
- `/icdev_plan` — Plan only
- `/icdev_sdlc` — Full lifecycle
- `/icdev_build run_id:abc12345` — Build with existing state

### Via Polling
The poll trigger automatically processes:
- New issues without comments
- Issues where the latest comment is `icdev`

### Bot Loop Prevention
All bot comments include `[ICDEV-BOT]` identifier. Webhooks ignore comments containing
this identifier to prevent infinite loops.

## Platform Detection
VCS auto-detects the platform from `git remote get-url origin`:
- `github.com` → GitHub mode (uses `gh` CLI)
- Everything else → GitLab mode (uses `glab` CLI)

## State Management
State persists across workflow phases via `agents/{run_id}/icdev_state.json`.
Supports piping between scripts via stdin/stdout for chaining.

Core fields: `run_id`, `issue_number`, `branch_name`, `plan_file`, `issue_class`, `platform`, `project_id`

## Security
- GitHub webhooks validated via HMAC-SHA256 (`WEBHOOK_SECRET`)
- GitLab webhooks validated via secret token (`GITLAB_WEBHOOK_TOKEN`)
- Safe subprocess environment filters sensitive env vars
- `stdin=subprocess.DEVNULL` prevents Claude Code CLI from hanging

## Edge Cases
- If `icdev_build` is called without a `run_id`, it is rejected (needs prior plan state)
- If branch already exists, checkout instead of create
- Always return 200 to webhooks to prevent retries
- Graceful shutdown via SIGINT/SIGTERM handlers in poll trigger

# CUI // SP-CTI
