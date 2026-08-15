#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 022 — sg_eo_signals table for Strategos EO/IR scene ingestion.

Stores electro-optical satellite scene metadata emitted by the GEOINT pipeline.
Rows are append-only (NIST AU); never UPDATE or DELETE.

Safe to re-run: uses CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "022"
MIGRATION_NAME = "sg_eo_signals"
DESCRIPTION = "Create sg_eo_signals table for Strategos EO/IR satellite scene records"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_eo_signals (
    id                TEXT PRIMARY KEY,
    scene_id          TEXT,
    satellite         TEXT,
    bbox_wkt          TEXT,
    cloud_pct         REAL,
    sensing_date      TEXT,
    thumbnail_url     TEXT,
    relevance_score   REAL,
    aoi_tag           TEXT,
    status            TEXT DEFAULT 'new',
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sg_eo_signals_sensing_date
    ON sg_eo_signals(sensing_date DESC);

CREATE INDEX IF NOT EXISTS idx_sg_eo_signals_scene_id
    ON sg_eo_signals(scene_id);

CREATE INDEX IF NOT EXISTS idx_sg_eo_signals_aoi_tag
    ON sg_eo_signals(aoi_tag);

CREATE INDEX IF NOT EXISTS idx_sg_eo_signals_status
    ON sg_eo_signals(status);
"""


def up(conn=None) -> None:
    """Apply migration: create sg_eo_signals table in icdev.db."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
        _close = True
    else:
        _close = False

    conn.executescript(_SCHEMA)
    conn.commit()

    if _close:
        conn.close()

    print(f"[migration {MIGRATION_ID}] {DESCRIPTION} — applied")


if __name__ == "__main__":
    up()
