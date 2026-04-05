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
