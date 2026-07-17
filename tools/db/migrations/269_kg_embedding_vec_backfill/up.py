#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 269: backfill kg_nodes.embedding_vec from the legacy embedding BLOB.

PostgreSQL only. graph_rag now ranks semantic candidates with pgvector's ``<=>``
operator against ``kg_nodes.embedding_vec``; rows that only carry the packed
float32 ``embedding`` BLOB (written before Tier B) are invisible to that path
until re-enriched. This one-time, idempotent backfill unpacks each BLOB and
writes the pgvector column so existing graphs keep working without waiting for
re-enrichment.

Dimension-agnostic: whatever length the BLOB implies is what gets stored (the
column is an unbounded ``vector``). graph_rag's ``vector_dims`` guard skips at
read time any row whose stored dimensionality differs from the live query
embedding, so a later provider/dimension change can never error the search.

Requires pgvector. Safe with 0 rows. SQLite is unaffected (BLOB path unchanged).
"""

import struct


def _is_postgres(conn) -> bool:
    mod = type(conn).__module__
    return "psycopg" in mod or "psycopg2" in mod or "storage" in mod


def up(conn):
    if not _is_postgres(conn):
        return {"status": "skipped", "reason": "SQLite — BLOB path unchanged"}

    cur = conn.cursor()

    # kg_nodes.embedding_vec must exist (owned by the consolidated schema).
    try:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'kg_nodes' AND column_name = 'embedding_vec'"
        )
        if cur.fetchone() is None:
            return {"status": "skipped", "reason": "kg_nodes.embedding_vec absent"}
    except Exception:
        return {"status": "skipped", "reason": "information_schema unavailable"}

    # pgvector must be present; create it if we can, else skip (keep the BLOBs).
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception:
        conn.rollback()
    cur.execute("SELECT 1 FROM pg_type WHERE typname = 'vector'")
    if cur.fetchone() is None:
        return {"status": "skipped", "reason": "pgvector unavailable; kept BLOBs"}

    cur.execute(
        "SELECT id, embedding FROM kg_nodes "
        "WHERE embedding IS NOT NULL AND embedding_vec IS NULL"
    )
    rows = cur.fetchall()

    backfilled = 0
    skipped = 0
    for row in rows:
        nid = row[0]
        blob = row[1]
        if blob is None:
            continue
        try:
            raw = bytes(blob)
            n = len(raw) // 4
            if n == 0:
                skipped += 1
                continue
            floats = struct.unpack(f"{n}f", raw)
            vec_str = "[" + ",".join(f"{v:.6f}" for v in floats) + "]"
            cur.execute(
                "UPDATE kg_nodes SET embedding_vec = %s::vector WHERE id = %s",
                (vec_str, nid),
            )
            backfilled += 1
            if backfilled % 200 == 0:
                conn.commit()  # bound lock duration on large graphs
        except Exception:
            skipped += 1
            continue

    conn.commit()
    return {"status": "ok", "backfilled": backfilled, "skipped": skipped}


def down(conn):
    # No-op: the enricher also writes embedding_vec now, so clearing it would
    # discard live data, not just this backfill. The backfill is idempotent
    # (WHERE embedding_vec IS NULL), so re-running up() is safe instead.
    return {"status": "noop"}
