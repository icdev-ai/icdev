-- CUI // SP-CTI
-- AI-ify canvas — schema baseline (penta-aiify-06), migration 0001.
--
-- This is the registry-declared migration baseline
-- (component_registry: aiify.db_migration = tools/aiify/db/migrations).
--
-- The AUTHORITATIVE schema is authored in tools/aiify/db/init_db.py, where the
-- CHECK constraints on language / pattern_type / ai_paradigm / ai_readiness /
-- category are derived from the Python constants in tools/aiify/constants.py
-- (per the repo rule: never hardcode CHECK enums in SQL). init_db() applies that
-- PG-authored schema BEFORE the migration runner, so the CREATE TABLE IF NOT
-- EXISTS statements below are idempotent no-ops on a normally-initialized DB and
-- serve as the recorded 0001 baseline (and a safety net if the Python DDL is
-- ever skipped). All statements are PG-authored and translated for SQLite by the
-- canvas StorageConnection.

CREATE TABLE IF NOT EXISTS aiify_scans (
    scan_id          SERIAL PRIMARY KEY,
    input_type       TEXT NOT NULL,
    input_ref        TEXT NOT NULL,
    language_profile JSONB,
    total_files      INTEGER DEFAULT 0,
    total_loc        INTEGER DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',
    project_summary  TEXT,
    overall_verdict      TEXT,
    overall_ai_readiness TEXT,
    overall_rationale    TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_opportunities (
    opportunity_id       SERIAL PRIMARY KEY,
    scan_id              INTEGER NOT NULL REFERENCES aiify_scans(scan_id) ON DELETE CASCADE,
    module_path          TEXT NOT NULL,
    function_name        TEXT NOT NULL,
    line_start           INTEGER,
    line_end             INTEGER,
    language             TEXT NOT NULL,
    pattern_type         TEXT NOT NULL,
    pattern_detail       JSONB,
    ai_paradigm          TEXT NOT NULL,
    il_recommended_model TEXT,
    data_requirements    JSONB,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_scores (
    score_id          SERIAL PRIMARY KEY,
    opportunity_id    INTEGER NOT NULL REFERENCES aiify_opportunities(opportunity_id) ON DELETE CASCADE,
    value_score       REAL,
    feasibility_score REAL,
    risk_score        REAL,
    composite_score   REAL,
    score_detail      JSONB,
    verdict           TEXT,
    ai_readiness      TEXT,
    rationale         TEXT,
    pros              JSONB,
    cons              JSONB,
    category          TEXT,
    scored_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_roadmaps (
    id                SERIAL PRIMARY KEY,
    scan_id           INTEGER NOT NULL REFERENCES aiify_scans(scan_id) ON DELETE CASCADE,
    roadmap_id        TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    phases            JSONB,
    total_effort_days INTEGER DEFAULT 0,
    aimc_links        JSONB,
    aadc_links        JSONB,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_audit_log (
    id         SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    scan_id    INTEGER REFERENCES aiify_scans(scan_id) ON DELETE SET NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    detail     JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_hitl_decisions (
    id          SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    phase_id    TEXT,
    decision    TEXT NOT NULL,
    reason      TEXT,
    actor       TEXT NOT NULL DEFAULT 'user',
    decided_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_posture_snapshots (
    id                SERIAL PRIMARY KEY,
    overall_score     REAL DEFAULT 0,
    grade             TEXT DEFAULT 'F',
    posture           TEXT DEFAULT 'critical',
    scan_count        INTEGER DEFAULT 0,
    opportunity_count INTEGER DEFAULT 0,
    dimensions_json   JSONB,
    snapshot_json     JSONB,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_prd_provenance (
    id                SERIAL PRIMARY KEY,
    roadmap_id        TEXT NOT NULL,
    phase_id          TEXT NOT NULL,
    ai_boosted        INTEGER NOT NULL DEFAULT 0,
    generation_model  TEXT,
    citation_valid    INTEGER NOT NULL DEFAULT 1,
    citation_report   JSONB,
    evidence_sources  JSONB,
    provenance        JSONB,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
