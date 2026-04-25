#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 037: Create canvas_events table for the cross-canvas event bus.

tools/canvas/event_bus.py writes to this table on every publish() call.
consumed_at is nullable; mark_consumed() stamps it when a subscriber
confirms processing.

The table is append-only by intent (NIST AU): rows are never deleted and
payload history is preserved for audit. consumed_at is the sole mutable
column (stamped once, never cleared).
"""
from __future__ import annotations

from tools.db.storage import get_connection

_DDL = """
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
_IDX_TYPE = "CREATE INDEX IF NOT EXISTS idx_canvas_events_event_type ON canvas_events(event_type)"
_IDX_CONSUMED = "CREATE INDEX IF NOT EXISTS idx_canvas_events_consumed ON canvas_events(consumed_at)"


def up() -> None:
    conn = get_connection()
    try:
        backend = getattr(conn, "_backend", "sqlite")
        ddl = _DDL if backend == "postgresql" else _DDL_SQLITE
        conn.execute(ddl)
        conn.execute(_IDX_SOURCE)
        conn.execute(_IDX_TYPE)
        conn.execute(_IDX_CONSUMED)
        conn.commit()
        print("[037_canvas_events] up: canvas_events created (or already exists)")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
