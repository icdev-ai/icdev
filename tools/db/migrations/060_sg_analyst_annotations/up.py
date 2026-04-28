#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 060 — sg_analyst_annotations.

Append-only analyst annotation store.  Any KG node, conflict event,
ORBAT unit, supply chain node, or prioritised signal can be annotated.

NIST AU-9: table is append-only.  No UPDATE or DELETE ever issued against
this table (enforced by APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py).
"""
from tools.db.storage import get_connection, is_pg

# Keep in sync with tools/intelligence/annotations.py ANNOTATION_TYPES
_ANNOTATION_TYPES = ("assessed", "unconfirmed", "disputed", "source_tag")
_ENTITY_TYPES = ("kg_node", "conflict_event", "orbat_unit", "supply_node", "signal")


def up(conn=None) -> None:
    conn = get_connection()
    annotation_check = "', '".join(_ANNOTATION_TYPES)
    entity_check = "', '".join(_ENTITY_TYPES)

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS sg_analyst_annotations (
            id               INTEGER PRIMARY KEY {'AUTOINCREMENT' if not is_pg() else 'GENERATED ALWAYS AS IDENTITY'},
            entity_type      TEXT NOT NULL CHECK(entity_type IN ('{entity_check}')),
            entity_id        TEXT NOT NULL,
            annotation_type  TEXT NOT NULL CHECK(annotation_type IN ('{annotation_check}')),
            content          TEXT NOT NULL,
            analyst_id       TEXT NOT NULL DEFAULT 'analyst',
            created_at       TEXT NOT NULL
        )
        """
    )

    # Index for the most common lookup: annotations for a specific entity
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_ann_entity "
        "ON sg_analyst_annotations (entity_type, entity_id)"
    )
    # Index for analyst workload queries
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sg_ann_analyst "
        "ON sg_analyst_annotations (analyst_id, created_at DESC)"
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 060 applied: sg_analyst_annotations created.")
