#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 051 — Create sg_pir_requirements table.

Stores Priority Intelligence Requirements (PIR), Commander's Critical
Information Requirements (CCIR), and Essential Elements of Information
(EEI) for the Strategos PIR/CCIR management module.

Schema:
  sg_pir_requirements(id, pir_type, topic, description, collection_priority,
                       status, tasked_to, due_by, created_at, updated_at)

Idempotent: uses CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS.
"""
from tools.db.storage import get_connection

MIGRATION_ID = "051"
MIGRATION_NAME = "sg_pir_requirements"
DESCRIPTION = "Create sg_pir_requirements table for PIR/CCIR management"

_DDL = """
CREATE TABLE IF NOT EXISTS sg_pir_requirements (
    id                  TEXT PRIMARY KEY,
    pir_type            TEXT NOT NULL DEFAULT 'PIR'
                            CHECK(pir_type IN ('PIR','CCIR','EEI')),
    topic               TEXT NOT NULL,
    description         TEXT,
    collection_priority INTEGER NOT NULL DEFAULT 3
                            CHECK(collection_priority BETWEEN 1 AND 5),
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active','satisfied','cancelled')),
    tasked_to           TEXT,
    due_by              TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
)
"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_pir_type     ON sg_pir_requirements(pir_type)",
    "CREATE INDEX IF NOT EXISTS idx_sg_pir_status   ON sg_pir_requirements(status)",
    "CREATE INDEX IF NOT EXISTS idx_sg_pir_priority ON sg_pir_requirements(collection_priority)",
    "CREATE INDEX IF NOT EXISTS idx_sg_pir_created  ON sg_pir_requirements(created_at)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute(_DDL)
        for idx in _INDICES:
            try:
                conn.execute(idx)
            except Exception:
                pass
        conn.commit()
        print("Migration 051 up: sg_pir_requirements created with 4 indexes.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
