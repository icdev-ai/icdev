#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 208: component-level audit log.

Adds ``component_audit_log`` for append-only recording of enable/disable
actions, core-profile applications, and per-tenant component overrides.
"""

import sqlite3

MIGRATION_ID = "208"
MIGRATION_NAME = "component_audit_log"
DESCRIPTION = (
    "Create component_audit_log table for tracking configuration changes to "
    "canvases, child apps, features, and core profiles."
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS component_audit_log (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    tenant_id TEXT,
    component_key TEXT,
    profile_name TEXT,
    details TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI',
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_component_audit_log_event ON component_audit_log(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_component_audit_log_component ON component_audit_log(component_key);",
    "CREATE INDEX IF NOT EXISTS idx_component_audit_log_recorded_at ON component_audit_log(recorded_at);",
]


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration idempotently."""
    conn.execute(_CREATE_TABLE)
    for stmt in _CREATE_INDEXES:
        conn.execute(stmt)
    conn.commit()
    return {
        "status": "applied",
        "table": "component_audit_log",
        "indexes": len(_CREATE_INDEXES),
    }
