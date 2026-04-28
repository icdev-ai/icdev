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


def up(conn=None) -> None:
    conn = get_connection()
    try:
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
