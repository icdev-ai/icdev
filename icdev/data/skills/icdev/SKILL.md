---
name: icdev
description: Work inside an ICDEV project from any coding harness -- the icdev-unified MCP tools, the `icdev` CLI, kanban claim/release, worktree-first branching, and where ICDEV's pre-tool guard lives. Use when a repo has CLAUDE.md/AGENTS.md naming ICDEV, a kanban task id (e.g. omx-dx-02), or you need an ICDEV tool.
---

# ICDEV

ICDEV orchestrates coding harnesses; it is not one. You (Claude Code, Codex, opencode,
Pi, Gemini, Hermes, ...) are the harness. ICDEV supplies tools, a task board and a guard.
AI orchestrates; deterministic Python tools execute.

## Tools
- **MCP:** the `icdev-unified` server exposes the platform's tools (kanban, compliance,
  RAG, security scans). Prefer an MCP tool over hand-written SQL or scripts.
- **CLI:** `icdev --help`. Common: `icdev init`, `icdev status`, `icdev enable <name>`,
  `icdev profile list`, `icdev skill install|uninstall`.
- Before writing a new script, grep `tools/manifest/` -- the tool probably exists.
- Full reference: `docs/reference/commands.md`.

## Kanban
- Claim before you build: `python tools/kanban/cli.py --claim <task-id> --intent "<why>"`;
  release with `--release <task-id>`. The autonomous runner skips claimed tasks.
- `done` is merge-verified: `python tools/kanban/cli.py --set-status <id> done --merge`.

## Worktree-first
- Never commit on `main` and never edit the shared checkout. Create a worktree:
  `git worktree add -b kanban/<task-id> "$(python -m tools.git.worktree_paths --path cli <slug>)" origin/main`.
- Push, open a PR, wait for green CI.

## The guard
- Every check lives ONCE in `tools/hooks/shared_checks.py`. Claude Code reaches it via
  `.claude/hooks/pre_tool_use.py`; every other harness via
  `tools/airgap/hook_compat.py::run_pre_tool_check`. Exit 2 means BLOCKED -- do not retry
  around it, and never wrap a guard in `|| true`.

## Rules of thumb
- Read the project `CLAUDE.md` / `AGENTS.md` first; it overrides this skill.
- No model IDs in code -- route through `LLMRouter` + `args/llm_config.yaml`.
- Audit tables are append-only. Runbook for non-Claude harnesses: `docs/ops/airgap-runbook.md`.
