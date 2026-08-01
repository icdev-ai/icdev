#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG KG Search API — parameterized hybrid search with pgvector/SQLite detection.

Endpoints:
    POST /api/rag-kg/search — Semantic search over rag_chunks with tenant isolation

All SQL uses fully parameterized queries — no f-strings, no % formatting,
no string concatenation for SQL values. Bandit B608 clean by design.

Backend detection:
    is_pg(conn) → True  : pgvector cosine distance via sqlalchemy text() + bindparams
    is_pg(conn) → False : Python cosine similarity over SQLite BLOB embeddings
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, jsonify, request as flask_request  # noqa: E402
from tools.db.storage import get_connection, is_pg  # noqa: E402

rag_kg_search_api = Blueprint("rag_kg_search_api", __name__)

# ---------------------------------------------------------------------------
# Static SQL templates — all variable parts are bind parameters only
# ---------------------------------------------------------------------------

# PostgreSQL: pgvector cosine distance via <=> operator.
# :embedding is a pgvector string '[0.1,0.2,...]' bound at execute time.
# :tenant_id, :min_tier, :classification, :top_k are filter bind params.
_PG_SEARCH_SQL = (
    "SELECT id, content, source_type, source_id, source_table, "
    "chunk_index, metadata, tier, classification, "
    "1 - (embedding_vec <=> :embedding::vector) AS score "
    "FROM rag_chunks "
    "WHERE tenant_id = :tenant_id "
    "AND tier >= :min_tier "
    "AND classification = :classification "
    "AND embedding_vec IS NOT NULL "
    "ORDER BY embedding_vec <=> :embedding::vector "
    "LIMIT :top_k"
)

# SQLite: fetch candidates with bound named params, rank via Python cosine.
_SQLITE_FETCH_SQL = (
    "SELECT id, content, source_type, source_id, source_table, "
    "chunk_index, metadata, tier, classification, embedding "
    "FROM rag_chunks "
    "WHERE tenant_id = :tenant_id "
    "AND tier >= :min_tier "
    "AND classification = :classification "
    "AND embedding IS NOT NULL"
)


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _embedding_to_pg_vector(embedding: List[float]) -> str:
    """Serialize float list to pgvector literal '[x,y,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"


def _blob_to_embedding(blob: bytes) -> List[float]:
    """Deserialize a packed embedding BLOB to a float list.

    Header-aware (rce-quant-01): blobs written by SQLiteVectorStore carry a
    magic + dtype byte selecting float16/float32; blobs without the magic are
    read as legacy raw float32. Kept in sync with
    tools/rag/sqlite_vector_store._blob_to_embedding.
    """
    magic = b"RVQ1"
    char_to_dtype = {"f": "float32", "e": "float16"}
    if len(blob) >= 5 and blob[:4] == magic:
        char = chr(blob[4])
        if char in char_to_dtype:
            payload = blob[5:]
            n = len(payload) // struct.calcsize(char)
            return list(struct.unpack(f"{n}{char}", payload))
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity with numpy fast path and pure-python fallback."""
    try:
        import numpy as np

        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        dot = float(np.dot(va, vb))
        norm = float(np.linalg.norm(va) * np.linalg.norm(vb))
        return dot / norm if norm > 0 else 0.0
    except ImportError:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        return dot / (norm_a * norm_b) if norm_a > 0 and norm_b > 0 else 0.0


# ---------------------------------------------------------------------------
# Backend-specific search implementations
# ---------------------------------------------------------------------------

def _search_pg(
    conn,
    query_embedding: List[float],
    tenant_id: str,
    top_k: int,
    min_tier: str,
    classification: str,
) -> List[Dict[str, Any]]:
    """pgvector search via sqlalchemy text() with bindparams.

    Compiles a parameterized query for psycopg2 using the PostgreSQL dialect
    so that all values — including the embedding vector — are bound parameters
    and never interpolated into the SQL string.
    """
    from sqlalchemy import text as sa_text
    from sqlalchemy.dialects import postgresql as pg_dialect

    vec_str = _embedding_to_pg_vector(query_embedding)

    stmt = sa_text(_PG_SEARCH_SQL).bindparams(
        embedding=vec_str,
        tenant_id=tenant_id,
        min_tier=min_tier,
        classification=classification,
        top_k=top_k,
    )
    compiled = stmt.compile(dialect=pg_dialect.dialect())

    # Execute against the raw psycopg2 connection (RealDictCursor factory).
    # conn._conn is _PooledPgConnection; cursor() delegates to the raw PG conn.
    cursor = conn._conn.cursor()
    try:
        cursor.execute(str(compiled), dict(compiled.params))
        rows = cursor.fetchall()
    finally:
        cursor.close()

    results = []
    for row in rows:
        entry = dict(row)
        entry["metadata"] = json.loads(entry.get("metadata") or "{}")
        results.append(entry)
    return results


def _search_sqlite(
    conn,
    query_embedding: List[float],
    tenant_id: str,
    top_k: int,
    min_tier: str,
    classification: str,
) -> List[Dict[str, Any]]:
    """SQLite search: fetch filtered candidates with bound params, rank via Python cosine.

    Uses SQLite named-parameter style (:name) with a dict so the driver binds
    all filter values — no string formatting touches the SQL template.
    """
    rows = conn.execute(
        _SQLITE_FETCH_SQL,
        {"tenant_id": tenant_id, "min_tier": min_tier, "classification": classification},
    ).fetchall()

    scored: List[tuple] = []
    for row in rows:
        blob = row["embedding"]
        if not blob:
            continue
        try:
            emb = _blob_to_embedding(bytes(blob))
            score = _cosine_similarity(query_embedding, emb)
            scored.append((row, score))
        except Exception:
            continue

    scored.sort(key=lambda x: x[1], reverse=True)

    results = []
    for row, score in scored[:top_k]:
        results.append(
            {
                "id": row["id"],
                "content": row["content"],
                "source_type": row["source_type"],
                "source_id": row["source_id"],
                "source_table": row["source_table"],
                "chunk_index": row["chunk_index"],
                "metadata": json.loads(row["metadata"] or "{}"),
                "tier": row["tier"],
                "classification": row["classification"],
                "score": score,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def search_handler(
    query_embedding: List[float],
    tenant_id: str,
    top_k: int = 10,
    min_tier: str = "cold",
    classification: str = "CUI",
) -> List[Dict[str, Any]]:
    """Parameterized RAG hybrid search with pgvector/SQLite auto-detection.

    Detects the active backend via StorageConnection._backend:
        - postgresql → _search_pg  (pgvector <=> operator, sqlalchemy bindparams)
        - sqlite     → _search_sqlite (Python cosine over BLOB embeddings, named params)

    Args:
        query_embedding: Float vector representing the query.
        tenant_id:       Tenant scope filter (required; pass "" for global queries).
        top_k:           Maximum results to return (default 10).
        min_tier:        Minimum chunk tier — hot|warm|cold (default "cold" = all).
        classification:  Classification label filter (default "CUI").

    Returns:
        List of result dicts with id, content, score, tier, classification, metadata.
    """
    conn = get_connection()
    try:
        if is_pg(conn):
            return _search_pg(conn, query_embedding, tenant_id, top_k, min_tier, classification)
        return _search_sqlite(conn, query_embedding, tenant_id, top_k, min_tier, classification)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------

@rag_kg_search_api.route("/api/rag-kg/search", methods=["POST"])
def rag_kg_search():
    """POST /api/rag-kg/search — parameterized RAG KG semantic search.

    Request body (JSON):
        embedding      list[float]  Query embedding vector (required)
        tenant_id      str          Tenant scope (default: "")
        top_k          int          Max results (default: 10)
        min_tier       str          Minimum tier: hot|warm|cold (default: "cold")
        classification str          Classification label (default: "CUI")

    Response (JSON):
        results  list[dict]  Ranked chunks with score, content, metadata
        count    int         Number of results returned
    """
    data = flask_request.get_json(silent=True) or {}

    embedding = data.get("embedding")
    if not embedding or not isinstance(embedding, list):
        return jsonify({"error": "embedding must be a non-empty list of floats"}), 400

    tenant_id = str(data.get("tenant_id", ""))
    top_k = int(data.get("top_k", 10))
    min_tier = str(data.get("min_tier", "cold"))
    classification = str(data.get("classification", "CUI"))

    if min_tier not in ("hot", "warm", "cold"):
        return jsonify({"error": "min_tier must be one of: hot, warm, cold"}), 400

    try:
        results = search_handler(
            query_embedding=embedding,
            tenant_id=tenant_id,
            top_k=top_k,
            min_tier=min_tier,
            classification=classification,
        )
        return jsonify({"results": results, "count": len(results)})
    except Exception as exc:
        return jsonify({"error": str(exc), "results": [], "count": 0}), 500
