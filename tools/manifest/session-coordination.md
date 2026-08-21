# Cross-Session Coordination (`tools/coordination/`)

LLM-agnostic primitives so concurrent agent sessions (Claude Code CLI, Cursor,
the Kanban scheduler + dispatched runs, any agent) coordinate and don't collide.
Goal: [goals/session_coordination.md](../../goals/session_coordination.md).

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Session Registry | tools/coordination/session_registry.py | `agent_sessions` table (self-creating) + heartbeat liveness. `register()/heartbeat()/list_active()/others()/end_session()/reap_stale()` | intent str | session rows |
| Leases | tools/coordination/leases.py | filelock-backed named resource leases (metadata + TTL, cross-process). `acquire()/holder()/release()/release_all_for_session()/list_leases()`. Hard (service/git/migration) refuse; soft (file) warn | resource, intent, ttl | Lease handle / holder dict |
| DB Advisory Lock | tools/coordination/dblock.py | PostgreSQL advisory locks: `advisory_lock(name)` ctx mgr, `try_acquire()/release()`. Serializes migrations / hot-table critical sections; no-op on SQLite | lock name | held/None |
| Git Commit Lock | tools/coordination/gitlock.py | `repo_commit_lock()` — repo-wide filelock to serialize concurrent git auto-commits | timeout | held bool |
| Coordination CLI | tools/coordination/__main__.py | `python -m tools.coordination status\|whoami\|register\|heartbeat\|end\|sessions\|reap\|lease-acquire\|lease-release\|leases` | argv | text/JSON |
| Identity | tools/coordination/constants.py | `get_session_id()` (CLAUDE/ICDEV_SESSION_ID), `get_agent_type()` (ICDEV_AGENT), TTLs, namespaces | env | str |
| Claude hook | .claude/hooks/coordination.py | Auto register/heartbeat (UserPromptSubmit), file-edit warn (PreToolUse), release (Stop). Wired in .claude/settings.json | hook stdin | exit 0 |

DB safety (companion fix): `tools/db/storage.py` sets
`idle_in_transaction_session_timeout` + `lock_timeout` on every pooled PG
connection (`.env`: `ICDEV_PG_IDLE_TXN_TIMEOUT_MS`, `ICDEV_PG_LOCK_TIMEOUT_MS`).
Tests: `tests/test_coordination.py`. Kanban scheduler registers via
`tools/genesis/kanban_scheduler.py`.
| Process Code Identity | tools/coordination/code_identity.py | Records WHICH CODE a live process is running, frozen at boot and never recomputed (`code_reload.pull_if_safe` moves HEAD underneath a running daemon, so a re-read would report `current` at the moment it went stale). Persisted onto `agent_sessions` by `session_registry.register`. `code_version=None` means UNKNOWN and never reads as current; `code_version_source` says why (git/env/unavailable); `code_dirty` is a separate axis. (autonomy-id-01) | --boot, --json | Per-process identity; `processes()` fleet reader (recorded/unknown, never a fabricated version) |
| Service Session Identity | tools/coordination/service_identity.py | autonomy-sid-01: a long-running service session id that is DISTINCT PER PROCESS and still readable. Observed live 2026-08-20: two kanban_scheduler processes were dispatching onto the same board (the supervisor and one from a task worktree with its own .env), and each did `setdefault("ICDEV_SESSION_ID", "kanban-scheduler")` — so both presented the SAME id. `leases.acquire` refuses a hard lease held by ANOTHER live session, and `kanban:task:<id>` IS hard (RES_KANBAN in HARD_NAMESPACES), so the refusal was armed, correct and BLIND: the guard against two workers on one task could not tell them apart. `session_registry.list_active()` likewise showed ONE row, carrying the worktree pid, because the two upsert over each other. NOT what adm-03 fixed — that made liveness require a heartbeat ("is the holder ALIVE"); this is "is the holder ME". `<name>-<pid>`, and the pid is deliberate: `code_reload` re-execs these daemons through `os.execv`, which KEEPS the pid, so a self-updating daemon keeps the leases it held — a uuid minted at boot would orphan them on every code change. The name stays a prefix because the id is read by humans in list_active(), by merge_stall attribution and by the coordination hook. PID reuse is already guarded (lease TTL + holder_is_alive + adm-03 heartbeat) and deliberately not re-guarded here. An id already set by an orchestrator always wins. Used by kanban_scheduler, genesis daemon and pr_watcher; a test asserts all three claim it rather than re-inventing the scheme. | (library) | `service_session_id(name, pid)`, `claim_service_identity(name, agent)`, `is_service_session(id, name)` |
