# AI Developer — Capability Scope

## Permitted Tools
- **Read, Edit, Write** — full file access within project root
- **Grep, Glob** — codebase search
- **Bash** — run `ruff`, `pytest`, `python tools/...`, `git status/diff/log`
- **PowerShell** — Windows-compatible equivalents of Unix commands

## Restricted Tools (HITL required)
- **Bash (git push / git reset --hard)** — requires HITL or explicit user instruction
- **Bash (rm -rf / destructive ops)** — requires HITL
- **Write to .env** — never modify secrets without explicit authorization

## Explicitly Forbidden
- `sqlite3.connect()` directly — always use `get_connection()` or `get_canvas_connection()`
- Hardcoding model IDs or API keys in Python source
- Skipping `--no-verify` hooks
- Auto-merging to `main` or `irad/feature` without human review

## Primary Modules
- `icdev/tools/` — canonical package for all new code
- `tools/db/storage.py` — database access layer
- `tools/llm/router.py` — LLM routing (never hardcode provider)
- `tools/workflow/coherence_checker.py` — coherence gate
- `pytest tests/ -v --tb=short` — test runner
