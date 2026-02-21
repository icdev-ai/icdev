#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 003: Dev profiles — versioned development standards with 5-layer cascade.

Targets data/icdev.db.
Adds: dev_profiles (D183), dev_profile_locks (D184), dev_profile_detections (D185).
"""

import sqlite3


def _table_exists(conn, table):
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


DEV_PROFILES_SCHEMA = """
-- Versioned dev profiles — immutable rows per D183
CREATE TABLE IF NOT EXISTS dev_profiles (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('platform','tenant','program','project','user')),
    scope_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    profile_md TEXT,
    profile_yaml TEXT NOT NULL,
    inherits_from TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_active INTEGER DEFAULT 1,
    change_summary TEXT,
    approved_by TEXT,
    approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dev_profiles_scope ON dev_profiles(scope, scope_id, is_active);
CREATE INDEX IF NOT EXISTS idx_dev_profiles_active ON dev_profiles(scope_id, is_active, version);

-- Dimension locks — role-based governance (D184)
CREATE TABLE IF NOT EXISTS dev_profile_locks (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES dev_profiles(id),
    dimension_path TEXT NOT NULL,
    lock_owner_role TEXT NOT NULL CHECK(lock_owner_role IN ('isso','architect','pm','admin')),
    locked_by TEXT NOT NULL,
    locked_at TEXT NOT NULL DEFAULT (datetime('now')),
    reason TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_dev_profile_locks_profile ON dev_profile_locks(profile_id, is_active);

-- Auto-detection results — advisory only per D185
CREATE TABLE IF NOT EXISTS dev_profile_detections (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    project_id TEXT,
    session_id TEXT,
    repo_url TEXT,
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    detection_results TEXT NOT NULL,
    accepted INTEGER DEFAULT 0,
    accepted_by TEXT,
    accepted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_dev_profile_detections_tenant ON dev_profile_detections(tenant_id);
"""


def up(conn):
    """Apply dev_profiles tables to icdev.db."""
    if not _table_exists(conn, "dev_profiles"):
        conn.executescript(DEV_PROFILES_SCHEMA)
    if not _table_exists(conn, "dev_profile_locks"):
        conn.executescript(
            DEV_PROFILES_SCHEMA.split("-- Dimension locks")[1].split(
                "-- Auto-detection"
            )[0]
        )
    if not _table_exists(conn, "dev_profile_detections"):
        conn.executescript(
            DEV_PROFILES_SCHEMA.split("-- Auto-detection results")[1]
        )
    conn.commit()


def down(conn):
    """Rollback: drop dev_profiles tables."""
    conn.execute("DROP TABLE IF EXISTS dev_profile_detections")
    conn.execute("DROP TABLE IF EXISTS dev_profile_locks")
    conn.execute("DROP TABLE IF EXISTS dev_profiles")
    conn.commit()
