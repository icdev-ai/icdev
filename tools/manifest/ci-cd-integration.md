# CI/CD Integration (GitHub + GitLab)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## CI/CD Integration (GitHub + GitLab)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| VCS Abstraction | tools/ci/modules/vcs.py | Unified GitHub (gh) + GitLab (glab) interface | Auto-detects platform | VCS instance |
| Agent Executor | tools/ci/modules/agent.py | Claude Code CLI subprocess invocation | AgentTemplateRequest | AgentPromptResponse |
| State Manager | tools/ci/modules/state.py | Persistent workflow state (agents/{run_id}/icdev_state.json) | run_id | ICDevState |
| Git Ops | tools/ci/modules/git_ops.py | Branch, commit, push, PR/MR creation | branch_name, message | success/error |
| Workflow Ops | tools/ci/modules/workflow_ops.py | Issue classification, branch gen, commit, PR helpers | issue_json, run_id | Results |
| Webhook Server | tools/ci/triggers/webhook_server.py | Flask server for GitHub + GitLab webhooks | POST /gh-webhook, /gl-webhook | Workflow launch |
| Poll Trigger | tools/ci/triggers/poll_trigger.py | Cron-based issue polling (20s interval) | Auto-detects platform | Workflow launch |
| ICDEV™ Plan | tools/ci/workflows/icdev_plan.py | Planning phase: classify, branch, plan | issue-number, run-id | Plan file |
| ICDEV™ Build | tools/ci/workflows/icdev_build.py | Implementation phase: implement plan | issue-number, run-id | Committed code |
| ICDEV™ Test | tools/ci/workflows/icdev_test.py | Testing phase: pytest, ruff, bandit, gates | issue-number, run-id | Test results |
| ICDEV™ Review | tools/ci/workflows/icdev_review.py | Code review against spec | issue-number, run-id | Review results |
| ICDEV™ Document | tools/ci/workflows/icdev_document.py | Documentation generation from changes | issue-number, run-id | Doc file |
| ICDEV™ Patch | tools/ci/workflows/icdev_patch.py | Quick fix workflow from issue content | issue-number, run-id | Patched code |
| ICDEV™ SDLC | tools/ci/workflows/icdev_sdlc.py | Complete lifecycle: plan+build+test+review | issue-number, run-id | All artifacts |
| Agent Model Test | tools/testing/test_agent_models.py | Verify opus/sonnet/haiku model availability | — | Pass/fail per model |
| Base Connector | tools/ci/connectors/base_connector.py | ABC for CI/CD platform connectors (GitHub, GitLab, Mattermost, Slack) | (library) | BaseConnector ABC |
| Connector Registry | tools/ci/connectors/connector_registry.py | Registry for CI/CD platform connectors — auto-discover and load | (library) | ConnectorRegistry |
| Mattermost Connector | tools/ci/connectors/mattermost_connector.py | Mattermost integration for CI/CD notifications and triggers (air-gap safe, D140) | (library) | MattermostConnector |
| Slack Connector | tools/ci/connectors/slack_connector.py | Slack integration for CI/CD notifications and triggers | (library) | SlackConnector |
| Air Gap Detector | tools/ci/core/air_gap_detector.py | Detect air-gapped environments and disable internet-dependent features (D134/D139) | (library) | AirGapStatus |
| Comment Handler | tools/ci/core/comment_handler.py | Parse and handle CI/CD comments from issues/PRs (bot loop prevention) | (library) | ParsedComment |
| Conversation Manager | tools/ci/core/conversation_manager.py | Manage multi-turn CI/CD conversations for issue resolution | (library) | ConversationState |
| Event Router | tools/ci/core/event_router.py | Route webhook/poll events to appropriate workflow handlers | (library) | RoutedEvent |
| Failure Parser | tools/ci/core/failure_parser.py | Parse CI/CD failure logs and extract actionable error context | (library) | ParsedFailure |
| Recovery Engine | tools/ci/core/recovery_engine.py | Auto-recover from CI/CD pipeline failures (retry, workaround, escalate) | (library) | RecoveryAction |
| ICDEV™ Comply | tools/ci/workflows/icdev_comply.py | Compliance artifact generation workflow for CI/CD | issue-number, run-id | Compliance artifacts |
| ICDEV™ E2E | tools/ci/workflows/icdev_e2e.py | E2E test execution workflow for CI/CD | issue-number, run-id | E2E results |
| ICDEV™ Plan+Build | tools/ci/workflows/icdev_plan_build.py | Combined plan + build workflow | issue-number | Plan + committed code |
| ICDEV™ Plan+Build+Test | tools/ci/workflows/icdev_plan_build_test.py | Combined plan + build + test workflow | issue-number | Plan + code + test results |
| ICDEV™ Plan+Build+Test+Review | tools/ci/workflows/icdev_plan_build_test_review.py | Full SDLC pipeline (explicit variant) | issue-number | All artifacts |
| CLAUDE.md Card Regenerator | tools/docs/claude_md_cards.py | Moves card essays out of `### Essential Commands` into docs/reference/cards/, IDEMPOTENTLY (xrv-docs-02). The trim was originally done BY HAND and did not survive: the branch's own later commit carried CLAUDE.md at 371,288 bytes with every essay back inside the fence while the 82 extracted records and the `#### Card records` index were still correct. EVERY card appends to CLAUDE.md, so it conflicts on nearly every merge and each resolution is another chance to take the side that still has the essays -- a restructure re-appliable only by hand gets undone by hand. NOTHING IS DELETED THAT IS NOT FIRST WRITTEN DOWN: an essay leaves the fence only after its record exists on disk AND an index line points at it, both verified by RE-READING; a record that cannot be written is left INLINE and reported. A BLANK LINE separates a real header from prose that merely ends in `(210)` -- measured, that false positive sits four lines into the rem-hyg-13 essay and would have truncated it and written a `210.md`. Applied 2026-09-12: 371,288 -> 79,941 bytes, 71 essays trimmed, 0 remaining, and a second run reports `changed: false`. Report-only under `--check` (exit 1 on drift). Mirrored to icdev/tools/docs/. | `--check` / `--apply` / `--dry-run` / `--json` | `{state, bytes, inline_essays, prelude_commands, records_on_disk, indexed, missing_record[], missing_index[], changed, trimmed, kept_inline[]}` |

