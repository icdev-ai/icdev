#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 301: retype memory_entries.embedding vector(1536) -> vector(768).

Migration 044 sized the column vector(1536) (OpenAI text-embedding-3-small), but the
configured/air-gap embedding provider is Ollama nomic-embed-text at 768 dimensions
(gemini text-embedding-004 and ibm-slate are also 768). So every embedding write
failed with `expected 1536 dimensions, not 768` and coverage stayed at 0%.

PostgreSQL only — SQLite stores embeddings as an untyped BLOB and is unaffected.
Safe because coverage is 0% (all embeddings are NULL); ``USING NULL`` re-nulls the
column at the new dimension, so nothing is converted or lost. Idempotent: a no-op if
the column is already vector(768) or is not a pgvector column (pgvector absent).
"""


def _embedding_typmod(cur):
    """Return the pgvector dimension of memory_entries.embedding, or None if the
    column is not a vector type / the table is absent. For pgvector, atttypmod IS
    the declared dimension."""
    try:
        cur.execute(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_type t ON a.atttypid = t.oid "
            "WHERE a.attrelid = 'memory_entries'::regclass "
            "AND a.attname = 'embedding' AND t.typname = 'vector'"
        )
        row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    return row[0] if not hasattr(row, "keys") else list(row.values())[0]


def up(conn):
    cur = conn.cursor()
    dim = _embedding_typmod(cur)
    if dim is None:
        # Not a vector column (SQLite BLOB, or pgvector absent / column bytea) — nothing to do.
        return {"status": "skipped", "reason": "embedding is not a pgvector column"}
    if dim == 768:
        return {"status": "skipped", "reason": "already vector(768)"}
    cur.execute("ALTER TABLE memory_entries ALTER COLUMN embedding TYPE vector(768) USING NULL")
    conn.commit()
    return {"status": "ok", "from_dim": dim, "to_dim": 768}


def down(conn):
    cur = conn.cursor()
    dim = _embedding_typmod(cur)
    if dim is None or dim == 1536:
        return
    cur.execute("ALTER TABLE memory_entries ALTER COLUMN embedding TYPE vector(1536) USING NULL")
    conn.commit()
