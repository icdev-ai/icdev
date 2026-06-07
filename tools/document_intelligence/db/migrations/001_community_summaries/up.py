#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 001: DIC community_summaries table.

Lightweight indexable structure for storing per-community generated summaries
with citation provenance. Does NOT touch existing KG nodes/edges.

Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
Supports both SQLite and PostgreSQL backends.
"""

import os
import sqlite3

MIGRATION_ID = "001"
MIGRATION_NAME = "community_summaries"
DESCRIPTION = "Add dic_community_summaries table to DIC schema"


_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS dic_community_summaries (
    summary_id      TEXT        PRIMARY KEY,
    community_id    TEXT        NOT NULL,
    summary_text    TEXT        NOT NULL,
    citations_list  TEXT        DEFAULT '[]',
    model_version   TEXT        DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_community ON dic_community_summaries(community_id);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_tenant ON dic_community_summaries(tenant_id);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS dic_community_summaries (
    summary_id      TEXT    PRIMARY KEY,
    community_id    TEXT    NOT NULL,
    summary_text    TEXT    NOT NULL,
    citations_list  TEXT    DEFAULT '[]',
    model_version   TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_community ON dic_community_summaries(community_id);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_tenant ON dic_community_summaries(tenant_id);
"""


def up(conn) -> dict:
    is_pg = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower() in (
        "postgresql", "postgres", "pg"
    )
    schema = _SCHEMA_PG if is_pg else _SCHEMA_SQLITE

    if is_pg:
        cur = conn.cursor()
        for stmt in schema.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
    else:
        conn.executescript(schema)
        conn.commit()

    return {
        "status": "applied",
        "table": "dic_community_summaries",
        "backend": "postgresql" if is_pg else "sqlite",
    }
