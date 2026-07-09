-- Migration 251: RFI Capability-Gap Demand Loop stores (rfidem-store-01/02).
-- Materializes rfi_capability_gaps + rfi_gap_task_links for databases bootstrapped
-- before tools/govcon/init_db.py added them. When an RFI requirement is both an
-- ICDEV capability-catalog miss (grade N) AND flagged uncovered/partial by the
-- workbench, it is recorded here as a demand signal, aggregated across RFIs by
-- content_hash (frequency/priority), then decomposed into SUGGESTED kanban tasks.
-- Idempotent: safe where init_govcon_intelligence_tables() already created them.

CREATE TABLE IF NOT EXISTS rfi_capability_gaps (
    content_hash TEXT PRIMARY KEY,
    capability_need TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    domain TEXT,
    frequency INTEGER NOT NULL DEFAULT 1,
    velocity REAL NOT NULL DEFAULT 0.0,
    best_coverage REAL NOT NULL DEFAULT 0.0,
    priority REAL NOT NULL DEFAULT 0.0,
    is_high_demand INTEGER NOT NULL DEFAULT 0,
    rfi_refs TEXT NOT NULL DEFAULT '[]',
    prediction_id TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    -- timestamps are TEXT (ISO-8601) always supplied by the app (_now()); no DB
    -- default so this DDL is portable to PostgreSQL (no timestamptz->text cast).
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    classification TEXT DEFAULT 'CUI'
);

-- Append-only provenance (NIST AU): gap -> emitted kanban task, via route.
CREATE TABLE IF NOT EXISTS rfi_gap_task_links (
    id TEXT PRIMARY KEY,
    gap_hash TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    route TEXT NOT NULL DEFAULT 'direct',
    emitted_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    UNIQUE (gap_hash, task_id)
);

CREATE INDEX IF NOT EXISTS idx_rfi_gaps_priority ON rfi_capability_gaps(priority);
CREATE INDEX IF NOT EXISTS idx_rfi_gaps_status   ON rfi_capability_gaps(status);
CREATE INDEX IF NOT EXISTS idx_rfi_gaps_high     ON rfi_capability_gaps(is_high_demand);
CREATE INDEX IF NOT EXISTS idx_rfi_gap_links_gap ON rfi_gap_task_links(gap_hash);

-- Allow the ACF Foundry to harvest RFI demand signals: extend the
-- foundry_signals.source_engine CHECK with 'rfi' (mirrors SOURCE_ENGINES in
-- tools/foundry/constants.py). Live PG tables predate this value; fresh DBs get
-- it from init_db. Guarded so it is a no-op where foundry_signals is absent.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'foundry_signals') THEN
        ALTER TABLE foundry_signals DROP CONSTRAINT IF EXISTS foundry_signals_source_engine_check;
        ALTER TABLE foundry_signals ADD CONSTRAINT foundry_signals_source_engine_check
            CHECK (source_engine IN ('innovation','creative','research','genesis','rfi','telemetry'));
    END IF;
END $$;
