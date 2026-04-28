#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 067: Create usage_events (append-only) and usage_aggregates tables.

Rationale (pint usage tracking, 2026-04-28): usage_events records every HTTP
request for audit and adoption analysis (append-only, NIST AU). usage_aggregates
holds daily rollup counts for dashboards and adoption scoring (updatable).

Guard: skip if either table already exists.
"""

import sqlite3

MIGRATION_ID = "067"
MIGRATION_NAME = "usage_events"
DESCRIPTION = "Create usage_events append-only table and usage_aggregates rollup table"


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
    cur = conn.cursor()

    if _table_exists(conn, "usage_events"):
        actions.append("usage_events_exists")
    else:
        cur.execute("""
            CREATE TABLE usage_events (
                id               TEXT PRIMARY KEY,
                route            TEXT,
                method           TEXT,
                status_code      INTEGER,
                duration_ms      INTEGER,
                skill_invoked    TEXT,
                user_session     TEXT,
                ip_hash          TEXT,
                feature_tag      TEXT,
                occurred_at      TEXT NOT NULL,
                classification   TEXT DEFAULT 'CUI // SP-CTI'
            )
        """)
        actions.append("usage_events_created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_route "
        "ON usage_events(route)"
    )
    actions.append("idx_usage_route_ensured")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_occurred_at "
        "ON usage_events(occurred_at)"
    )
    actions.append("idx_usage_occurred_at_ensured")

    if _table_exists(conn, "usage_aggregates"):
        actions.append("usage_aggregates_exists")
    else:
        cur.execute("""
            CREATE TABLE usage_aggregates (
                id               TEXT PRIMARY KEY,
                feature_tag      TEXT,
                date             TEXT,
                hit_count        INTEGER,
                unique_sessions  INTEGER,
                error_count      INTEGER,
                avg_duration_ms  REAL,
                adoption_score   REAL,
                updated_at       TEXT
            )
        """)
        actions.append("usage_aggregates_created")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agg_feature_date "
        "ON usage_aggregates(feature_tag, date)"
    )
    actions.append("idx_agg_feature_date_ensured")

    conn.commit()
    return {"status": "applied", "actions": actions}
