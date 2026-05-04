#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 072 — sg_vessel_tracks (maritime animated track data)."""

from tools.db.storage import get_connection

MIGRATION_ID = "072"
MIGRATION_NAME = "sg_vessel_tracks"
DESCRIPTION = "Time-series AIS/vessel position tracks for maritime animation"

_DDL = [
    """CREATE TABLE IF NOT EXISTS sg_vessel_tracks (
        id          TEXT PRIMARY KEY,
        mmsi        TEXT NOT NULL,
        vessel_name TEXT NOT NULL,
        vessel_type TEXT NOT NULL DEFAULT 'cargo',
        flag        TEXT,
        lat         REAL NOT NULL,
        lon         REAL NOT NULL,
        speed       REAL DEFAULT 0.0,
        heading     REAL DEFAULT 0.0,
        ts          TEXT NOT NULL,
        status      TEXT DEFAULT 'active'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sgt_mmsi ON sg_vessel_tracks(mmsi)",
    "CREATE INDEX IF NOT EXISTS idx_sgt_ts   ON sg_vessel_tracks(ts)",
]


def up(conn=None):
    _conn = conn or get_connection()
    try:
        for stmt in _DDL:
            _conn.execute(stmt)
        _conn.commit()
        print("[072_sg_vessel_tracks] migration up complete")
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    up()
