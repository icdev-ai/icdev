#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 108 — Create sg_war_council_briefs table.

Creates:
  sg_war_council_briefs — AI-generated war council course-of-action briefs

Idempotent: all CREATE TABLE / CREATE INDEX use IF NOT EXISTS.
"""
from tools.db.storage import get_connection

MIGRATION_ID = "108"
MIGRATION_NAME = "sg_war_council_briefs"
DESCRIPTION = "Create sg_war_council_briefs table for AI-generated war council COA briefs"

_DDL = [
    """CREATE TABLE IF NOT EXISTS sg_war_council_briefs (
        id               TEXT PRIMARY KEY,
        theater          TEXT NOT NULL,
        scenario_summary TEXT,
        content_md       TEXT NOT NULL,
        rag_active       INTEGER NOT NULL DEFAULT 0,
        rag_doc_count    INTEGER NOT NULL DEFAULT 0,
        model_used       TEXT,
        latency_ms       REAL,
        generated_at     TEXT NOT NULL
    )""",
]

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_wcb_theater      ON sg_war_council_briefs(theater)",
    "CREATE INDEX IF NOT EXISTS idx_sg_wcb_generated_at ON sg_war_council_briefs(generated_at)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for ddl in _DDL:
            conn.execute(ddl)
        for idx in _INDICES:
            try:
                conn.execute(idx)
            except Exception:
                pass
        conn.commit()
        print("Migration 108 up: sg_war_council_briefs created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
