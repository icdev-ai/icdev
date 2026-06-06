#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 018 — reflex_observations table for tracking reflex execution telemetry.

Creates:
  reflex_observations(id, reflex_name, started_at, finished_at, duration_ms,
                      status, artifact_count, error_msg, result_json)
  idx_reflex_obs_name ON reflex_observations(reflex_name)
  idx_reflex_obs_started ON reflex_observations(started_at)

Idempotent: uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS.
"""
from tools.db.storage import get_connection

MIGRATION_ID = "018"
MIGRATION_NAME = "reflex_observations"
DESCRIPTION = "Create reflex_observations table for reflex execution telemetry"


def up(conn=None):
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reflex_observations (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                reflex_name    TEXT NOT NULL,
                started_at     TEXT NOT NULL DEFAULT (datetime('now')),
                finished_at    TEXT,
                duration_ms    INTEGER,
                status         TEXT NOT NULL DEFAULT 'running',
                artifact_count INTEGER DEFAULT 0,
                error_msg      TEXT,
                result_json    TEXT DEFAULT '{}'
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reflex_obs_name "
            "ON reflex_observations(reflex_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reflex_obs_started "
            "ON reflex_observations(started_at)"
        )
        conn.commit()
        print("Migration 018 up: reflex_observations created with 2 indexes.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
