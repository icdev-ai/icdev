# CUI // SP-CTI
"""Migration 043 — Add content_fingerprint column to memory_entries.

Adds:
  memory_entries.content_fingerprint TEXT
  UNIQUE INDEX idx_me_fingerprint ON memory_entries(content_fingerprint)
    WHERE content_fingerprint IS NOT NULL  (partial, skips NULLs)

Backfill: SHA-256 hex digest of content for all existing rows.
  PostgreSQL: encode(sha256(content::bytea), 'hex')
  SQLite:     Python hashlib loop (sha256)

Idempotent — column ADD is guarded by IF NOT EXISTS (PG) or PRAGMA table_info
check (SQLite); index uses CREATE ... IF NOT EXISTS.
"""
import hashlib
import os

from tools.db.storage import get_connection

MIGRATION_ID = "043"
MIGRATION_NAME = "memory_fingerprint"
DESCRIPTION = "Add content_fingerprint TEXT column + partial unique index to memory_entries"

_BACKEND = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()


def _column_exists_sqlite(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
    return any(row[1] == column for row in rows)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def up(conn=None):
    conn = get_connection()
    try:
        actions = []

        if _BACKEND == "postgresql":
            # PostgreSQL: ALTER TABLE ... ADD COLUMN IF NOT EXISTS
            conn.execute(
                "ALTER TABLE memory_entries "
                "ADD COLUMN IF NOT EXISTS content_fingerprint TEXT"
            )
            actions.append("column_ensured_pg")

            # Partial unique index — skips NULL fingerprints
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_me_fingerprint "
                "ON memory_entries(content_fingerprint) "
                "WHERE content_fingerprint IS NOT NULL"
            )
            actions.append("index_ensured_pg")

            # Backfill using PostgreSQL's native sha256/encode functions
            conn.execute(
                "UPDATE memory_entries "
                "SET content_fingerprint = encode(sha256(content::bytea), 'hex') "
                "WHERE content IS NOT NULL AND content_fingerprint IS NULL"
            )
            actions.append("backfill_pg")

        else:
            # SQLite path
            if not _column_exists_sqlite(conn, "memory_entries", "content_fingerprint"):
                conn.execute(
                    "ALTER TABLE memory_entries ADD COLUMN content_fingerprint TEXT"
                )
                actions.append("column_added_sqlite")
            else:
                actions.append("column_exists_sqlite")

            # SQLite supports partial (WHERE) indexes since 3.8.0 (2013)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_me_fingerprint "
                "ON memory_entries(content_fingerprint) "
                "WHERE content_fingerprint IS NOT NULL"
            )
            actions.append("index_ensured_sqlite")

            # Backfill via Python hashlib — SQLite has no built-in sha256
            rows = conn.execute(
                "SELECT id, content FROM memory_entries "
                "WHERE content IS NOT NULL AND content_fingerprint IS NULL"
            ).fetchall()
            for row in rows:
                row_id = row[0]
                content = row[1]
                conn.execute(
                    "UPDATE memory_entries SET content_fingerprint = ? WHERE id = ?",
                    (_sha256_hex(content), row_id),
                )
            actions.append(f"backfill_sqlite_rows={len(rows)}")

        conn.commit()
        print(f"Migration 043 up: {', '.join(actions)}")
        return {"status": "applied", "actions": actions}
    finally:
        conn.close()


if __name__ == "__main__":
    up()
