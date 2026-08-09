# Safety Hooks

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Shared Pre-Tool Checks | tools/hooks/shared_checks.py | The ONE implementation of every pre-tool safety check: .env access, dangerous rm, append-only table writes, direct `sqlite3.connect()`, D-ORCH-8 file access tiers, unmerged remote-branch deletion, worktree path enforcement, review-loop pre-commit, destructive-git blocklist. Repo root from `__file__`, never `os.getcwd()` | (library) `check_*(tool_name, tool_input)` | `Optional[str]` block reason (None = allow) |
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Claude Code entry point — holds `APPEND_ONLY_TABLES` and delegates every check to `tools/hooks/shared_checks.py` | tool_name, tool_input on stdin | exit 0 = allow, exit 2 = block |
| Headless Pre-Tool Guard | tools/airgap/hook_compat.py | Same checks for every non-Claude-Code orchestrator (SAG, MCP gateway, cron), via the same shared module | `run_pre_tool_check(tool_name, tool_input)` | `{"allowed": bool, "reason": str}` |

Both hook paths import `tools/hooks/shared_checks.py` so they cannot drift apart
(hgx-guard-01). The canonical `APPEND_ONLY_TABLES` list deliberately stays in
`.claude/hooks/pre_tool_use.py` — CLAUDE.md's guardrail, the child-app
generator's per-schema filter and `coherence_checker`'s autofix all read it
there; the caller passes its list into the shared check.

