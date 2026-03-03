#!/usr/bin/env python3
# CUI // SP-CTI
"""Baseline migration — delegates to init_icdev_db.py.

This migration applies the complete ICDEV schema (124+ tables) as it exists
in tools/db/init_icdev_db.py. Rather than duplicating the 2860-line SQL
string, this Python migration imports and executes the existing init_db logic.

For existing databases that already have the schema, use:
    python tools/db/migrate.py --mark-applied 001
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def up(conn):
    """Apply the baseline schema using init_icdev_db.py's SCHEMA_SQL."""
    from tools.db.init_icdev_db import (
        SCHEMA_SQL,
        MBSE_ALTER_SQL,
        MODERNIZATION_ALTER_SQL,
        RICOAS_ALTER_SQL,
        AGENTIC_ALTER_SQL,
        MARKETPLACE_ALTER_SQL,
        FIPS_ALTER_SQL,
        COMPLIANCE_PLATFORM_ALTER_SQL,
        MOSA_ALTER_SQL,
    )
    import sqlite3

    # Apply main schema
    conn.executescript(SCHEMA_SQL)

    # Apply all ALTER TABLE lists (idempotent)
    for alter_list in [
        MBSE_ALTER_SQL,
        MODERNIZATION_ALTER_SQL,
        RICOAS_ALTER_SQL,
        AGENTIC_ALTER_SQL,
        MARKETPLACE_ALTER_SQL,
        FIPS_ALTER_SQL,
        COMPLIANCE_PLATFORM_ALTER_SQL,
        MOSA_ALTER_SQL,
    ]:
        for alter_sql in alter_list:
            try:
                conn.execute(alter_sql)
            except sqlite3.OperationalError:
                pass  # Column already exists

    conn.commit()


def down(conn):
    """Drop all tables (DEVELOPMENT ONLY — never in production)."""
    import sqlite3
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_migrations'")
    tables = [row[0] for row in c.fetchall()]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
