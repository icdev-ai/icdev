-- CUI // SP-CTI
-- Migration 325: GameDay pre-session registration + snake-draft formation (gdx-reg-01).
--
-- ttx_registrations and ttx_formation_plan existed ONLY in
-- tools/db/schema/pg_consolidated.sql, never in a migration. The schema comment on
-- both said so explicitly: "no migration creates it -- it exists only in this
-- consolidated snapshot", and "do not extend this table without also landing a
-- migration -- fresh databases do not have it".
--
-- So every database built by running migrations rather than restoring the snapshot
-- lacked both tables, and now that gdx-reg-01 wires routes against them, that gap
-- becomes a runtime error instead of a documented curiosity. This migration closes
-- it. On a database restored from the snapshot the tables already exist and
-- IF NOT EXISTS makes this a no-op.
--
-- Column types match the snapshot so the two paths converge rather than drift.
--
-- Portable across PostgreSQL and the SQLite test/fallback backend:
--   * CREATE TABLE IF NOT EXISTS only -- no ALTER TABLE, which would raise on a
--     second run against SQLite.
--   * No semicolon inside any string literal, so naive statement splitters do not
--     cut a statement in half.
--   * Autoincrementing keys are declared with the portable INTEGER PRIMARY KEY
--     form; the PostgreSQL snapshot already owns its own sequences for these
--     tables, and IF NOT EXISTS means this DDL never runs there.

CREATE TABLE IF NOT EXISTS ttx_registrations (
    registration_id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    email TEXT,
    stated_skill TEXT NOT NULL,
    matched_role_id TEXT NOT NULL,
    matched_role_label TEXT NOT NULL,
    match_confidence REAL NOT NULL DEFAULT 1.0,
    match_method TEXT NOT NULL DEFAULT 'selected',
    match_reasoning TEXT,
    academy_username TEXT,
    registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    classification VARCHAR(50) DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_ttx_registrations_session
    ON ttx_registrations (session_id);

CREATE TABLE IF NOT EXISTS ttx_formation_plan (
    plan_id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    registration_id INTEGER NOT NULL,
    team_slot INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    classification VARCHAR(50) DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_ttx_formation_plan_session
    ON ttx_formation_plan (session_id);

CREATE INDEX IF NOT EXISTS idx_ttx_formation_plan_registration
    ON ttx_formation_plan (registration_id);
