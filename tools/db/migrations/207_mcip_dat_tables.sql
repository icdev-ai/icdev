-- CUI // SP-CTI
-- Migration 207: Create mcip_dat_events and mcip_dti_scores tables.
--
-- These tables back the MCIP (Mission-Critical Intelligence Platform) DAT
-- canvas.  They previously existed only as dynamic CREATE TABLE calls inside
-- tools/mcip/db/init_db.py, which the Internal Awareness Engine gap detector
-- flagged as orphan_db_table findings (referenced via INSERT/SELECT in
-- tools/mcip/ but absent from any numbered migration).
--
-- mcip_dat_events  — raw ingestion store for cable/UNSC/backchannel events.
--   classification carries the RLS predicate via get_connection().
-- mcip_dti_scores  — append-only diplomatic tension index scores (NIST AU-9).
--   Registered in APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.
--   Never UPDATE or DELETE rows.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS mcip_dat_events (
    id              TEXT    PRIMARY KEY,
    source_type     TEXT    NOT NULL,
    content_hash    TEXT    NOT NULL,
    sender          TEXT    NOT NULL DEFAULT 'unknown',
    recipient       TEXT    NOT NULL DEFAULT 'unknown',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    tension_signal  REAL    NOT NULL DEFAULT 0.0,
    ingested_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_source ON mcip_dat_events(source_type);
CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_at     ON mcip_dat_events(ingested_at);

CREATE TABLE IF NOT EXISTS mcip_dti_scores (
    id              TEXT    PRIMARY KEY,
    score           REAL    NOT NULL,
    cable_sub       REAL    NOT NULL DEFAULT 0.0,
    unsc_sub        REAL    NOT NULL DEFAULT 0.0,
    backchannel_sub REAL    NOT NULL DEFAULT 0.0,
    event_count     INTEGER NOT NULL DEFAULT 0,
    computed_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcip_dti_scores_at ON mcip_dti_scores(computed_at);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS mcip_dat_events (
    id              TEXT    PRIMARY KEY,
    source_type     TEXT    NOT NULL,
    content_hash    TEXT    NOT NULL,
    sender          TEXT    NOT NULL DEFAULT 'unknown',
    recipient       TEXT    NOT NULL DEFAULT 'unknown',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    tension_signal  REAL    NOT NULL DEFAULT 0.0,
    ingested_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_source ON mcip_dat_events(source_type);
CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_at     ON mcip_dat_events(ingested_at);

CREATE TABLE IF NOT EXISTS mcip_dti_scores (
    id              TEXT    PRIMARY KEY,
    score           REAL    NOT NULL,
    cable_sub       REAL    NOT NULL DEFAULT 0.0,
    unsc_sub        REAL    NOT NULL DEFAULT 0.0,
    backchannel_sub REAL    NOT NULL DEFAULT 0.0,
    event_count     INTEGER NOT NULL DEFAULT 0,
    computed_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcip_dti_scores_at ON mcip_dti_scores(computed_at);

-- @all
