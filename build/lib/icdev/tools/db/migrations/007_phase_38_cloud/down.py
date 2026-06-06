#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 007 rollback: Remove Phase 38 cloud provider tables."""


def down(conn):
    """Drop Phase 38 cloud provider tables in reverse dependency order."""
    tables = [
        "cloud_tenant_csp_config",
        "cloud_provider_status",
    ]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
