-- CUI // SP-CTI
-- Migration 195: Create cmmc_systems and cmmc_practice_gaps tables.
--
-- cmmc_systems is a registry of information systems under CMMC assessment,
-- recording the system's name, classification level, authorization boundary,
-- CMMC level, system owner, and authorizing official.
--
-- cmmc_practice_gaps records per-practice gap findings from a CMMC assessment,
-- linked to a cmmc_assessments row via assessment_id.
--
-- Both tables are referenced by:
--   tools/notification_service/handler_service.py (handle_cmmc_assessment_handler)
-- Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
--
-- Carries tenant_id + classification so queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS cmmc_systems (
    id                   TEXT        PRIMARY KEY,
    name                 TEXT        NOT NULL,
    classification       TEXT        NOT NULL DEFAULT 'CUI'
                         CHECK (classification IN ('CUI', 'SECRET', 'TOP SECRET', 'UNCLASSIFIED')),
    boundary             TEXT,
    level                INTEGER     CHECK (level IN (1, 2, 3)),
    system_owner         TEXT,
    authorizing_official TEXT,
    tenant_id            TEXT        NOT NULL DEFAULT 'default',
    created_at           TEXT        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT        NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cmmc_systems_classification ON cmmc_systems (classification);
CREATE INDEX IF NOT EXISTS idx_cmmc_systems_tenant         ON cmmc_systems (tenant_id);

CREATE TABLE IF NOT EXISTS cmmc_practice_gaps (
    id                  INTEGER     PRIMARY KEY AUTOINCREMENT,
    assessment_id       INTEGER     NOT NULL,
    practice_id         TEXT        NOT NULL,
    domain              TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'gap'
                        CHECK (status IN ('gap', 'partial', 'met', 'not_applicable')),
    gap_description     TEXT,
    remediation_notes   TEXT,
    tenant_id           TEXT        NOT NULL DEFAULT 'default',
    created_at          TEXT        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (assessment_id, practice_id)
);
CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_assessment ON cmmc_practice_gaps (assessment_id);
CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_domain     ON cmmc_practice_gaps (domain);
CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_tenant     ON cmmc_practice_gaps (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS cmmc_systems (
    id                   TEXT        PRIMARY KEY,
    name                 TEXT        NOT NULL,
    classification       TEXT        NOT NULL DEFAULT 'CUI'
                         CHECK (classification IN ('CUI', 'SECRET', 'TOP SECRET', 'UNCLASSIFIED')),
    boundary             TEXT,
    level                INTEGER     CHECK (level IN (1, 2, 3)),
    system_owner         TEXT,
    authorizing_official TEXT,
    tenant_id            TEXT        NOT NULL DEFAULT 'default',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cmmc_systems_classification ON cmmc_systems (classification);
CREATE INDEX IF NOT EXISTS idx_cmmc_systems_tenant         ON cmmc_systems (tenant_id);

CREATE TABLE IF NOT EXISTS cmmc_practice_gaps (
    id                  SERIAL      PRIMARY KEY,
    assessment_id       INTEGER     NOT NULL,
    practice_id         TEXT        NOT NULL,
    domain              TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'gap'
                        CHECK (status IN ('gap', 'partial', 'met', 'not_applicable')),
    gap_description     TEXT,
    remediation_notes   TEXT,
    tenant_id           TEXT        NOT NULL DEFAULT 'default',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (assessment_id, practice_id)
);
CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_assessment ON cmmc_practice_gaps (assessment_id);
CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_domain     ON cmmc_practice_gaps (domain);
CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_tenant     ON cmmc_practice_gaps (tenant_id);

-- @all
