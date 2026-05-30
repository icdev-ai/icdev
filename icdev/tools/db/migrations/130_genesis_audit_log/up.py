"""Migration 130 — create genesis_audit_log table.

Canonical schema registration for the genesis_audit_log append-only audit table.
Referenced by tools/genesis/reflexes/sim_training_export.py (and any other
Genesis reflex that calls _log_audit). Resolves the orphan_db_table gap
detected by tools/awareness/gap_detector.py.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS genesis_audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    reflex_name  TEXT NOT NULL,
    status       TEXT NOT NULL,
    details_json TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gal_reflex_name
    ON genesis_audit_log (reflex_name);

CREATE INDEX IF NOT EXISTS idx_gal_created_at
    ON genesis_audit_log (created_at);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
