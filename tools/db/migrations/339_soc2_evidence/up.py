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


def up(conn=None) -> dict:
    """Create evidence_items.

    The runner calls ``mod.up(conn)`` and owns the transaction. This previously
    declared ``up()`` with no parameters and opened its own connection, so the
    runner raised ``TypeError: up() takes 0 positional arguments but 1 was
    given`` — it would have failed on first contact with the runner. Nobody
    found out because migration 211 shares its version with
    211_idr_tables.sql, which sorts first, so this one was never dispatched.

    ``conn`` stays optional so the ``__main__`` path below still works
    standalone; committing is left to the runner when it owns the connection.
    """
    own = conn is None
    if own:
        conn = get_connection()
    try:
        created = 0
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
                created += 1
        if own:
            conn.commit()
        return {"statements": created}
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    up()
    print("Migration 211 (soc2 evidence_items) applied.")
