#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 118 — STRATEGOS core tables.

Creates:
  sg_theaters           — theater registry (pacific, ukraine, ...)
  sg_entities           — ORBAT units, vessels, aircraft, installations
  sg_conflict_events    — ACLED + Oryx + OSINT conflict events
  sg_tracks             — live AIS/ADS-B/TLE position tracks
  sg_economic_signals   — economic indicator time-series
  sg_war_readiness_events — append-only I&W event log (NIST AU)
  sg_ghost_tracks       — behavioral anomaly / ghost track registry
  sg_coa_decisions      — COA decision capture from HITL agent

All lat/lon geometry stored as REAL columns (SpatiaLite-compatible).
Idempotent: CREATE TABLE IF NOT EXISTS.
"""

from __future__ import annotations

MIGRATION_ID = "118"
MIGRATION_NAME = "strategos_core_tables"
DESCRIPTION = "STRATEGOS core operational tables for multi-domain conflict analysis"

_DDL = """
-- ── Theater registry ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sg_theaters (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    config_file     TEXT DEFAULT '',
    bounds_json     TEXT DEFAULT '{}',
    active_domains  TEXT DEFAULT '[]',
    actors_json     TEXT DEFAULT '[]',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── ORBAT entities ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sg_entities (
    id              TEXT PRIMARY KEY,
    theater_id      TEXT NOT NULL REFERENCES sg_theaters(id) ON DELETE CASCADE,
    entity_type     TEXT NOT NULL,
    label           TEXT NOT NULL,
    actor           TEXT DEFAULT '',
    domain          TEXT DEFAULT 'land',
    lat             REAL,
    lon             REAL,
    status          TEXT DEFAULT 'active',
    properties_json TEXT DEFAULT '{}',
    source          TEXT DEFAULT 'manual',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK (domain IN ('land','sea','air','cyber','space','ew','info','supply'))
);

CREATE INDEX IF NOT EXISTS idx_sg_entities_theater ON sg_entities(theater_id);
CREATE INDEX IF NOT EXISTS idx_sg_entities_type    ON sg_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_sg_entities_actor   ON sg_entities(actor);
CREATE INDEX IF NOT EXISTS idx_sg_entities_domain  ON sg_entities(domain);

-- ── Conflict events ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sg_conflict_events (
    id              TEXT PRIMARY KEY,
    theater_id      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    sub_type        TEXT DEFAULT '',
    actor           TEXT DEFAULT '',
    date            TEXT NOT NULL,
    lat             REAL,
    lon             REAL,
    location        TEXT DEFAULT '',
    admin1          TEXT DEFAULT '',
    fatalities      INTEGER DEFAULT 0,
    source          TEXT DEFAULT '',
    source_url      TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    properties_json TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sg_events_theater ON sg_conflict_events(theater_id);
CREATE INDEX IF NOT EXISTS idx_sg_events_date    ON sg_conflict_events(date);
CREATE INDEX IF NOT EXISTS idx_sg_events_actor   ON sg_conflict_events(actor);
CREATE INDEX IF NOT EXISTS idx_sg_events_type    ON sg_conflict_events(event_type);

-- ── Live position tracks ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sg_tracks (
    id              TEXT PRIMARY KEY,
    theater_id      TEXT NOT NULL,
    entity_id       TEXT REFERENCES sg_entities(id) ON DELETE SET NULL,
    track_source    TEXT NOT NULL,
    mmsi            TEXT DEFAULT '',
    icao24          TEXT DEFAULT '',
    tle_norad       TEXT DEFAULT '',
    callsign        TEXT DEFAULT '',
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    altitude_m      REAL DEFAULT 0,
    speed_kts       REAL DEFAULT 0,
    heading_deg     REAL DEFAULT 0,
    timestamp       TEXT NOT NULL,
    domain          TEXT DEFAULT 'sea',
    properties_json TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK (domain IN ('land','sea','air','cyber','space','ew','info','supply'))
);

CREATE INDEX IF NOT EXISTS idx_sg_tracks_theater   ON sg_tracks(theater_id);
CREATE INDEX IF NOT EXISTS idx_sg_tracks_entity    ON sg_tracks(entity_id);
CREATE INDEX IF NOT EXISTS idx_sg_tracks_ts        ON sg_tracks(timestamp);
CREATE INDEX IF NOT EXISTS idx_sg_tracks_mmsi      ON sg_tracks(mmsi);
CREATE INDEX IF NOT EXISTS idx_sg_tracks_icao      ON sg_tracks(icao24);

-- ── Economic signals ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sg_economic_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    theater_id      TEXT NOT NULL,
    indicator       TEXT NOT NULL,
    country         TEXT DEFAULT '',
    date            TEXT NOT NULL,
    value           REAL,
    unit            TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    cusum_alert     INTEGER DEFAULT 0,
    properties_json TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sg_econ_theater    ON sg_economic_signals(theater_id);
CREATE INDEX IF NOT EXISTS idx_sg_econ_indicator  ON sg_economic_signals(indicator);
CREATE INDEX IF NOT EXISTS idx_sg_econ_country    ON sg_economic_signals(country);
CREATE INDEX IF NOT EXISTS idx_sg_econ_date       ON sg_economic_signals(date);

-- ── War readiness events (APPEND-ONLY, NIST AU) ───────────────────────────────
CREATE TABLE IF NOT EXISTS sg_war_readiness_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    theater_id      TEXT NOT NULL,
    event_class     TEXT NOT NULL,
    domain          TEXT DEFAULT '',
    signal_score    REAL DEFAULT 0,
    composite_score REAL DEFAULT 0,
    escalation_lvl  INTEGER DEFAULT 0,
    summary         TEXT DEFAULT '',
    detail_json     TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sg_wr_theater ON sg_war_readiness_events(theater_id);
CREATE INDEX IF NOT EXISTS idx_sg_wr_class   ON sg_war_readiness_events(event_class);
CREATE INDEX IF NOT EXISTS idx_sg_wr_ts      ON sg_war_readiness_events(created_at);

-- Immutability triggers (SQLite)
CREATE TRIGGER IF NOT EXISTS sg_wr_no_update
    BEFORE UPDATE ON sg_war_readiness_events
    BEGIN SELECT RAISE(ABORT, 'sg_war_readiness_events is append-only — NIST AU-6'); END;

CREATE TRIGGER IF NOT EXISTS sg_wr_no_delete
    BEFORE DELETE ON sg_war_readiness_events
    BEGIN SELECT RAISE(ABORT, 'sg_war_readiness_events cannot be deleted'); END;

-- ── Ghost tracks (behavioral anomaly) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sg_ghost_tracks (
    id              TEXT PRIMARY KEY,
    theater_id      TEXT NOT NULL,
    entity_id       TEXT REFERENCES sg_entities(id) ON DELETE SET NULL,
    mmsi            TEXT DEFAULT '',
    icao24          TEXT DEFAULT '',
    anomaly_type    TEXT DEFAULT 'ais_gap',
    confidence      REAL DEFAULT 0.0,
    predicted_lat   REAL,
    predicted_lon   REAL,
    uncertainty_km  REAL DEFAULT 0,
    last_known_lat  REAL,
    last_known_lon  REAL,
    gap_minutes     INTEGER DEFAULT 0,
    kalman_state    TEXT DEFAULT '{}',
    hitl_status     TEXT DEFAULT 'pending',
    resolved_at     TEXT,
    properties_json TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK (hitl_status IN ('pending','confirmed','false_positive','dismissed'))
);

CREATE INDEX IF NOT EXISTS idx_sg_ghost_theater ON sg_ghost_tracks(theater_id);
CREATE INDEX IF NOT EXISTS idx_sg_ghost_mmsi    ON sg_ghost_tracks(mmsi);
CREATE INDEX IF NOT EXISTS idx_sg_ghost_hitl    ON sg_ghost_tracks(hitl_status);

-- ── COA decisions (HITL capture) ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sg_coa_decisions (
    id              TEXT PRIMARY KEY,
    theater_id      TEXT NOT NULL,
    brief_id        TEXT DEFAULT '',
    operator        TEXT DEFAULT '',
    coa_selected    TEXT NOT NULL,
    coa_json        TEXT DEFAULT '{}',
    rationale       TEXT DEFAULT '',
    escalation_lvl  INTEGER DEFAULT 0,
    execution_status TEXT DEFAULT 'pending',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    executed_at     TEXT,
    CHECK (execution_status IN ('pending','approved','rejected','executed','superseded'))
);

CREATE INDEX IF NOT EXISTS idx_sg_coa_theater ON sg_coa_decisions(theater_id);
CREATE INDEX IF NOT EXISTS idx_sg_coa_status  ON sg_coa_decisions(execution_status);
"""

_INDEXES = []  # All indexes embedded in _DDL above


def run(conn) -> None:
    """Execute migration 118 against a live DB connection."""
    conn.executescript(_DDL)
    conn.commit()
    print(f"[migration-{MIGRATION_ID}] {MIGRATION_NAME} applied OK")


if __name__ == "__main__":
    import sqlite3
    from pathlib import Path

    db_path = Path(__file__).resolve().parents[4] / "data" / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    run(conn)
    conn.close()
