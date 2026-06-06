#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 101 — sg_ooda_assessments.

Stores aggregated OODA cycle assessment scores per wargame, one row per
assessment run. Each row captures observe/orient/decide/act scores
(0.0–1.0) and an optional overall composite score.
"""

from tools.db.storage import get_connection, is_pg

MIGRATION_ID = "101"
MIGRATION_NAME = "sg_ooda_assessments"

_DDL_SQLITE = """CREATE TABLE IF NOT EXISTS sg_ooda_assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    wargame_id      TEXT    NOT NULL,
    observe_score   REAL    NOT NULL DEFAULT 0.0,
    orient_score    REAL    NOT NULL DEFAULT 0.0,
    decide_score    REAL    NOT NULL DEFAULT 0.0,
    act_score       REAL    NOT NULL DEFAULT 0.0,
    overall_score   REAL    NOT NULL DEFAULT 0.0,
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

_DDL_PG = """CREATE TABLE IF NOT EXISTS sg_ooda_assessments (
    id              SERIAL  PRIMARY KEY,
    wargame_id      TEXT    NOT NULL,
    observe_score   REAL    NOT NULL DEFAULT 0.0,
    orient_score    REAL    NOT NULL DEFAULT 0.0,
    decide_score    REAL    NOT NULL DEFAULT 0.0,
    act_score       REAL    NOT NULL DEFAULT 0.0,
    overall_score   REAL    NOT NULL DEFAULT 0.0,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
)"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_ooda_wargame ON sg_ooda_assessments(wargame_id)",
    "CREATE INDEX IF NOT EXISTS idx_sg_ooda_created  ON sg_ooda_assessments(created_at DESC)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute(_DDL_PG if is_pg() else _DDL_SQLITE)
        for idx in _INDICES:
            try:
                conn.execute(idx)
            except Exception:
                pass
        conn.commit()
        print("[101_sg_ooda_assessments] migration up complete")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
