#!/usr/bin/env python3
# CUI // SP-CTI
"""Revert migration 027: drop compliance_snapshots table + indexes."""

import sqlite3

MIGRATION_ID = "027"


def down(conn: sqlite3.Connection) -> dict:
    conn.execute("DROP INDEX IF EXISTS idx_cs_taken_at")
    conn.execute("DROP INDEX IF EXISTS idx_cs_control_status")
    conn.execute("DROP INDEX IF EXISTS idx_cs_project_framework")
    conn.execute("DROP TABLE IF EXISTS compliance_snapshots")
    conn.commit()
    return {
        "status": "reverted",
        "actions": ["dropped_indexes", "dropped_table"],
    }
