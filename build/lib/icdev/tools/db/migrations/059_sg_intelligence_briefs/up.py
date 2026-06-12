#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 059 — Create sg_intelligence_briefs table.

Creates:
  sg_intelligence_briefs — generated intelligence products (sitrep, iir, warnord, assessment)

Idempotent: all CREATE TABLE / CREATE INDEX use IF NOT EXISTS.
"""
from tools.db.storage import get_connection

MIGRATION_ID = "059"
MIGRATION_NAME = "sg_intelligence_briefs"
DESCRIPTION = "Create sg_intelligence_briefs table for generated intelligence products"

_DDL = [
    """CREATE TABLE IF NOT EXISTS sg_intelligence_briefs (
        id                  TEXT PRIMARY KEY,
        brief_type          TEXT NOT NULL CHECK(brief_type IN ('sitrep','iir','warnord','assessment')),
        title               TEXT NOT NULL,
        content_md          TEXT NOT NULL,
        sio_confidence      REAL,
        analyst_reviewed    INTEGER NOT NULL DEFAULT 0,
        reviewed_by         TEXT,
        reviewed_at         TEXT,
        annotations         TEXT,
        created_at          TEXT NOT NULL
    )""",
]

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_ib_type        ON sg_intelligence_briefs(brief_type)",
    "CREATE INDEX IF NOT EXISTS idx_sg_ib_reviewed     ON sg_intelligence_briefs(analyst_reviewed)",
    "CREATE INDEX IF NOT EXISTS idx_sg_ib_created_at   ON sg_intelligence_briefs(created_at)",
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
        print("Migration 059 up: sg_intelligence_briefs created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
