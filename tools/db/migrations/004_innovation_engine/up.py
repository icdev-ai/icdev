#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 004: Innovation Engine — autonomous self-improvement pipeline.

Targets data/icdev.db.
Adds: innovation_signals (D206), innovation_triage_log (D206),
      innovation_solutions, innovation_trends (D207),
      innovation_competitor_scans, innovation_standards_updates,
      innovation_feedback.
"""

import sqlite3


def _table_exists(conn, table):
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


INNOVATION_SCHEMA = """
-- ============================================================
-- INNOVATION SIGNALS — discovered opportunities (append-only, D206)
-- ============================================================
CREATE TABLE IF NOT EXISTS innovation_signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    url TEXT,
    metadata TEXT,
    community_score REAL DEFAULT 0.0,
    content_hash TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'scored', 'triaged', 'approved', 'suggested',
                         'blocked', 'logged', 'solution_generated', 'published')),
    category TEXT,
    innovation_score REAL,
    score_breakdown TEXT,
    triage_result TEXT
        CHECK(triage_result IS NULL OR triage_result IN ('approved', 'suggested', 'blocked', 'logged')),
    gotcha_layer TEXT
        CHECK(gotcha_layer IS NULL OR gotcha_layer IN ('goal', 'tool', 'arg', 'context', 'hardprompt')),
    boundary_tier TEXT
        CHECK(boundary_tier IS NULL OR boundary_tier IN ('GREEN', 'YELLOW', 'ORANGE', 'RED')),
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_innovation_signals_status ON innovation_signals(status);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_source ON innovation_signals(source);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_score ON innovation_signals(innovation_score);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_hash ON innovation_signals(content_hash);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_discovered ON innovation_signals(discovered_at);
CREATE INDEX IF NOT EXISTS idx_innovation_signals_category ON innovation_signals(category);

-- ============================================================
-- INNOVATION TRIAGE LOG — triage decisions per signal (append-only, D206)
-- ============================================================
CREATE TABLE IF NOT EXISTS innovation_triage_log (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES innovation_signals(id),
    stage INTEGER NOT NULL CHECK(stage BETWEEN 1 AND 5),
    stage_name TEXT NOT NULL
        CHECK(stage_name IN ('classify', 'gotcha_fit', 'boundary_impact',
                              'compliance_check', 'dedup_license')),
    result TEXT NOT NULL CHECK(result IN ('pass', 'block', 'warn')),
    details TEXT,
    triaged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_innovation_triage_signal ON innovation_triage_log(signal_id);
CREATE INDEX IF NOT EXISTS idx_innovation_triage_result ON innovation_triage_log(result);

-- ============================================================
-- INNOVATION SOLUTIONS — generated solution specs
-- ============================================================
CREATE TABLE IF NOT EXISTS innovation_solutions (
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL REFERENCES innovation_signals(id),
    spec_content TEXT NOT NULL,
    gotcha_layer TEXT NOT NULL
        CHECK(gotcha_layer IN ('goal', 'tool', 'arg', 'context', 'hardprompt')),
    asset_type TEXT NOT NULL
        CHECK(asset_type IN ('skill', 'goal', 'tool', 'context', 'hardprompt',
                              'arg', 'compliance_extension')),
    estimated_effort TEXT NOT NULL CHECK(estimated_effort IN ('S', 'M', 'L', 'XL')),
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK(status IN ('generated', 'building', 'built', 'published', 'failed', 'rejected')),
    spec_quality_score REAL,
    build_output TEXT,
    marketplace_asset_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_innovation_solutions_signal ON innovation_solutions(signal_id);
CREATE INDEX IF NOT EXISTS idx_innovation_solutions_status ON innovation_solutions(status);
CREATE INDEX IF NOT EXISTS idx_innovation_solutions_layer ON innovation_solutions(gotcha_layer);

-- ============================================================
-- INNOVATION TRENDS — detected cross-signal patterns (D207)
-- ============================================================
CREATE TABLE IF NOT EXISTS innovation_trends (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    signal_ids TEXT NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 0,
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    velocity REAL DEFAULT 0.0,
    acceleration REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'emerging'
        CHECK(status IN ('emerging', 'active', 'declining', 'stale')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_innovation_trends_status ON innovation_trends(status);
CREATE INDEX IF NOT EXISTS idx_innovation_trends_category ON innovation_trends(category);
CREATE INDEX IF NOT EXISTS idx_innovation_trends_velocity ON innovation_trends(velocity);

-- ============================================================
-- INNOVATION COMPETITOR SCANS — competitive intelligence results
-- ============================================================
CREATE TABLE IF NOT EXISTS innovation_competitor_scans (
    id TEXT PRIMARY KEY,
    competitor_name TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    releases_found INTEGER DEFAULT 0,
    features_found INTEGER DEFAULT 0,
    gaps_identified INTEGER DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_innovation_competitor_name ON innovation_competitor_scans(competitor_name);
CREATE INDEX IF NOT EXISTS idx_innovation_competitor_date ON innovation_competitor_scans(scan_date);

-- ============================================================
-- INNOVATION STANDARDS UPDATES — standards body change tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS innovation_standards_updates (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL
        CHECK(body IN ('nist', 'cisa', 'dod', 'fedramp', 'iso')),
    title TEXT NOT NULL,
    publication_type TEXT,
    url TEXT,
    abstract TEXT,
    published_date TEXT,
    impact_assessment TEXT,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new', 'assessed', 'applied', 'not_applicable')),
    content_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_innovation_standards_body ON innovation_standards_updates(body);
CREATE INDEX IF NOT EXISTS idx_innovation_standards_status ON innovation_standards_updates(status);
CREATE INDEX IF NOT EXISTS idx_innovation_standards_hash ON innovation_standards_updates(content_hash);

-- ============================================================
-- INNOVATION FEEDBACK — feedback loop metrics for calibration
-- ============================================================
CREATE TABLE IF NOT EXISTS innovation_feedback (
    id TEXT PRIMARY KEY,
    signal_id TEXT REFERENCES innovation_signals(id),
    solution_id TEXT REFERENCES innovation_solutions(id),
    feedback_type TEXT NOT NULL
        CHECK(feedback_type IN ('marketplace_install', 'marketplace_rating',
                                 'self_heal_hit', 'gate_failure_reduction',
                                 'feature_request_addressed', 'manual_review')),
    feedback_value REAL,
    feedback_details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_innovation_feedback_signal ON innovation_feedback(signal_id);
CREATE INDEX IF NOT EXISTS idx_innovation_feedback_type ON innovation_feedback(feedback_type);
"""


def up(conn):
    """Apply innovation engine tables to icdev.db."""
    tables = [
        "innovation_signals",
        "innovation_triage_log",
        "innovation_solutions",
        "innovation_trends",
        "innovation_competitor_scans",
        "innovation_standards_updates",
        "innovation_feedback",
    ]

    # Only create tables that don't exist yet (idempotent)
    missing = [t for t in tables if not _table_exists(conn, t)]
    if missing:
        conn.executescript(INNOVATION_SCHEMA)

    conn.commit()


def down(conn):
    """Rollback: drop innovation engine tables."""
    tables = [
        "innovation_feedback",
        "innovation_standards_updates",
        "innovation_competitor_scans",
        "innovation_trends",
        "innovation_solutions",
        "innovation_triage_log",
        "innovation_signals",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
