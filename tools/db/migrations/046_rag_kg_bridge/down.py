# CUI // SP-CTI
"""Migration 046 rollback — remove RAG-KG bridge additions.

Drops (in dependency order):
  INDEX  idx_kg_nodes_source_chunk
  TABLE  rag_kg_backfill_cursor
  COLUMN rag_chunks.kg_node_ids
  COLUMN kg_nodes.source_chunk_id

SQLite DROP COLUMN requires SQLite ≥ 3.35.0 (2021-03-12).
"""
import os

from tools.db.storage import get_connection

_BACKEND = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()


def down():
    conn = get_connection()
    try:
        actions = []

        # Drop index first (PG drops it automatically with the column, but be explicit)
        conn.execute("DROP INDEX IF EXISTS idx_kg_nodes_source_chunk")
        actions.append("idx_kg_nodes_source_chunk_dropped")

        # Drop backfill cursor table
        conn.execute("DROP TABLE IF EXISTS rag_kg_backfill_cursor")
        actions.append("rag_kg_backfill_cursor_dropped")

        if _BACKEND == "postgresql":
            conn.execute(
                "ALTER TABLE rag_chunks DROP COLUMN IF EXISTS kg_node_ids"
            )
            actions.append("rag_chunks.kg_node_ids_dropped")

            conn.execute(
                "ALTER TABLE kg_nodes DROP COLUMN IF EXISTS source_chunk_id"
            )
            actions.append("kg_nodes.source_chunk_id_dropped")

        else:
            # SQLite: DROP COLUMN supported since 3.35.0; no IF EXISTS syntax
            conn.execute("ALTER TABLE rag_chunks DROP COLUMN kg_node_ids")
            actions.append("rag_chunks.kg_node_ids_dropped")

            conn.execute("ALTER TABLE kg_nodes DROP COLUMN source_chunk_id")
            actions.append("kg_nodes.source_chunk_id_dropped")

        conn.commit()
        print(f"Migration 046 down: {', '.join(actions)}")
        return {"status": "rolled_back", "actions": actions}
    finally:
        conn.close()


if __name__ == "__main__":
    down()
