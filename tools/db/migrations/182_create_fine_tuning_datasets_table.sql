-- CUI // SP-CTI
-- Migration 182: Create fine_tuning_datasets table
-- Addresses the orphan_db_table gap (op-gap-640fb72cbc8097c9): tools/agentic_ai_canvas/ft_linkage.py
-- INSERTs/SELECTs fine_tuning_datasets, but the table existed only in the consolidated
-- PG snapshot (pg_consolidated.sql) with no matching migration — so replayed migration
-- chains and SQLite test/dev DBs never created it. Columns mirror pg_consolidated.sql.

-- @sqlite-only
CREATE TABLE IF NOT EXISTS fine_tuning_datasets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL DEFAULT 'unknown',
    source_id       TEXT NOT NULL DEFAULT '',
    input_text      TEXT NOT NULL DEFAULT '',
    expected_output TEXT DEFAULT '',
    quality_signal  REAL DEFAULT 0.5,
    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    classification  TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_ftd_quality ON fine_tuning_datasets (quality_signal);
CREATE INDEX IF NOT EXISTS idx_ftd_source ON fine_tuning_datasets (source);
CREATE UNIQUE INDEX IF NOT EXISTS uix_ftd_source_id ON fine_tuning_datasets (source, source_id);

-- @pg-only
CREATE TABLE IF NOT EXISTS fine_tuning_datasets (
    id              SERIAL PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'unknown',
    source_id       TEXT NOT NULL DEFAULT '',
    input_text      TEXT NOT NULL DEFAULT '',
    expected_output TEXT DEFAULT '',
    quality_signal  REAL DEFAULT 0.5,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    classification  VARCHAR(50) DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_ftd_quality ON fine_tuning_datasets (quality_signal);
CREATE INDEX IF NOT EXISTS idx_ftd_source ON fine_tuning_datasets (source);
CREATE UNIQUE INDEX IF NOT EXISTS uix_ftd_source_id ON fine_tuning_datasets (source, source_id);
