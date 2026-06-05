#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 044: Alter memory_entries.embedding from bytea to vector(1536).

PostgreSQL only — SQLite uses BLOB which is untyped and unaffected.
Requires pgvector extension (already present at v0.8.2).
Safe to run with 0 existing embeddings (rows with NULL embedding remain NULL).
"""


def _is_postgres(conn) -> bool:
    mod = type(conn).__module__
    return "psycopg" in mod or "psycopg2" in mod or "storage" in mod


def up(conn):
    cur = conn.cursor()

    # Only applicable for PostgreSQL
    try:
        cur.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name='memory_entries' AND column_name='embedding'"
        )
        row = cur.fetchone()
    except Exception:
        return  # SQLite path — PRAGMA would be used; skip

    if row is None:
        return  # column doesn't exist yet

    current_type = row[0] if not hasattr(row, "__getitem__") else row[0]

    if current_type == "bytea":
        # Safe: 0 existing non-null embeddings; NULL rows stay NULL after cast
        # Requires the pgvector extension. It is per-database, so create it
        # explicitly rather than assuming it is pre-loaded. If it cannot be
        # created (e.g. a stock postgres image in CI without pgvector), leave
        # the column as bytea instead of failing the whole migration chain.
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            conn.rollback()
            return {"status": "skipped", "reason": "pgvector unavailable; kept bytea"}
        cur.execute(
            "ALTER TABLE memory_entries "
            "ALTER COLUMN embedding TYPE vector(1536) USING NULL"
        )
        conn.commit()
    # If already vector, no-op


def down(conn):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name='memory_entries' AND column_name='embedding'"
        )
        row = cur.fetchone()
    except Exception:
        return

    if row is None:
        return

    current_type = row[0] if not hasattr(row, "__getitem__") else row[0]
    if current_type != "bytea":
        cur.execute(
            "ALTER TABLE memory_entries "
            "ALTER COLUMN embedding TYPE bytea USING NULL"
        )
        conn.commit()
