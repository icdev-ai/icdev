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
