-- CUI // SP-CTI
-- Migration 184: Create creative_gap and innovation_signal tables
-- Addresses acf-ada-04-d1: normalized ACF consumption tables for creative engine
-- inputs and innovation engine outputs.

-- @sqlite-only
CREATE TABLE IF NOT EXISTS creative_gap (
    id TEXT PRIMARY KEY,
    concept_id TEXT,
    source_ref TEXT,
    gap_type TEXT DEFAULT 'feature_gap'
        CHECK(gap_type IN ('feature_gap', 'ux_gap', 'integration_gap', 'performance_gap',
                           'security_gap', 'compliance_gap', 'documentation_gap', 'other')),
    score REAL DEFAULT 0.0,
    rank INTEGER,
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_creative_gap_concept ON creative_gap(concept_id);
CREATE INDEX IF NOT EXISTS idx_creative_gap_score ON creative_gap(score);
CREATE INDEX IF NOT EXISTS idx_creative_gap_hash ON creative_gap(content_hash);
CREATE INDEX IF NOT EXISTS idx_creative_gap_created ON creative_gap(created_at);

CREATE TABLE IF NOT EXISTS innovation_signal (
    id TEXT PRIMARY KEY,
    concept_id TEXT,
    signal_type TEXT DEFAULT 'opportunity'
        CHECK(signal_type IN ('opportunity', 'trend', 'threat', 'technology', 'regulatory', 'other')),
    source_ref TEXT,
    score REAL DEFAULT 0.0,
    rank INTEGER,
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_concept ON innovation_signal(concept_id);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_score ON innovation_signal(score);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_hash ON innovation_signal(content_hash);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_created ON innovation_signal(created_at);

-- @pg-only
CREATE TABLE IF NOT EXISTS creative_gap (
    id TEXT PRIMARY KEY,
    concept_id TEXT,
    source_ref TEXT,
    gap_type TEXT DEFAULT 'feature_gap'
        CHECK(gap_type IN ('feature_gap', 'ux_gap', 'integration_gap', 'performance_gap',
                           'security_gap', 'compliance_gap', 'documentation_gap', 'other')),
    score REAL DEFAULT 0.0,
    rank INTEGER,
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_creative_gap_concept ON creative_gap(concept_id);
CREATE INDEX IF NOT EXISTS idx_creative_gap_score ON creative_gap(score);
CREATE INDEX IF NOT EXISTS idx_creative_gap_hash ON creative_gap(content_hash);
CREATE INDEX IF NOT EXISTS idx_creative_gap_created ON creative_gap(created_at);

CREATE TABLE IF NOT EXISTS innovation_signal (
    id TEXT PRIMARY KEY,
    concept_id TEXT,
    signal_type TEXT DEFAULT 'opportunity'
        CHECK(signal_type IN ('opportunity', 'trend', 'threat', 'technology', 'regulatory', 'other')),
    source_ref TEXT,
    score REAL DEFAULT 0.0,
    rank INTEGER,
    content_hash TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_concept ON innovation_signal(concept_id);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_score ON innovation_signal(score);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_hash ON innovation_signal(content_hash);
CREATE INDEX IF NOT EXISTS idx_innovation_signal_created ON innovation_signal(created_at);
