# Goal: Cross-Session Coordination

Prevent concurrent agent sessions (Claude Code CLI, Cursor, the Kanban
scheduler + its dispatched runs, any other LLM agent) from colliding on the same
repo + PostgreSQL DB. **LLM-agnostic**: all logic lives in `tools/coordination/`
and is callable by any agent; Claude sessions additionally auto-coordinate via
`.claude/hooks/coordination.py`.

## What it prevents
1. **DB lock storms** — server-side PG timeouts (`storage.py`,
   `idle_in_transaction_session_timeout=30s` + `lock_timeout=10s`) make a leaked
   `idle in transaction` auto-roll-back and stuck DDL fail fast. Migrations can
   serialize via `dblock.advisory_lock("migrations:schema")`.
2. **Sessions overriding one another** — a session claims a *lease* on a resource;
   others SEE it. Hybrid enforcement: **hard** (refuse/queue) for
   `service:*`, `git:*`, `migration:*`; **warn-only** for `file:*`.

## Library (any agent)
```python
from tools.coordination import session_registry as reg, leases, dblock, gitlock
reg.register(intent="what I'm doing")          # + reg.heartbeat() periodically
reg.others()                                   # other active sessions
h = leases.acquire("service:dashboard", intent="restart", block=True)  # None = refused
who = leases.holder("file:tools/x.py")         # None or holder dict (warn)
with dblock.advisory_lock("migrations:schema"): run_migrations()
with gitlock.repo_commit_lock(): git_add_commit()   # serialize auto-commits
```

## CLI (LLM-agnostic — for non-Claude agents)
```bash
python -m tools.coordination status                 # sessions + leases
python -m tools.coordination register --intent "…"  # + heartbeat / end
python -m tools.coordination sessions --json
python -m tools.coordination lease-acquire file:path/to/x.py --intent edit
python -m tools.coordination lease-release service:dashboard
```
A non-Claude agent opts in by setting `ICDEV_SESSION_ID` (+ optional
`ICDEV_AGENT=cursor`) and calling the CLI/library.

## How each entry path coordinates
| Path | Mechanism |
|------|-----------|
| **Claude Code CLI** (interactive + headless `claude -p`) | `.claude/settings.json` hooks → `coordination.py` auto register/heartbeat (UserPromptSubmit), file-edit warn (PreToolUse), release (Stop) |
| **Kanban scheduler** (daemon) | `tools/genesis/kanban_scheduler.py` registers as session `kanban-scheduler` + heartbeats each cycle |
| **Kanban-dispatched tasks** | dispatched as Claude CLI → coordinate via the same hooks (distinct session id per run) |
| **Cursor / other LLM agents** | CLI/library with `ICDEV_SESSION_ID` + `ICDEV_AGENT=cursor` |

## Modules
- `tools/coordination/constants.py` — identity (`get_session_id`/`get_agent_type`), TTLs, namespaces
- `tools/coordination/session_registry.py` — `agent_sessions` table (self-creating) + heartbeat liveness
- `tools/coordination/leases.py` — filelock-backed named leases (metadata + TTL, cross-process)
- `tools/coordination/dblock.py` — PostgreSQL advisory locks
- `tools/coordination/gitlock.py` — repo-wide commit serialization
- `tools/coordination/__main__.py` — CLI

## Known limitation
`file:*` leases are **advisory (warn-only)** by design — they cannot stop an
automated `git`/save overwrite, only warn a cooperating agent. For files that
must not be clobbered, use a `git:` lock around the write or land changes during
a quiet window. (Observed live: a settings.json edit was reverted by another
session's auto-commit despite a held file lease — exactly why the warning exists.)

## Verify
```bash
python -m tools.coordination status
# two shells with distinct ICDEV_SESSION_ID -> each sees the other; file edit warns; service refused
pytest tests/test_coordination.py -q
```
