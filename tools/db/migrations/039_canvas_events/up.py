#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 039: Create canvas_events table.

Provides the persistence layer for the cross-canvas event bus
(tools/canvas/event_bus.py).  Rows are append-only (NIST AU) — once a
row is inserted only consumed_at may be updated by the bus dispatcher.
"""
from __future__ import annotations

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection

_DDL_PG = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id             TEXT        NOT NULL,
    source_canvas  TEXT        NOT NULL,
    target_canvas  TEXT,
    event_type     TEXT        NOT NULL,
    payload_json   TEXT        NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at    TIMESTAMPTZ,
    PRIMARY KEY (id)
)
"""

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id             TEXT NOT NULL,
    source_canvas  TEXT NOT NULL,
    target_canvas  TEXT,
    event_type     TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    consumed_at    TEXT,
    PRIMARY KEY (id)
)
"""

_IDX_SOURCE = "CREATE INDEX IF NOT EXISTS idx_canvas_events_source ON canvas_events(source_canvas)"
_IDX_TARGET = "CREATE INDEX IF NOT EXISTS idx_canvas_events_target ON canvas_events(target_canvas)"
_IDX_TYPE = "CREATE INDEX IF NOT EXISTS idx_canvas_events_type ON canvas_events(event_type)"
_IDX_CONSUMED = "CREATE INDEX IF NOT EXISTS idx_canvas_events_consumed ON canvas_events(consumed_at)"


def up(conn=None) -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        is_pg = backend == "postgresql"

        conn.execute(_DDL_PG if is_pg else _DDL_SQLITE)
        conn.execute(_IDX_SOURCE)
        conn.execute(_IDX_TARGET)
        conn.execute(_IDX_TYPE)
        conn.execute(_IDX_CONSUMED)
        conn.commit()
        print("[039_canvas_events] up: canvas_events created (or already exists)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
