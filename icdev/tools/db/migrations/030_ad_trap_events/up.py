#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 030: Create ad_trap_events table.

Resolves gap op-gap-39cf4608dd3bf9da detected by the Internal Awareness Engine
(orphan_db_table on ad_trap_events).

The table is queried by tools/genesis/reflexes/alphadesk_trap_scenarios.py.
The INSERT write path is implemented in Phase 7.12 (trap scanner daemon).
This migration ensures the schema exists on fresh deployments so the reflex
can SELECT without error and the orphan_db_table gap does not re-fire.
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS ad_trap_events (
    id           SERIAL PRIMARY KEY,
    ticker       TEXT        NOT NULL,
    pattern      TEXT        NOT NULL,
    broken_level REAL,
    breakout_bar TEXT,
    reentry_bar  TEXT,
    confidence   REAL,
    volume_ratio REAL,
    bar_age      INTEGER,
    timeframe    TEXT,
    evidence_json TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS ad_trap_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT        NOT NULL,
    pattern      TEXT        NOT NULL,
    broken_level REAL,
    breakout_bar TEXT,
    reentry_bar  TEXT,
    confidence   REAL,
    volume_ratio REAL,
    bar_age      INTEGER,
    timeframe    TEXT,
    evidence_json TEXT,
    created_at   TEXT        NOT NULL DEFAULT (datetime('now'))
)
"""


def up(conn=None) -> None:
    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()
    try:
        dialect = getattr(conn, "_dialect", "sqlite")
        ddl = _DDL if dialect == "postgresql" else _DDL_SQLITE
        conn.execute(ddl)
        if _own_conn:
            conn.commit()
        print("[030_ad_trap_events] up: ad_trap_events created (or already exists)")
    finally:
        if _own_conn:
            conn.close()


if __name__ == "__main__":
    up()
