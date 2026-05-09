#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Migration 028: Add mitre_coverage table for ODC Twin.

Creates:
  mitre_coverage — append-only log of MITRE ATT&CK technique coverage events.
  Each row is one coverage observation (state change = new row, not an update).
  State values: none | partial | full | false_positive
  Primary lookup: (technique_id, signal_source, project_id) → latest row.

Idempotent — uses CREATE TABLE IF NOT EXISTS.
"""

MIGRATION_ID = "028"
MIGRATION_NAME = "odc_mitre_coverage"
DESCRIPTION = "Add mitre_coverage table for ODC Twin (append-only, NIST AU)"


_COVERAGE_DDL = """
CREATE TABLE IF NOT EXISTS mitre_coverage (
    id               TEXT PRIMARY KEY,
    technique_id     TEXT NOT NULL,
    signal_source    TEXT NOT NULL,
    state            TEXT NOT NULL
                         CHECK(state IN ('none', 'partial', 'full', 'false_positive')),
    last_observed_at TEXT NOT NULL,
    project_id       TEXT NOT NULL,
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_mc_project    ON mitre_coverage (project_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_technique  ON mitre_coverage (technique_id, project_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_state      ON mitre_coverage (state)",
    "CREATE INDEX IF NOT EXISTS idx_mc_observed   ON mitre_coverage (last_observed_at)",
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return bool(row)


def up(conn) -> dict:
    actions = []

    if _table_exists(conn, "mitre_coverage"):
        actions.append("table_exists")
        conn.commit()
        return {"status": "applied", "actions": actions}

    conn.execute(_COVERAGE_DDL)
    actions.append("created_table")

    for idx_sql in _INDEX_DDL:
        conn.execute(idx_sql)
    actions.append("created_indexes")

    conn.commit()
    return {"status": "applied", "actions": actions}
