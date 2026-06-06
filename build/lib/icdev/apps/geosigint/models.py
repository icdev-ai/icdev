"""GeoSIGINT — Database models and initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "geosigint.db"

SCHEMA = """
-- Frequency bands (ITU/FCC public allocations)
CREATE TABLE IF NOT EXISTS frequency_bands (
    band_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    min_freq_hz REAL NOT NULL,
    max_freq_hz REAL NOT NULL,
    allocation  TEXT,            -- e.g. 'Amateur', 'Maritime', 'Aeronautical'
    region      TEXT DEFAULT 'ITU-1'
);

-- Stations (receivers / known transmitters)
CREATE TABLE IF NOT EXISTS stations (
    station_id  TEXT PRIMARY KEY,
    callsign    TEXT,
    name        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    station_type TEXT DEFAULT 'receiver',  -- receiver, transmitter, transceiver
    status      TEXT DEFAULT 'active',
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Signal detections
CREATE TABLE IF NOT EXISTS signals (
    signal_id       TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    freq_hz         REAL NOT NULL,
    power_dbm       REAL,
    snr_db          REAL,
    bandwidth_hz    REAL,
    modulation      TEXT,           -- AM, FM, CW, PSK, FSK, OFDM, unknown
    bearing_deg     REAL,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    station_id      TEXT,
    band_id         TEXT,
    classification  TEXT DEFAULT 'unclassified',  -- unclassified, of_interest, anomalous
    metadata        TEXT,           -- JSON blob
    FOREIGN KEY (station_id) REFERENCES stations(station_id),
    FOREIGN KEY (band_id)    REFERENCES frequency_bands(band_id)
);

-- Emitters (deduced transmitter entities from signal clustering)
CREATE TABLE IF NOT EXISTS emitters (
    emitter_id  TEXT PRIMARY KEY,
    name        TEXT,
    lat         REAL,
    lon         REAL,
    freq_hz     REAL,
    modulation  TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    signal_count INTEGER DEFAULT 0,
    confidence  REAL DEFAULT 0.0,
    emitter_type TEXT DEFAULT 'unknown'  -- comms, radar, beacon, jammer, unknown
);

-- Detected patterns
CREATE TABLE IF NOT EXISTS patterns (
    pattern_id   TEXT PRIMARY KEY,
    pattern_type TEXT NOT NULL,     -- frequency_hopping, burst, periodic, spatial_cluster, co_occurrence
    description  TEXT,
    confidence   REAL DEFAULT 0.0,
    emitter_ids  TEXT,             -- JSON array of emitter_id
    signal_ids   TEXT,             -- JSON array of signal_id
    params       TEXT,             -- JSON blob with pattern-specific params
    detected_at  TEXT DEFAULT (datetime('now'))
);

-- Anomalies
CREATE TABLE IF NOT EXISTS anomalies (
    anomaly_id   TEXT PRIMARY KEY,
    anomaly_type TEXT NOT NULL,     -- unexpected_freq, power_spike, new_emitter, schedule_deviation
    severity     TEXT DEFAULT 'low', -- low, medium, high, critical
    description  TEXT,
    signal_id    TEXT,
    emitter_id   TEXT,
    score        REAL DEFAULT 0.0,
    resolved     INTEGER DEFAULT 0,
    detected_at  TEXT DEFAULT (datetime('now'))
);

-- Knowledge graph edges
CREATE TABLE IF NOT EXISTS kg_edges (
    edge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,      -- signal, station, emitter, band, pattern, anomaly
    source_id   TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    relation    TEXT NOT NULL,      -- detected_by, transmits_on, correlated_with, co_occurs, located_near, caused_by
    weight      REAL DEFAULT 1.0,
    metadata    TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_freq ON signals(freq_hz);
CREATE INDEX IF NOT EXISTS idx_signals_location ON signals(lat, lon);
CREATE INDEX IF NOT EXISTS idx_kg_source ON kg_edges(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_kg_target ON kg_edges(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_kg_relation ON kg_edges(relation);

-- Space weather observations (affects propagation)
CREATE TABLE IF NOT EXISTS space_weather (
    obs_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    k_index     INTEGER,
    solar_flux  REAL,
    sunspot_num INTEGER,
    conditions  TEXT       -- quiet, unsettled, active, storm
);

-- AIS vessel tracks (imported from NMEA AIS files via ais_importer.py)
CREATE TABLE IF NOT EXISTS sg_tracks (
    track_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    speed       REAL,           -- speed over ground in knots
    heading     REAL,           -- true heading in degrees (0-359)
    timestamp   TEXT NOT NULL,
    vessel_type TEXT,           -- e.g. "Class A", "Class B", "AtoN", or ITU type name
    source_file TEXT,           -- originating NMEA file path
    msg_type    INTEGER,        -- raw AIS message type (1-27)
    imported_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sg_tracks_mmsi ON sg_tracks(mmsi);
CREATE INDEX IF NOT EXISTS idx_sg_tracks_timestamp ON sg_tracks(timestamp);
CREATE INDEX IF NOT EXISTS idx_sg_tracks_location ON sg_tracks(lat, lon);

-- Naval ORBAT — vessels of interest with capability flags
CREATE TABLE IF NOT EXISTS vessel_orbat (
    orbat_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi           TEXT NOT NULL UNIQUE,
    vessel_name    TEXT NOT NULL,
    vessel_class   TEXT,
    nation         TEXT,
    hull_number    TEXT,
    displacement_t INTEGER,
    kalibr_capable INTEGER NOT NULL DEFAULT 0,  -- 1 = carries Kalibr-NK/Kalibr-NKE
    kalibr_range_km INTEGER DEFAULT 1500,        -- max range for threat ring
    notes          TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_orbat_mmsi ON vessel_orbat(mmsi);
CREATE INDEX IF NOT EXISTS idx_orbat_kalibr ON vessel_orbat(kalibr_capable);

-- KG nodes for ORBAT vessels (used by /graph endpoint enrichment)
CREATE TABLE IF NOT EXISTS vessel_kg_nodes (
    node_id        TEXT PRIMARY KEY,   -- "vessel:<mmsi>"
    mmsi           TEXT NOT NULL UNIQUE,
    vessel_name    TEXT NOT NULL,
    nation         TEXT,
    entity_type    TEXT DEFAULT 'vessel',
    kalibr_capable INTEGER DEFAULT 0,
    properties_json TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

-- Theater supply chain entities (logistics nodes, depots, corridors, ports)
CREATE TABLE IF NOT EXISTS sg_entities (
    entity_id      TEXT PRIMARY KEY,
    entity_name    TEXT NOT NULL,
    entity_type    TEXT NOT NULL,   -- logistics_node, port, rail_hub, depot, border_crossing, airbase
    theater        TEXT NOT NULL,   -- ukraine, pacific, europe, ...
    country        TEXT NOT NULL,
    lat            REAL,
    lon            REAL,
    domain_profile TEXT DEFAULT 'war_economy',
    capacity_json  TEXT,            -- JSON: {throughput_tpd, modes, gauge, ...}
    criticality    TEXT DEFAULT 'high',
    status         TEXT DEFAULT 'active',   -- active, degraded, contested, destroyed
    notes          TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sg_ent_theater ON sg_entities(theater);
CREATE INDEX IF NOT EXISTS idx_sg_ent_type    ON sg_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_sg_ent_country ON sg_entities(country);

-- Theater KG nodes — multi-entity-type knowledge graph (extends vessel_kg_nodes)
--   Supported entity_type values:
--     DefenseIndustrialNode, SupplyRoute, CriticalMaterial,
--     SanctionsEvader, HumanitarianCorridor, logistics_node
CREATE TABLE IF NOT EXISTS sg_kg_nodes (
    node_id         TEXT PRIMARY KEY,   -- "<entity_type>:<entity_id>"
    entity_id       TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    label           TEXT NOT NULL,
    theater         TEXT,
    country         TEXT,
    lat             REAL,
    lon             REAL,
    properties_json TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sg_kg_theater ON sg_kg_nodes(theater);
CREATE INDEX IF NOT EXISTS idx_sg_kg_type    ON sg_kg_nodes(entity_type);
"""


def get_connection() -> sqlite3.Connection:
    """Get a database connection, creating the DB dir if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> dict:
    """Initialize the database schema."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        return {"status": "ok", "tables": tables, "count": len(tables)}
    finally:
        conn.close()
