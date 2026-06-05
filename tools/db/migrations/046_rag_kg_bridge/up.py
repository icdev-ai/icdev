# CUI // SP-CTI
"""Migration 046 — RAG-KG bridge: source_chunk_id back-ref, kg_node_ids, backfill cursor.

Adds:
  kg_nodes.source_chunk_id INT FK → rag_chunks(id) ON DELETE SET NULL
  rag_chunks.kg_node_ids   TEXT DEFAULT '[]'  (JSON array of int IDs, TEXT for SQLite compat)
  rag_kg_backfill_cursor   TABLE (id PK, last_chunk_id INT, updated_at TIMESTAMPTZ)
  idx_kg_nodes_source_chunk INDEX ON kg_nodes(source_chunk_id)

Idempotent — ADD COLUMN guarded by IF NOT EXISTS (PG) or PRAGMA table_info (SQLite);
table/index use CREATE IF NOT EXISTS; cursor seed uses ON CONFLICT DO NOTHING (PG)
or INSERT OR IGNORE (SQLite).
"""
import os

from tools.db.storage import get_connection

MIGRATION_ID = "046"
MIGRATION_NAME = "rag_kg_bridge"
DESCRIPTION = (
    "Add source_chunk_id FK on kg_nodes, kg_node_ids JSON on rag_chunks, "
    "backfill cursor table, and source_chunk index"
)

_BACKEND = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()


def _column_exists_sqlite(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
    return any(row[1] == column for row in rows)


def _table_exists(conn, table: str) -> bool:
    """True if `table` exists. kg_nodes is created by a later migration (058),
    so on a fresh ordered replay it may not exist yet — guard against that."""
    if _BACKEND == "postgresql":
        row = conn.execute("SELECT to_regclass(?) AS t", (f"public.{table}",)).fetchone()
        if row is None:
            return False
        try:
            return row["t"] is not None
        except Exception:
            return row[0] is not None
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def up(conn=None):
    conn = get_connection()
    try:
        actions = []
        kg_present = _table_exists(conn, "kg_nodes")

        if _BACKEND == "postgresql":
            # 1. FK column on kg_nodes — TEXT to match rag_chunks.id (which is TEXT).
            #    kg_nodes is created later (migration 058); skip on fresh replay.
            if kg_present:
                conn.execute(
                    "ALTER TABLE kg_nodes "
                    "ADD COLUMN IF NOT EXISTS source_chunk_id TEXT "
                    "REFERENCES rag_chunks(id) ON DELETE SET NULL"
                )
                actions.append("kg_nodes.source_chunk_id_ensured")
            else:
                actions.append("kg_nodes.skipped_absent")

            # 2. JSON back-ref array on rag_chunks
            conn.execute(
                "ALTER TABLE rag_chunks "
                "ADD COLUMN IF NOT EXISTS kg_node_ids TEXT DEFAULT '[]'"
            )
            actions.append("rag_chunks.kg_node_ids_ensured")

            # 3. Backfill cursor table
            conn.execute(
                "CREATE TABLE IF NOT EXISTS rag_kg_backfill_cursor ("
                "  id SERIAL PRIMARY KEY,"
                "  last_chunk_id INT NOT NULL DEFAULT 0,"
                "  updated_at TIMESTAMPTZ DEFAULT NOW()"
                ")"
            )
            actions.append("rag_kg_backfill_cursor_table_ensured")

            # Seed one cursor row so the backfill worker always finds a row
            conn.execute(
                "INSERT INTO rag_kg_backfill_cursor(last_chunk_id) VALUES(0) "
                "ON CONFLICT DO NOTHING"
            )
            actions.append("cursor_seed_pg")

            # 4. Index (only if kg_nodes exists yet)
            if kg_present:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kg_nodes_source_chunk "
                    "ON kg_nodes(source_chunk_id)"
                )
                actions.append("idx_kg_nodes_source_chunk_ensured")

        else:
            # SQLite path — kg_nodes is created later (058); skip if absent.
            if kg_present and not _column_exists_sqlite(conn, "kg_nodes", "source_chunk_id"):
                conn.execute(
                    "ALTER TABLE kg_nodes ADD COLUMN source_chunk_id TEXT "
                    "REFERENCES rag_chunks(id) ON DELETE SET NULL"
                )
                actions.append("kg_nodes.source_chunk_id_added")
            else:
                actions.append("kg_nodes.source_chunk_id_skipped")

            if not _column_exists_sqlite(conn, "rag_chunks", "kg_node_ids"):
                conn.execute(
                    "ALTER TABLE rag_chunks ADD COLUMN kg_node_ids TEXT DEFAULT '[]'"
                )
                actions.append("rag_chunks.kg_node_ids_added")
            else:
                actions.append("rag_chunks.kg_node_ids_exists")

            conn.execute(
                "CREATE TABLE IF NOT EXISTS rag_kg_backfill_cursor ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  last_chunk_id INT NOT NULL DEFAULT 0,"
                "  updated_at TEXT DEFAULT (datetime('now'))"
                ")"
            )
            actions.append("rag_kg_backfill_cursor_table_ensured")

            conn.execute(
                "INSERT OR IGNORE INTO rag_kg_backfill_cursor(id, last_chunk_id) VALUES(1, 0)"
            )
            actions.append("cursor_seed_sqlite")

            if kg_present:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kg_nodes_source_chunk "
                    "ON kg_nodes(source_chunk_id)"
                )
                actions.append("idx_kg_nodes_source_chunk_ensured")

        conn.commit()
        print(f"Migration 046 up: {', '.join(actions)}")
        return {"status": "applied", "actions": actions}
    finally:
        conn.close()


if __name__ == "__main__":
    up()
