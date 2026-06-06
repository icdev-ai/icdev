#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 064 — sg_pattern_learner_log (signature delta learning records)."""

from tools.db.storage import get_connection

_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS sg_pattern_learner_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        signature_id TEXT    NOT NULL,
        delta        REAL    NOT NULL,
        reason       TEXT,
        ts           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_spll_sig ON sg_pattern_learner_log(signature_id)",
    "CREATE INDEX IF NOT EXISTS idx_spll_ts  ON sg_pattern_learner_log(ts)",
]


def up(conn=None) -> None:
    conn = get_connection()
    try:
        for stmt in _STATEMENTS:
            conn.execute(stmt)
        conn.commit()
        print("[064_sg_pattern_learner_log] migration up complete")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
