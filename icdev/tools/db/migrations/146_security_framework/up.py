#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 146 — Security Framework Audit Tables.

Append-only tables for:
- security_context_log
- abac_decisions
- mac_violations
- rls_audit
- column_mask_audit
- field_filter_audit
"""

from typing import Any

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS security_context_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT,
    clearance_level INTEGER,
    compartments TEXT,
    tenant_id TEXT,
    classification TEXT,
    auth_method TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS abac_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_name TEXT,
    decision TEXT NOT NULL,
    subject TEXT,
    resource TEXT,
    action TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mac_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event_type TEXT,
    resource_classification TEXT,
    action TEXT,
    details TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rls_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    action TEXT,
    tenant_id TEXT,
    details TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS column_mask_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    role TEXT,
    masked_columns TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS field_filter_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name TEXT,
    role TEXT,
    filtered_fields TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

POSTGRESQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS security_context_log (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT,
    clearance_level INTEGER,
    compartments TEXT,
    tenant_id TEXT,
    classification TEXT,
    auth_method TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS abac_decisions (
    id SERIAL PRIMARY KEY,
    policy_name TEXT,
    decision TEXT NOT NULL,
    subject TEXT,
    resource TEXT,
    action TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mac_violations (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    event_type TEXT,
    resource_classification TEXT,
    action TEXT,
    details TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rls_audit (
    id SERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    action TEXT,
    tenant_id TEXT,
    details TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS column_mask_audit (
    id SERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    role TEXT,
    masked_columns TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS field_filter_audit (
    id SERIAL PRIMARY KEY,
    schema_name TEXT,
    role TEXT,
    filtered_fields TEXT,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""


def up(conn: Any) -> None:
    backend = "sqlite"
    if hasattr(conn, "_backend"):
        backend = conn._backend
    elif hasattr(conn, "cursor") and hasattr(conn, "commit"):
        # Try to detect via duck typing
        try:
            import psycopg2
            if isinstance(conn, psycopg2.extensions.connection):
                backend = "postgresql"
        except Exception:
            pass

    schema = POSTGRESQL_SCHEMA if backend == "postgresql" else SQLITE_SCHEMA
    if hasattr(conn, "executescript"):
        conn.executescript(schema)
    else:
        for stmt in schema.split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()
