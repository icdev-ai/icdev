#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 207: per-tenant canvas/component enablement overrides.

Adds ``tenant_component_overrides`` so enterprise tenants can enable or
disable individual canvases/features independently of the global env flag.
A missing row falls back to the environment/default setting.
"""

import sqlite3

MIGRATION_ID = "207"
MIGRATION_NAME = "tenant_component_overrides"
DESCRIPTION = (
    "Create tenant_component_overrides table for per-tenant canvas/feature "
    "enablement overrides."
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tenant_component_overrides (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    component_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    updated_by TEXT DEFAULT 'system',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tenant_id, component_key)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tenant_component_overrides_tenant ON tenant_component_overrides(tenant_id);",
    "CREATE INDEX IF NOT EXISTS idx_tenant_component_overrides_key ON tenant_component_overrides(component_key);",
]


def up(conn: sqlite3.Connection) -> dict:
    """Apply migration idempotently."""
    conn.execute(_CREATE_TABLE)
    for stmt in _CREATE_INDEXES:
        conn.execute(stmt)
    conn.commit()
    return {
        "status": "applied",
        "table": "tenant_component_overrides",
        "indexes": len(_CREATE_INDEXES),
    }
