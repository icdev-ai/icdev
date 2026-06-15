-- CUI // SP-CTI
-- Migration 186: Create foundry_* tables for ACF (Autonomous Capability Foundry).
--
-- Idempotently creates the six platform tables that back the ACF pipeline
-- (harvester -> synthesizer -> scorer -> deliberator -> CoD -> SIPA).
-- Rows carry tenant_id + classification so all queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- These tables are NOT in pg_consolidated.sql (they post-date the snapshot)
-- so both SQLite and PostgreSQL paths are required.
--
-- Append-only: foundry_runs / foundry_signals / foundry_specs /
-- foundry_tasks_emitted / foundry_outcomes.
-- Mutable: foundry_concepts (status transitions).
--
-- CHECK constraints mirror tools.foundry.constants single-source-of-truth.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS foundry_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_at           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    harvested          INTEGER NOT NULL DEFAULT 0,
    concepts_proposed  INTEGER NOT NULL DEFAULT 0,
    concepts_approved  INTEGER NOT NULL DEFAULT 0,
    tasks_emitted      INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'completed', 'failed', 'deferred', 'rate_limited', 'circuit_open')),
    detail             TEXT    DEFAULT '{}',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_runs_tenant ON foundry_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_foundry_runs_status ON foundry_runs(status);

CREATE TABLE IF NOT EXISTS foundry_signals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT    NOT NULL,
    source_engine      TEXT    NOT NULL
                       CHECK (source_engine IN ('innovation', 'creative', 'research', 'genesis', 'telemetry')),
    source_ref         TEXT    NOT NULL,
    theme              TEXT,
    raw_score          REAL    DEFAULT 0.0,
    keywords           TEXT    DEFAULT '[]',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_run    ON foundry_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_engine ON foundry_signals(source_engine);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_score  ON foundry_signals(raw_score);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_tenant ON foundry_signals(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_concepts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT    NOT NULL,
    name               TEXT    NOT NULL,
    slug               TEXT    NOT NULL UNIQUE,
    problem_statement  TEXT,
    proposed_capability TEXT,
    target_users       TEXT,
    cluster_signal_ids TEXT    DEFAULT '[]',
    novelty_score      REAL    DEFAULT 0.0,
    market_score       REAL    DEFAULT 0.0,
    fit_score          REAL    DEFAULT 0.0,
    effort_estimate    REAL    DEFAULT 0.0,
    compliance_risk    REAL    DEFAULT 0.0,
    composite_score    REAL    DEFAULT 0.0,
    status             TEXT    NOT NULL DEFAULT 'proposed'
                       CHECK (status IN ('proposed', 'scored', 'approved', 'rejected')),
    reject_reason      TEXT    CHECK (reject_reason IS NULL OR reject_reason IN ('duplicate', 'low_novelty', 'low_score', 'cod_reject', 'vv_fail')),
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_by         TEXT    NOT NULL DEFAULT 'system',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_run    ON foundry_concepts(run_id);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_status ON foundry_concepts(status);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_score  ON foundry_concepts(composite_score);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_tenant ON foundry_concepts(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_specs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    spec_md            TEXT    NOT NULL,
    canvas_contract    TEXT    DEFAULT '{}',
    task_count         INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_specs_concept ON foundry_specs(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_specs_tenant  ON foundry_specs(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_tasks_emitted (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    kanban_task_id     TEXT    NOT NULL,
    epic               TEXT,
    seq                INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_tasks_concept ON foundry_tasks_emitted(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_tasks_tenant  ON foundry_tasks_emitted(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_outcomes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    outcome            TEXT    NOT NULL
                       CHECK (outcome IN ('shipped', 'vv_pass', 'vv_fail', 'abandoned')),
    metric             REAL,
    detail             TEXT    DEFAULT '{}',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_outcomes_concept ON foundry_outcomes(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_outcomes_tenant  ON foundry_outcomes(tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS foundry_runs (
    id                 BIGSERIAL PRIMARY KEY,
    cycle_at           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    harvested          INTEGER NOT NULL DEFAULT 0,
    concepts_proposed  INTEGER NOT NULL DEFAULT 0,
    concepts_approved  INTEGER NOT NULL DEFAULT 0,
    tasks_emitted      INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'completed', 'failed', 'deferred', 'rate_limited', 'circuit_open')),
    detail             TEXT    DEFAULT '{}',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_runs_tenant ON foundry_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_foundry_runs_status ON foundry_runs(status);

CREATE TABLE IF NOT EXISTS foundry_signals (
    id                 BIGSERIAL PRIMARY KEY,
    run_id             TEXT    NOT NULL,
    source_engine      TEXT    NOT NULL
                       CHECK (source_engine IN ('innovation', 'creative', 'research', 'genesis', 'telemetry')),
    source_ref         TEXT    NOT NULL,
    theme              TEXT,
    raw_score          REAL    DEFAULT 0.0,
    keywords           TEXT    DEFAULT '[]',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_run    ON foundry_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_engine ON foundry_signals(source_engine);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_score  ON foundry_signals(raw_score);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_tenant ON foundry_signals(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_concepts (
    id                 BIGSERIAL PRIMARY KEY,
    run_id             TEXT    NOT NULL,
    name               TEXT    NOT NULL,
    slug               TEXT    NOT NULL UNIQUE,
    problem_statement  TEXT,
    proposed_capability TEXT,
    target_users       TEXT,
    cluster_signal_ids TEXT    DEFAULT '[]',
    novelty_score      REAL    DEFAULT 0.0,
    market_score       REAL    DEFAULT 0.0,
    fit_score          REAL    DEFAULT 0.0,
    effort_estimate    REAL    DEFAULT 0.0,
    compliance_risk    REAL    DEFAULT 0.0,
    composite_score    REAL    DEFAULT 0.0,
    status             TEXT    NOT NULL DEFAULT 'proposed'
                       CHECK (status IN ('proposed', 'scored', 'approved', 'rejected')),
    reject_reason      TEXT    CHECK (reject_reason IS NULL OR reject_reason IN ('duplicate', 'low_novelty', 'low_score', 'cod_reject', 'vv_fail')),
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_by         TEXT    NOT NULL DEFAULT 'system',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_run    ON foundry_concepts(run_id);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_status ON foundry_concepts(status);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_score  ON foundry_concepts(composite_score);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_tenant ON foundry_concepts(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_specs (
    id                 BIGSERIAL PRIMARY KEY,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    spec_md            TEXT    NOT NULL,
    canvas_contract    TEXT    DEFAULT '{}',
    task_count         INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_specs_concept ON foundry_specs(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_specs_tenant  ON foundry_specs(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_tasks_emitted (
    id                 BIGSERIAL PRIMARY KEY,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    kanban_task_id     TEXT    NOT NULL,
    epic               TEXT,
    seq                INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_tasks_concept ON foundry_tasks_emitted(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_tasks_tenant  ON foundry_tasks_emitted(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_outcomes (
    id                 BIGSERIAL PRIMARY KEY,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    outcome            TEXT    NOT NULL
                       CHECK (outcome IN ('shipped', 'vv_pass', 'vv_fail', 'abandoned')),
    metric             REAL,
    detail             TEXT    DEFAULT '{}',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_outcomes_concept ON foundry_outcomes(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_outcomes_tenant  ON foundry_outcomes(tenant_id);

-- @all