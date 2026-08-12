# Safety Hooks

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Safety Hooks
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Shared Pre-Tool Checks | tools/hooks/shared_checks.py | The ONE implementation of every pre-tool safety check: .env access, dangerous rm, append-only table writes, direct `sqlite3.connect()`, D-ORCH-8 file access tiers, unmerged remote-branch deletion, worktree path enforcement, review-loop pre-commit, destructive-git blocklist. Repo root from `__file__`, never `os.getcwd()` | (library) `check_*(tool_name, tool_input)` | `Optional[str]` block reason (None = allow) |
| Pre-Tool-Use Hook | .claude/hooks/pre_tool_use.py | Claude Code entry point — holds `APPEND_ONLY_TABLES` and delegates every check to `tools/hooks/shared_checks.py` | tool_name, tool_input on stdin | exit 0 = allow, exit 2 = block |
| Headless Pre-Tool Guard | tools/airgap/hook_compat.py | Same checks for every non-Claude-Code orchestrator (SAG, MCP gateway, cron), via the same shared module | `run_pre_tool_check(tool_name, tool_input)` | `{"allowed": bool, "reason": str}` |
| Worktree Write Containment | tools/hooks/shared_checks.py | exa-bench-07. Refuses a write whose **resolved** target (`..` and symlinks followed, `~`/`$HOME` expanded, drive-relative and UNC treated as outside) is outside the session worktree, the main checkout it is linked to, and the scratch roots `tools/git/worktree_paths.is_sanctioned` blesses. The boundary D-ORCH-8's `args/file_access_tiers.yaml` cannot express — a glob list enumerates paths, a boundary covers the ones nobody enumerated. Anchored on the containing **worktree**, not the repo root: `AgentSession.working_dir` is what `claude_cli` passes as cwd. Bash targets read from `touch`/`mkdir`/`cp`/`mv`/`ln`/`dd of=`/`curl -o`/`wget -O` as well as redirects and `tee`, since those write with no operator to match. Main checkout resolved from `<anchor>/.git` rather than `git rev-parse` — no subprocess on the per-tool-call path. Fails OPEN on any resolution error. `ICDEV_WRITE_BOUNDARY_GUARD=0` disables, `=monitor` records without refusing, `ICDEV_WRITE_BOUNDARY_EXTRA_ROOTS` (os.pathsep-joined) sanctions more roots | (library) `check_write_outside_worktree(tool_name, tool_input, repo_root=None)`, `outside_write_root(raw, ...)`, `sanctioned_write_roots(...)`, `bash_write_targets(command)` | `Optional[str]` block reason (None = allow) |
| Egress Fire Rate | tools/security/egress_fire_rate.py | Measure how often `check_network_egress` would fire before flipping `agent_egress.enforce` (exa-bench-08). Reads the hook's findings sink, or replays a Claude Code transcript corpus | `--sink` (default) / `--corpus [DIR]` / `--top N` / `--json` | verdict counts, block rate %, top unapproved hosts |
| PreToolUse Fire-Rate Survey | tools/hooks/fire_rate_survey.py | exa-bench-05 — replays real session tool calls through every PreToolUse check and counts what each would refuse, so a check is never enabled unmeasured. Corpus is the Claude Code transcripts (`~/.claude/projects/**/*.jsonl`), the only source carrying the OPERANDS; `hook_events` persists tool-input key names only and is reported unusable rather than contributing a misleading zero. Every check the hook declares in `HOOK_CHECKS` is covered — a test pins the two lists equal, since a check missing from the survey is a check enabled unmeasured; `branch_deletion`, `agent_rules` and `review_loop_precommit` are `trigger_only` because evaluating them needs live refs, writes an `agent_findings` row, or runs ruff and re-stages files, and `network_egress` is replayed with `ICDEV_EGRESS_GUARD_ENFORCE=1` because at its shipped default it refuses nothing and would report a meaningless 0% | `--json`, `--markdown`, `--since-days N`, `--project SUBSTR`, `--check NAME`, `--samples N`, `--live-git`, `--gate --max-fire-rate F` | per-check `fired` / `fire_rate` / `distinct_operands` / `sessions_affected`; exit 1 under `--gate` |

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

