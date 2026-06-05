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
        # pgvector may be absent (e.g. the CI E2E job's stock postgres:15 image
        # has no `vector` type). Try to enable it; if it can't be created the
        # type is unavailable, so leave embedding as bytea (untyped BLOB still
        # stores raw embeddings) rather than failing the whole migration chain.
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            conn.rollback()
        cur.execute("SELECT 1 FROM pg_type WHERE typname = 'vector'")
        if cur.fetchone() is None:
            return  # pgvector unavailable — skip the type change, keep bytea
        # Safe: 0 existing non-null embeddings; NULL rows stay NULL after cast
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
