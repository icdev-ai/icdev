#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 061 — add vulnerability_score to sg_entities.

sg_entities already exists with id, entity_type, entity_subtype, source,
external_id, location_wkt, and a UNIQUE INDEX on (source, external_id).

This migration adds:
  vulnerability_score REAL NOT NULL DEFAULT 0.5

The OSM importer seeds every logistics node at 0.5.
The domain scorer (tools/strategos/domain_scorer.py) updates scores
based on criticality, proximity, and targeting history.
"""
from tools.db.storage import get_connection


def _table_exists(conn, table: str) -> bool:
    """True if ``table`` exists. ``sg_entities`` is created later (migration 118,
    or at runtime by the strategos modules) — so a migrate-only fresh DB, like
    the CI E2E PostgreSQL job running this migration before 118, legitimately
    lacks it. Skip rather than abort the whole chain."""
    if getattr(conn, "_backend", "sqlite") == "postgresql":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def up(conn=None) -> None:
    conn = get_connection()
    try:
        if not _table_exists(conn, "sg_entities"):
            print("Migration 061 up: sg_entities absent — skipping (created later/at runtime).")
            return
        conn.execute(
            "ALTER TABLE sg_entities "
            "ADD COLUMN IF NOT EXISTS vulnerability_score REAL NOT NULL DEFAULT 0.5"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_ent_vuln "
            "ON sg_entities (vulnerability_score)"
        )
        conn.commit()
        print("Migration 061 applied: sg_entities.vulnerability_score added.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
