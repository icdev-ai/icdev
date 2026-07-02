# CUI // SP-CTI
"""Migration 211 — SOC 2 evidence_items table.

evidence_items is append-only (NIST AU-9): rows are immutable compliance
evidence records. Never UPDATE or DELETE rows — use superseding inserts
with a newer collected_at timestamp instead.
"""
from __future__ import annotations

from tools.db.storage import get_connection

DDL = """
CREATE TABLE IF NOT EXISTS evidence_items (
    id              TEXT PRIMARY KEY,
    control_id      TEXT NOT NULL,
    framework       TEXT NOT NULL DEFAULT 'soc2',
    tenant_id       TEXT NOT NULL,
    evidence_type   TEXT CHECK(evidence_type IN ('log','config','test_result','screenshot','policy')),
    source_table    TEXT,
    source_row_id   TEXT,
    summary         TEXT,
    collected_at    TEXT NOT NULL DEFAULT (datetime('now')),
    collector       TEXT NOT NULL DEFAULT 'auto',
    classification  TEXT NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_evidence_items_control
    ON evidence_items(tenant_id, control_id);
CREATE INDEX IF NOT EXISTS idx_evidence_items_framework
    ON evidence_items(framework, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_items_source
    ON evidence_items(source_table, source_row_id, control_id)
    WHERE source_table IS NOT NULL AND source_row_id IS NOT NULL;
"""


def up() -> None:
    with get_connection() as conn:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()


if __name__ == "__main__":
    up()
    print("Migration 211 (soc2 evidence_items) applied.")
