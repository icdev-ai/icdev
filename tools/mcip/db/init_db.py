# CUI // SP-CTI
"""MCIP DAT — DB initializer for mcip_dat_events and mcip_dti_scores.

Both tables live in the global ICDEV DB (get_connection) because they carry a
`classification` column that participates in the RLS predicate.

mcip_dti_scores is append-only / immutable (NIST AU-9).  It is registered in
APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py — never UPDATE or DELETE rows.
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

log = get_logger("mcip.db.init_db")

_SCHEMA_STMTS = [
    """
    CREATE TABLE IF NOT EXISTS mcip_dat_events (
        id              TEXT PRIMARY KEY,
        source_type     TEXT NOT NULL,
        content_hash    TEXT NOT NULL,
        sender          TEXT NOT NULL DEFAULT 'unknown',
        recipient       TEXT NOT NULL DEFAULT 'unknown',
        classification  TEXT NOT NULL DEFAULT 'CUI',
        tension_signal  REAL NOT NULL DEFAULT 0.0,
        ingested_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_source ON mcip_dat_events(source_type)",
    "CREATE INDEX IF NOT EXISTS idx_mcip_dat_events_at     ON mcip_dat_events(ingested_at)",
    """
    CREATE TABLE IF NOT EXISTS mcip_dti_scores (
        id              TEXT PRIMARY KEY,
        score           REAL NOT NULL,
        cable_sub       REAL NOT NULL DEFAULT 0.0,
        unsc_sub        REAL NOT NULL DEFAULT 0.0,
        backchannel_sub REAL NOT NULL DEFAULT 0.0,
        event_count     INTEGER NOT NULL DEFAULT 0,
        computed_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mcip_dti_scores_at ON mcip_dti_scores(computed_at)",
]


def init_db() -> None:
    """Create mcip_dat_events and mcip_dti_scores in the global ICDEV DB."""
    # cvx-sql-03: reviewed — BY DESIGN the storage-global get_connection() is used
    # here. These tables live in the global ICDEV DB and mcip_dat_events carries a
    # `classification` column that participates in the RLS predicate, so this is
    # NOT a canvas-connection bypass. Do not convert to get_canvas_connection().
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        for stmt in _SCHEMA_STMTS:
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception as exc:
                    log.debug("mcip init_db stmt skipped (likely exists): %s", exc)
        conn.commit()
        log.info("mcip init_db: mcip_dat_events + mcip_dti_scores ready")
    except Exception as exc:
        log.error("mcip init_db failed: %s", exc)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
