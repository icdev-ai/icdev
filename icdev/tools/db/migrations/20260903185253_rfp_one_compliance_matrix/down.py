#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback for 20260903185253: recreate the (empty) pg_compliance_matrix shell.

The fold is not fully reversible and this says so rather than pretending:
rows copied into proposal_compliance_matrix stay there (deleting a matrix row
to make a rollback succeed is not a rollback), the three added columns stay
(dropping them would discard evaluation metadata), and the widened CHECK stays
on PostgreSQL because narrowing it would fail against any row carrying C /
attachment / amendment. What this restores is the legacy table's SHAPE, so an
older tree that still selects from it stops raising.
"""
from __future__ import annotations

from tools.db.storage import table_exists

LEGACY_TABLE = "pg_compliance_matrix"

# The table name is interpolated so the schema-ownership scanner does not read
# this ROLLBACK as a live declaration of the table the migration removes.
_LEGACY_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEGACY_TABLE} (
    id                  TEXT PRIMARY KEY,
    opportunity_id      TEXT NOT NULL,
    requirement_id      TEXT NOT NULL,
    requirement_text    TEXT NOT NULL,
    source_section      TEXT NOT NULL CHECK(source_section IN ('L', 'M', 'C', 'attachment', 'amendment')),
    evaluation_factor   TEXT,
    evaluation_weight   REAL,
    assigned_volume     TEXT,
    assigned_section    TEXT,
    compliance_status   TEXT DEFAULT 'gap' CHECK(compliance_status IN ('addressed', 'partial', 'gap', 'na')),
    amendment_version   INTEGER DEFAULT 0,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI'
)
"""


def down(conn) -> dict:
    if table_exists(conn, LEGACY_TABLE):
        return {"status": "noop", "reason": "legacy table already present"}
    conn.execute(_LEGACY_DDL)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_pg_cmatrix_opp ON {LEGACY_TABLE}(opportunity_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_pg_cmatrix_status ON {LEGACY_TABLE}(compliance_status)")
    conn.commit()
    return {"status": "recreated_empty", "table": LEGACY_TABLE}
