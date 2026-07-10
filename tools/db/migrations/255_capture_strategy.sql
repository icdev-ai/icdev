-- Migration: 255_capture_strategy
-- CUI // SP-CTI
-- Table: capture_strategy — company-wide capture strategy and win themes.
--
-- Singleton config (is_active = TRUE, newest row wins), mirroring the
-- rfi_company_style_guide pattern. Read at prompt-assembly time by
-- tools/govcon/capture_strategy.py so every generated RFI part and proposal
-- section inherits one message architecture instead of being drafted in isolation.
--
-- This is MUTABLE CONFIG, not an audit table: it must NOT be added to
-- APPEND_ONLY_TABLES. Edit history is written to audit_trail on save.
--
-- No init_icdev_db.py / pg_consolidated.sql companion entry is needed: the whole
-- rfi_workbench_* family is created by migrations 236+ rather than by the squashed
-- schema (pg_consolidated.sql predates them), exactly as rfi_company_style_guide
-- does in migration 238. This file therefore runs on fresh databases too.
--
-- JSON columns are stored as TEXT and parsed in Python. Per CLAUDE.md, runtime
-- call sites must not use SQLite-dialect JSON SQL and rely on translate_sql.

CREATE TABLE IF NOT EXISTS capture_strategy (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT 'default',
    is_active       INTEGER NOT NULL DEFAULT 1,
    golden_thread   TEXT NOT NULL DEFAULT '',
    win_themes      TEXT NOT NULL DEFAULT '[]',
    discriminators  TEXT NOT NULL DEFAULT '[]',
    proof_points    TEXT NOT NULL DEFAULT '[]',
    ghosting        TEXT NOT NULL DEFAULT '[]',
    hot_buttons     TEXT NOT NULL DEFAULT '[]',
    updated_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_capture_strategy_active ON capture_strategy(is_active);

-- Bridge: an RFI workbench session may be tied to a proposal opportunity so both
-- surfaces resolve per-pursuit overrides from the same pg_win_themes registry.
-- Nullable — an unlinked session falls back to the global strategy.

-- @pg-only
ALTER TABLE rfi_workbench_sessions ADD COLUMN IF NOT EXISTS opportunity_id TEXT;

-- @sqlite-only
-- SQLite has no ADD COLUMN IF NOT EXISTS; the runner applies each migration once.
ALTER TABLE rfi_workbench_sessions ADD COLUMN opportunity_id TEXT;
