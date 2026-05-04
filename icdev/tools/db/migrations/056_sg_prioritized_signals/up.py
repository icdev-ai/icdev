# CUI // SP-CTI
"""Migration 056 — Create sg_prioritized_signals table.

Stores top-20 scored signals produced by signal_scout reflex each run.
Each row records composite_score, sub-scores, and a JSON rationale.
"""
from tools.db.storage import get_connection, is_pg

MIGRATION_ID = "056"
MIGRATION_NAME = "sg_prioritized_signals"
DESCRIPTION = "Create sg_prioritized_signals output table for signal_scout reflex"

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS sg_prioritized_signals (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_signal_id               INTEGER NOT NULL,
    composite_score             REAL NOT NULL,
    posterior_shift_score       REAL NOT NULL DEFAULT 0.0,
    source_discriminability_score REAL NOT NULL DEFAULT 0.0,
    temporal_recency_score      REAL NOT NULL DEFAULT 0.0,
    domain_coverage_score       REAL NOT NULL DEFAULT 0.0,
    rationale                   TEXT,
    run_at                      TEXT NOT NULL,
    created_at                  TEXT NOT NULL
)
"""

_DDL_PG = """
CREATE TABLE IF NOT EXISTS sg_prioritized_signals (
    id                          SERIAL PRIMARY KEY,
    raw_signal_id               INTEGER NOT NULL,
    composite_score             REAL NOT NULL,
    posterior_shift_score       REAL NOT NULL DEFAULT 0.0,
    source_discriminability_score REAL NOT NULL DEFAULT 0.0,
    temporal_recency_score      REAL NOT NULL DEFAULT 0.0,
    domain_coverage_score       REAL NOT NULL DEFAULT 0.0,
    rationale                   TEXT,
    run_at                      TEXT NOT NULL,
    created_at                  TEXT NOT NULL
)
"""

_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_sg_pri_score    ON sg_prioritized_signals(composite_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sg_pri_run_at   ON sg_prioritized_signals(run_at)",
    "CREATE INDEX IF NOT EXISTS idx_sg_pri_raw_id   ON sg_prioritized_signals(raw_signal_id)",
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
        print("Migration 056 up: sg_prioritized_signals created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
