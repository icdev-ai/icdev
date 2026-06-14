-- Migration 162: CMMC Systems registry and practice gap tracking tables
-- Resolves orphan_db_table gap: cmmc_systems (referenced in
-- tools/notification_service/handler_service.py but never defined).
-- Also adds cmmc_practice_gaps which is co-referenced in the same handler.

CREATE TABLE IF NOT EXISTS cmmc_systems (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI'
        CHECK(classification IN ('CUI', 'SECRET', 'TOP SECRET', 'UNCLASSIFIED')),
    boundary TEXT,
    level INTEGER CHECK(level IN (1, 2, 3)),
    system_owner TEXT,
    authorizing_official TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cmmc_systems_classification
    ON cmmc_systems(classification);

-- Per-assessment practice gap findings linked to a cmmc_assessments row.
-- assessment_id references cmmc_assessments.id (INTEGER, defined in init_icdev_db.py).
CREATE TABLE IF NOT EXISTS cmmc_practice_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    practice_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'gap'
        CHECK(status IN ('gap', 'partial', 'met', 'not_applicable')),
    gap_description TEXT,
    remediation_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(assessment_id, practice_id)
);

CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_assessment
    ON cmmc_practice_gaps(assessment_id);
CREATE INDEX IF NOT EXISTS idx_cmmc_gaps_domain
    ON cmmc_practice_gaps(domain);
