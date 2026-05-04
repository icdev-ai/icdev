# CUI // SP-CTI
"""Migration 043 — memory_entity_relationships table for tracking typed relationships
between memory entries (supports/contradicts/supersedes/related).

Adds:
  memory_entity_relationships(id, from_entry_id, to_entry_id, rel_type,
                               confidence, created_by, created_at)
  UNIQUE(from_entry_id, to_entry_id, rel_type)
  idx_mer_from ON memory_entity_relationships(from_entry_id)
  idx_mer_to   ON memory_entity_relationships(to_entry_id)
  idx_mer_type ON memory_entity_relationships(rel_type)
"""
from tools.db.storage import get_connection

_REL_TYPES = ("supports", "contradicts", "supersedes", "related")


def up(conn=None):
    conn = get_connection()
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS memory_entity_relationships (
                id              SERIAL PRIMARY KEY,
                from_entry_id   INT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
                to_entry_id     INT NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
                rel_type        TEXT NOT NULL CHECK(rel_type IN {_REL_TYPES!r}),
                confidence      REAL DEFAULT 1.0,
                created_by      TEXT DEFAULT 'lint_reflex',
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(from_entry_id, to_entry_id, rel_type)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mer_from ON memory_entity_relationships(from_entry_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mer_to ON memory_entity_relationships(to_entry_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mer_type ON memory_entity_relationships(rel_type)"
        )
        conn.commit()
        print("Migration 043 up: memory_entity_relationships created with 3 indexes.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
