-- CUI // SP-CTI
-- Migration 288: remote_agent_sessions — gateway agent-mode session map (sag-gw-01)
--
-- Maps a messaging (channel, chat_id) to a standalone-agent runtime (SAG) chat
-- context so a bound user's conversation with the gateway agent resumes across
-- messages. Backs tools/gateway/agent_mode.py.
--
-- Conventions (mirror migration 287 sag_user_profiles): TEXT-only, dialect-
-- neutral, CREATE TABLE IF NOT EXISTS is idempotent (PostgreSQL primary, SQLite
-- init/test fallback). NOT append-only — a mapping is mutable state (last
-- activity bumps; a stale mapping is replaced by a fresh session). The runtime
-- module also self-creates this via _ensure_schema(), so a checkout that has not
-- run this migration still degrades gracefully. The full command execution audit
-- trail lives in the append-only remote_command_log / audit_log — this table is
-- routing state only, so it carries no classification column and is not RLS-gated
-- (parity with remote_user_bindings).

CREATE TABLE IF NOT EXISTS remote_agent_sessions (
    id               TEXT PRIMARY KEY,
    channel          TEXT NOT NULL,
    chat_id          TEXT NOT NULL,
    icdev_user_id    TEXT,
    tenant_id        TEXT DEFAULT '',
    context_id       TEXT NOT NULL,
    created_at       TEXT,
    last_activity_at TEXT,
    UNIQUE (channel, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_remote_agent_sessions_lookup
    ON remote_agent_sessions (channel, chat_id);
