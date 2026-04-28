#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 066: Create regulatory_foresight_signals table.

Rationale (Regulatory Foresight, 2026-04-28): stores scored regulatory signals
sourced from proposed rulemakings, comment periods, and mandate estimates.
Feeds the Regulatory Foresight dashboard with impact scores, blast radius, and
time-to-mandate pressure metrics.

Guard: skipped if table already exists.
"""

import sqlite3

MIGRATION_ID = "066"
MIGRATION_NAME = "regulatory_foresight_signals"
DESCRIPTION = "Create regulatory_foresight_signals table"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def up(conn: sqlite3.Connection) -> dict:
    actions = []

    if _table_exists(conn, "regulatory_foresight_signals"):
        return {"status": "skipped", "reason": "table already exists"}

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE regulatory_foresight_signals (
            id                     TEXT PRIMARY KEY,
            source                 TEXT,
            doc_id                 TEXT,
            title                  TEXT,
            url                    TEXT,
            proposed_at            TEXT,
            comment_deadline       TEXT,
            estimated_mandate_date TEXT,
            affected_frameworks    TEXT,
            icdev_impact_areas     TEXT,
            time_to_mandate_days   INTEGER,
            icdev_impact_score     REAL,
            blast_radius_score     REAL,
            composite_score        REAL,
            status                 TEXT DEFAULT 'new',
            innovation_signal_id   TEXT,
            scanned_at             TEXT NOT NULL,
            classification         TEXT DEFAULT 'CUI // SP-CTI'
        )
    """)
    actions.append("table_created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_regfore_score "
        "ON regulatory_foresight_signals(composite_score DESC)"
    )
    actions.append("idx_regfore_score_ensured")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_regfore_source "
        "ON regulatory_foresight_signals(source)"
    )
    actions.append("idx_regfore_source_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
