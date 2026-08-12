# Safety Hooks

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Shared Pre-Tool Checks | tools/hooks/shared_checks.py | The ONE implementation of every pre-tool safety check: .env access, dangerous rm, append-only table writes, direct `sqlite3.connect()`, D-ORCH-8 file access tiers, unmerged remote-branch deletion, worktree path enforcement, review-loop pre-commit, destructive-git blocklist. Repo root from `__file__`, never `os.getcwd()` | (library) `check_*(tool_name, tool_input)` | `Optional[str]` block reason (None = allow) |
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Claude Code entry point — holds `APPEND_ONLY_TABLES` and delegates every check to `tools/hooks/shared_checks.py` | tool_name, tool_input on stdin | exit 0 = allow, exit 2 = block |
| Headless Pre-Tool Guard | tools/airgap/hook_compat.py | Same checks for every non-Claude-Code orchestrator (SAG, MCP gateway, cron), via the same shared module | `run_pre_tool_check(tool_name, tool_input)` | `{"allowed": bool, "reason": str}` |
| PreToolUse Fire-Rate Survey | tools/hooks/fire_rate_survey.py | exa-bench-05 — replays real session tool calls through every PreToolUse check and counts what each would refuse, so a check is never enabled unmeasured. Corpus is the Claude Code transcripts (`~/.claude/projects/**/*.jsonl`), the only source carrying the OPERANDS; `hook_events` persists tool-input key names only and is reported unusable rather than contributing a misleading zero. Six checks are replayed; `branch_deletion`, `agent_rules` and `review_loop_precommit` are `trigger_only` because evaluating them needs live refs, writes an `agent_findings` row, or runs ruff and re-stages files | `--json`, `--markdown`, `--since-days N`, `--project SUBSTR`, `--check NAME`, `--samples N`, `--live-git`, `--gate --max-fire-rate F` | per-check `fired` / `fire_rate` / `distinct_operands` / `sessions_affected`; exit 1 under `--gate` |

Both hook paths import `tools/hooks/shared_checks.py` so they cannot drift apart
(hgx-guard-01). The canonical `APPEND_ONLY_TABLES` list deliberately stays in
`.claude/hooks/pre_tool_use.py` — CLAUDE.md's guardrail, the child-app
generator's per-schema filter and `coherence_checker`'s autofix all read it
there; the caller passes its list into the shared check.

The Claude Code hook's exit 2 is load-bearing again as of exa-bench-05:
`.claude/settings.json` no longer wraps it in `|| true`, so a refusal reaches
the caller. Stand it down with `ICDEV_PRETOOLUSE_ENFORCE=0` (all nine checks
still run and print, prefixed `ADVISORY:`) or with the per-check
`ICDEV_*_GUARD=0` switches listed in `CHECK_KILL_SWITCHES`. Re-measure before
changing any check: `python tools/hooks/fire_rate_survey.py --json`.

