-- CUI // SP-CTI
-- Migration 183: Create cli_llm_jobs table for the CLI LLM bridge job store.
--
-- Backs tools/llm/cli_bridge/job_store.py (uclb-job-02) and the soft-wait /
-- deferral flow in CLILLMProvider.invoke (uclb-job-03). The table is MUTABLE
-- (status transitions pending -> running -> done/error) and therefore is NOT in
-- APPEND_ONLY_TABLES. Rows carry tenant_id/classification so access through
-- tools.db.storage.get_connection is RLS-aware (the uclb-job-01 deliverable was
-- marked done but never landed; this migration supplies the missing table).

-- @sqlite-only
CREATE TABLE IF NOT EXISTS cli_llm_jobs (
    id             TEXT PRIMARY KEY,
    function       TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    system_prompt  TEXT DEFAULT '',
    model_id       TEXT,
    backend        TEXT DEFAULT 'auto',
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'error')),
    result         TEXT,
    error          TEXT,
    context_id     TEXT,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at     TEXT,
    updated_at     TEXT,
    claimed_at     TEXT,
    completed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_cli_llm_jobs_claim ON cli_llm_jobs (status, backend, created_at);
CREATE INDEX IF NOT EXISTS idx_cli_llm_jobs_context ON cli_llm_jobs (context_id);

-- @pg-only
CREATE TABLE IF NOT EXISTS cli_llm_jobs (
    id             TEXT PRIMARY KEY,
    function       TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    system_prompt  TEXT DEFAULT '',
    model_id       TEXT,
    backend        TEXT DEFAULT 'auto',
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'error')),
    result         TEXT,
    error          TEXT,
    context_id     TEXT,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at     TEXT,
    updated_at     TEXT,
    claimed_at     TEXT,
    completed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_cli_llm_jobs_claim ON cli_llm_jobs (status, backend, created_at);
CREATE INDEX IF NOT EXISTS idx_cli_llm_jobs_context ON cli_llm_jobs (context_id);
