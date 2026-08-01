-- CUI // SP-CTI
-- Migration 289: agent_cron_jobs + agent_cron_runs — user-facing cron for the
-- standalone agent (sag-cron-01).
--
-- Backs tools/agent_runtime/cron.py. A durable, user-managed schedule store that
-- rides ICDEV's existing daemon/reflex ticking (the agent_cron Genesis reflex
-- calls run_due_jobs) rather than adding a new loop.
--
--   * agent_cron_jobs  — one row per scheduled job (mutable state: pause/resume,
--     next_run_at bumps, retry attempt counter). NOT append-only.
--   * agent_cron_runs  — append-only execution log (one row per run attempt:
--     success/failure, truncated output, delivery outcome). Added to
--     APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.
--
-- Conventions (mirror migrations 287/288): TEXT-only, dialect-neutral, CREATE
-- TABLE IF NOT EXISTS is idempotent (PostgreSQL primary, SQLite init/test
-- fallback). First-class tenant_id + classification make the jobs table
-- RLS-eligible. The runtime module also self-creates both via _ensure_schema(),
-- so a checkout that has not run this migration still degrades gracefully.

CREATE TABLE IF NOT EXISTS agent_cron_jobs (
    id                    TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    user_id               TEXT NOT NULL DEFAULT 'default',
    tenant_id             TEXT DEFAULT '',
    classification        TEXT DEFAULT 'CUI',
    mode                  TEXT NOT NULL,          -- 'agent' | 'script'
    payload               TEXT NOT NULL,          -- agent prompt or allowlisted command
    schedule_kind         TEXT NOT NULL,          -- 'interval' | 'cron'
    schedule_expr         TEXT NOT NULL,          -- '900' seconds or '*/5 * * * *'
    delivery              TEXT DEFAULT 'log',     -- 'log' | 'email' | 'gateway:<ch>:<chat>'
    status                TEXT DEFAULT 'active',  -- 'active' | 'paused'
    max_retries           INTEGER DEFAULT 0,
    retry_backoff_seconds INTEGER DEFAULT 60,
    attempt               INTEGER DEFAULT 0,      -- current retry attempt (0 = fresh)
    next_run_at           TEXT,
    last_run_at           TEXT,
    last_status           TEXT,
    run_count             INTEGER DEFAULT 0,
    created_at            TEXT,
    updated_at            TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_cron_jobs_due
    ON agent_cron_jobs (status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_agent_cron_jobs_user
    ON agent_cron_jobs (user_id);

CREATE TABLE IF NOT EXISTS agent_cron_runs (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    success         INTEGER,
    attempt         INTEGER DEFAULT 0,
    mode            TEXT,
    output          TEXT,
    error           TEXT,
    delivered       INTEGER DEFAULT 0,
    delivery_target TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_cron_runs_job
    ON agent_cron_runs (job_id, started_at);
