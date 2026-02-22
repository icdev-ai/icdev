#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 007: Phase 38 Cloud — CSP abstraction layer status tracking.

Targets data/icdev.db.
Adds: cloud_provider_status (D230), cloud_tenant_csp_config,
      csp_region_certifications (D233).
"""

import sqlite3


def _table_exists(conn, table):
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


CLOUD_SCHEMA = """
-- ============================================================
-- CLOUD PROVIDER STATUS — CSP health check history (D230)
-- ============================================================
CREATE TABLE IF NOT EXISTS cloud_provider_status (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    service TEXT NOT NULL
        CHECK(service IN ('secrets', 'storage', 'kms', 'monitoring', 'iam', 'registry')),
    status TEXT NOT NULL
        CHECK(status IN ('healthy', 'degraded', 'unavailable', 'error')),
    latency_ms REAL,
    error_message TEXT,
    checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cloud_status_service ON cloud_provider_status(service);
CREATE INDEX IF NOT EXISTS idx_cloud_status_provider ON cloud_provider_status(provider);
CREATE INDEX IF NOT EXISTS idx_cloud_status_checked ON cloud_provider_status(checked_at);
CREATE INDEX IF NOT EXISTS idx_cloud_status_status ON cloud_provider_status(status);

-- ============================================================
-- CLOUD TENANT CSP CONFIG — per-tenant CSP overrides (D225, D60)
-- ============================================================
CREATE TABLE IF NOT EXISTS cloud_tenant_csp_config (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    service TEXT NOT NULL
        CHECK(service IN ('secrets', 'storage', 'kms', 'monitoring', 'iam', 'registry', 'global')),
    provider TEXT NOT NULL
        CHECK(provider IN ('aws', 'azure', 'gcp', 'oci', 'ibm', 'local')),
    config_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tenant_id, service)
);

CREATE INDEX IF NOT EXISTS idx_cloud_tenant_config_tenant ON cloud_tenant_csp_config(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cloud_tenant_config_service ON cloud_tenant_csp_config(service);

-- ============================================================
-- CSP REGION CERTIFICATIONS — compliance certification registry (D233)
-- ============================================================
CREATE TABLE IF NOT EXISTS csp_region_certifications (
    id TEXT PRIMARY KEY,
    csp TEXT NOT NULL CHECK(csp IN ('aws', 'azure', 'gcp', 'oci', 'ibm')),
    region TEXT NOT NULL,
    certification TEXT NOT NULL,
    certification_level TEXT DEFAULT '',
    impact_levels TEXT DEFAULT '[]',
    verified_at TEXT,
    expires_at TEXT,
    source_url TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(csp, region, certification)
);

CREATE INDEX IF NOT EXISTS idx_csp_certs_csp ON csp_region_certifications(csp);
CREATE INDEX IF NOT EXISTS idx_csp_certs_region ON csp_region_certifications(region);
CREATE INDEX IF NOT EXISTS idx_csp_certs_cert ON csp_region_certifications(certification);
"""


def up(conn):
    """Apply Phase 38 cloud provider tables to icdev.db."""
    tables = [
        "cloud_provider_status",
        "cloud_tenant_csp_config",
        "csp_region_certifications",
    ]

    # Only create tables that don't exist yet (idempotent)
    missing = [t for t in tables if not _table_exists(conn, t)]
    if missing:
        conn.executescript(CLOUD_SCHEMA)

    conn.commit()


def down(conn):
    """Rollback: drop Phase 38 cloud provider tables."""
    tables = [
        "csp_region_certifications",
        "cloud_tenant_csp_config",
        "cloud_provider_status",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
