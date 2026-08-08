# Safety Hooks

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Shared Pre-Tool Checks | tools/hooks/shared_checks.py | The ONE implementation of every pre-tool safety check, plus the canonical `APPEND_ONLY_TABLES` literal: .env access, dangerous rm, append-only table writes, direct `sqlite3.connect()`, D-ORCH-8 file access tiers, unmerged remote-branch deletion, worktree path enforcement, review-loop pre-commit, destructive-git blocklist. Repo root from `__file__`, never `os.getcwd()` | `evaluate(tool_name, tool_input, path=...)` | `CheckOutcome(allowed, reason, check, warnings)` |
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Claude Code entry point — a thin adapter that delegates every check to `tools/hooks/shared_checks.py` | tool_name, tool_input on stdin | exit 0 = allow, exit 2 = block |
| Headless Pre-Tool Guard | tools/airgap/hook_compat.py::run_pre_tool_check | Same checks for every non-Claude-Code orchestrator (agent loop, ACE, MCP gateway, cron), via the same shared module. Classifies by input shape, so an unrecognised tool name is scanned rather than waved through | `run_pre_tool_check(tool_name, tool_input)` | `{"allowed", "reason", "check", "warnings"}` |
| Headless Post-Tool Hook | tools/airgap/hook_compat.py::run_post_tool_check | Headless analogue of `.claude/hooks/post_tool_use.py` — audits to `hook_events` and fires `TOOL_EXECUTE_AFTER`. Observational; never blocks, never raises | `(tool_name, tool_input, tool_output, is_error)` | `{"recorded", "event_id", "dispatched"}` |
| Headless Stop Hook | tools/airgap/hook_compat.py::run_stop_check | Headless analogue of `.claude/hooks/stop.py` — audits the stop on `hook_events` and honours `ICDEV_AUTO_COMMIT` | `(reason, session_id, payload, auto_commit_message)` | `{"recorded", "event_id", "auto_commit"}` |
| Agent-Loop Hook Adapters | tools/airgap/hook_compat.py::agent_loop_hooks | The three guards shaped for `run_agent_loop`'s `PreToolUseHook`/`PostToolUseHook`/`StopHook` slots — `run_agent_loop(..., **agent_loop_hooks())` | `agent_loop_hooks(auto_commit_message=None)` | `{"on_pre_tool_use", "on_post_tool_use", "on_stop"}` |

Both pre-tool paths resolve to `tools/hooks/shared_checks.py`, so they cannot
drift apart (hgx-guard-01 extracted it; hgx-guard-02 brought the headless path
to full parity and added the post-tool/stop analogues). The canonical
`APPEND_ONLY_TABLES` literal lives there too — register a new append-only table
by editing that list. `tests/test_hgx_guard_parity.py` asserts the parity off
the shared registry itself, so a Claude-Code-only check fails the suite rather
than quietly leaving unattended agents less guarded than interactive ones.

