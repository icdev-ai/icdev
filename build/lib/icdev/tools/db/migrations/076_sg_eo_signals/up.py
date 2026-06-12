#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 076 — sg_eo_signals (electro-optical satellite imagery signals for GeoSIGINT)."""

from tools.db.storage import get_connection

MIGRATION_ID = "076"
MIGRATION_NAME = "sg_eo_signals"
DESCRIPTION = "Electro-optical satellite scene signals for GeoSIGINT area-of-interest monitoring"

_DDL = [
    """CREATE TABLE IF NOT EXISTS sg_eo_signals (
        id              TEXT PRIMARY KEY,
        scene_id        TEXT,
        satellite       TEXT,
        bbox_wkt        TEXT,
        cloud_pct       REAL,
        sensing_date    DATE,
        thumbnail_url   TEXT,
        relevance_score REAL,
        aoi_tag         TEXT,
        status          TEXT NOT NULL DEFAULT 'new',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sg_eo_status   ON sg_eo_signals(status)",
    "CREATE INDEX IF NOT EXISTS idx_sg_eo_aoi      ON sg_eo_signals(aoi_tag)",
    "CREATE INDEX IF NOT EXISTS idx_sg_eo_date     ON sg_eo_signals(sensing_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sg_eo_score    ON sg_eo_signals(relevance_score DESC)",
]


def up(conn=None):
    _conn = conn or get_connection()
    try:
        for stmt in _DDL:
            _conn.execute(stmt)
        _conn.commit()
        print("[076_sg_eo_signals] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()
