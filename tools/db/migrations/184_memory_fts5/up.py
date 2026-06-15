#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 184: FTS5 full-text search index over memory_entries (adapt-hermes-01).

Creates the `memory_fts` FTS5 virtual table (SQLite) for fast full-text search
over session history. FTS5 is a SQLite extension — on PostgreSQL deployments this
migration is a no-op; the session_indexer.py falls back to ILIKE search.

SQLite stores content locally in the FTS5 table so updates to memory_entries
do NOT auto-propagate; `tools/memory/session_indexer.py::reindex_all()` refreshes
the index on demand.
"""
import logging

logger = logging.getLogger(__name__)

_FTS5_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
    USING fts5(id UNINDEXED, content, type, tags)
"""

_POPULATE = """
INSERT OR IGNORE INTO memory_fts(id, content, type, tags)
SELECT id,
       COALESCE(content, ''),
       COALESCE(type, ''),
       COALESCE(tags, '')
FROM memory_entries
"""


def _is_sqlite(conn) -> bool:
    try:
        conn.execute("SELECT sqlite_version()")
        return True
    except Exception:
        return False


def up(conn):
    """Create memory_fts FTS5 virtual table (SQLite-only; no-op on PostgreSQL)."""
    if not _is_sqlite(conn):
        logger.info("[migration 184] PostgreSQL detected — FTS5 not applicable, skipping")
        print("[migration 184] PostgreSQL — memory_fts skipped (FTS5 is SQLite-only)")
        return

    try:
        conn.execute(_FTS5_DDL)
        conn.execute(_POPULATE)
        try:
            conn.commit()
        except Exception:
            pass
        count = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        print(f"[migration 184] memory_fts FTS5 table created, {count} rows indexed")
    except Exception as exc:
        logger.warning("[migration 184] FTS5 creation failed (FTS5 may be unavailable): %s", exc)
        print(f"[migration 184] WARNING: FTS5 unavailable — {exc}")


def down(conn):
    """Drop memory_fts FTS5 table."""
    if not _is_sqlite(conn):
        return
    conn.execute("DROP TABLE IF EXISTS memory_fts")
    try:
        conn.commit()
    except Exception:
        pass
    print("[migration 184] memory_fts FTS5 table dropped")
