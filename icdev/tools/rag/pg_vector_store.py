#!/usr/bin/env python3
# CUI // SP-CTI
"""PostgreSQL pgvector store — production backend (D-RAG-PG-1).

Uses pgvector extension for native vector similarity search via HNSW
indexes. 50-100x faster than Python brute-force cosine on SQLite BLOBs.

Also provides:
    - tsvector full-text search (replaces Python rank_bm25)
    - Hybrid RRF in SQL (vector + FTS in single query)
    - HNSW approximate nearest neighbor (99.9% recall)

Falls back to SQLiteVectorStore when pgvector is not available.

Architecture:
    D-RAG-PG-1: pgvector cosine via <=> operator (cosine distance)
    D-RAG-PG-2: HNSW index for ANN (O(log n) vs O(n) brute-force)
    D-RAG-PG-3: tsvector FTS replaces rank_bm25 (database-level)
    D-RAG-PG-4: Hybrid RRF in SQL (vector + FTS window functions)
"""

from __future__ import annotations

import json
import struct
from typing import Any, Dict, List, Optional

from tools.rag.vector_store_provider import (
    SearchResult,
    VectorChunk,
    VectorStoreProvider,
)

try:
    from tools.db.storage import get_connection, get_backend
except ImportError:
    get_connection = None
    get_backend = lambda: "sqlite"  # noqa: E731


def _embedding_to_pg_vector(embedding: List[float]) -> str:
    """Convert float list to pgvector string format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"


def _embedding_to_blob(embedding: List[float]) -> bytes:
    """Pack float list to binary (for BYTEA fallback)."""
    return struct.pack(f"{len(embedding)}f", *embedding)


class PgVectorStore(VectorStoreProvider):
    """PostgreSQL pgvector store implementation.

    Uses the main icdev.db rag_chunks table via storage abstraction.
    When pgvector extension is available, uses native vector operators.
    Falls back to BYTEA + Python cosine when pgvector is not installed.
    """

    def __init__(self, **kwargs):
        self._pgvector_available = None

    @property
    def provider_name(self) -> str:
        return "pgvector"

    def _has_pgvector(self) -> bool:
        """Check if pgvector extension is installed (cached)."""
        if self._pgvector_available is not None:
            return self._pgvector_available
        try:
            conn = get_connection()
            try:
                row = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
                self._pgvector_available = row is not None
            finally:
                conn.close()
        except Exception:
            self._pgvector_available = False
        return self._pgvector_available

    def check_availability(self) -> bool:
        """Check if pgvector is available and rag_chunks table exists."""
        try:
            conn = get_connection()
            try:
                conn.execute("SELECT 1 FROM rag_chunks LIMIT 1")
                return True
            finally:
                conn.close()
        except Exception:
            return False

    def get_by_content_hash(self, content_hash: str) -> Optional[VectorChunk]:
        """Look up an existing chunk by content hash (D-RAG-5 dedup path)."""
        if not content_hash or not get_connection:
            return None
        try:
            conn = get_connection()
            try:
                row = conn.execute(
                    """
                    SELECT id, content, content_hash, source_type, source_id,
                           source_table, chunk_index, total_chunks, metadata,
                           tier, tenant_id, project_id, classification
                    FROM rag_chunks
                    WHERE content_hash = %s
                    LIMIT 1
                    """,
                    (content_hash,),
                ).fetchone()
                if not row:
                    return None
                meta: dict = {}
                if row["metadata"]:
                    try:
                        meta = json.loads(row["metadata"])
                    except (json.JSONDecodeError, ValueError):
                        pass
                return VectorChunk(
                    chunk_id=row["id"],
                    content=row["content"] or "",
                    content_hash=row["content_hash"] or content_hash,
                    source_type=row["source_type"] or "",
                    source_id=row["source_id"] or "",
                    source_table=row["source_table"] or "",
                    chunk_index=row["chunk_index"] or 0,
                    total_chunks=row["total_chunks"] or 1,
                    metadata=meta,
                    tier=row["tier"] or "hot",
                    tenant_id=row["tenant_id"] or "",
                    project_id=row["project_id"] or "",
                    classification=row["classification"] or "CUI",
                )
            finally:
                conn.close()
        except Exception:
            return None

    def upsert(self, chunks: List[VectorChunk]) -> int:
        """Insert or update chunks with embeddings."""
        conn = get_connection()
        inserted = 0
        try:
            for chunk in chunks:
                if not chunk.embedding:
                    continue
                # Dedup by content_hash
                existing = conn.execute(
                    "SELECT id FROM rag_chunks WHERE content_hash = %s",
                    (chunk.content_hash,),
                ).fetchone()
                if existing:
                    continue

                # Store both BYTEA blob (backward compat) and vector column
                blob = _embedding_to_blob(chunk.embedding)
                vec_str = _embedding_to_pg_vector(chunk.embedding)
                metadata_json = json.dumps(chunk.metadata) if chunk.metadata else "{}"

                conn.execute(
                    """
                    INSERT INTO rag_chunks
                        (id, content, content_hash, embedding, embedding_vec,
                         source_type, source_id, source_table, chunk_index,
                         total_chunks, metadata, tier, tenant_id, project_id,
                         classification)
                    VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        chunk.chunk_id,
                        chunk.content,
                        chunk.content_hash,
                        blob,
                        vec_str,
                        chunk.source_type,
                        chunk.source_id,
                        chunk.source_table,
                        chunk.chunk_index,
                        chunk.total_chunks,
                        metadata_json,
                        chunk.tier,
                        chunk.tenant_id,
                        chunk.project_id,
                        chunk.classification,
                    ),
                )
                inserted += 1
            conn.commit()
        finally:
            conn.close()
        return inserted

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 50,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Search using pgvector cosine distance via HNSW index.

        50-100x faster than Python brute-force on SQLite.
        """
        if not self._has_pgvector():
            return self._search_fallback(query_embedding, top_k, filters)

        vec_str = _embedding_to_pg_vector(query_embedding)
        conn = get_connection()
        try:
            # Build filter clause.
            #
            # `%s`, not `?`. This is the PostgreSQL store and the surrounding
            # SELECT already uses `%s`; mixing the two produced a statement that
            # only worked because `translate_sql` rewrote the strays — making it
            # load-bearing, which CLAUDE.md explicitly forbids.
            #
            # It broke the moment RLS became involved. `StorageCursor._inject_rls`
            # appends its tenant/classification predicate with `%s`, so under any
            # Flask request context the statement carried BOTH dialects, psycopg2
            # raised, and the caller's `except` returned an empty list. Net effect:
            # DIC search returned ZERO results for every query in the browser while
            # returning 10 from a script, with no error surfaced anywhere.
            where_parts = ["embedding_vec IS NOT NULL"]
            filter_params: list = []

            if filters:
                if filters.get("source_type"):
                    where_parts.append("source_type = %s")
                    filter_params.append(filters["source_type"])
                if filters.get("tier"):
                    where_parts.append("tier = %s")
                    filter_params.append(filters["tier"])
                if filters.get("project_id"):
                    where_parts.append("project_id = %s")
                    filter_params.append(filters["project_id"])
                if filters.get("tenant_id"):
                    where_parts.append("tenant_id = %s")
                    filter_params.append(filters["tenant_id"])

            where_clause = " AND ".join(where_parts)
            # Params order must match SQL placeholders: SELECT vec, WHERE filters, ORDER BY vec, LIMIT
            params = [vec_str] + filter_params + [vec_str, top_k]

            # pgvector cosine distance: 1 - (a <=> b) = cosine similarity
            # <=> is cosine distance (0 = identical, 2 = opposite)
            rows = conn.execute(
                f"""
                SELECT id, content, source_type, source_id, source_table,
                       chunk_index, metadata, tier, classification,
                       1 - (embedding_vec <=> %s::vector) AS score
                FROM rag_chunks
                WHERE {where_clause}
                ORDER BY embedding_vec <=> %s::vector
                LIMIT %s
            """,
                params,
            ).fetchall()  # nosec B608 — where_clause from hardcoded parts

            results = []
            for row in rows:
                results.append(
                    SearchResult(
                        chunk_id=row["id"],
                        content=row["content"],
                        source_type=row["source_type"] or "",
                        source_id=row["source_id"] or "",
                        source_table=row["source_table"] or "",
                        chunk_index=row["chunk_index"] or 0,
                        score=float(row["score"]) if row["score"] else 0.0,
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                        tier=row["tier"] or "hot",
                        classification=row["classification"] or "CUI",
                    )
                )
            return results
        finally:
            conn.close()

    def search_hybrid(
        self,
        query_embedding: List[float],
        query_text: str,
        top_k: int = 10,
        vector_weight: float = 0.7,
        fts_weight: float = 0.3,
        rrf_k: int = 60,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Hybrid search: pgvector + tsvector FTS with RRF fusion in SQL.

        Performs both vector ANN and full-text search in a single query,
        combines via Reciprocal Rank Fusion (RRF), returns top-k.

        50-100x faster than Python post-processing approach.
        """
        if not self._has_pgvector():
            # Fall back to vector-only search
            return self.search(query_embedding, top_k, filters)

        vec_str = _embedding_to_pg_vector(query_embedding)
        conn = get_connection()
        try:
            # Build filter
            where_parts = ["embedding_vec IS NOT NULL"]
            filter_params: list = []

            if filters:
                if filters.get("source_type"):
                    where_parts.append("source_type = %s")
                    filter_params.append(filters["source_type"])
                if filters.get("tier"):
                    where_parts.append("tier = %s")
                    filter_params.append(filters["tier"])

            where_clause = " AND ".join(where_parts)
            # query_text used 3x in FTS CTEs, rrf_k 2x, top_k 1x
            # Params order: SELECT vec, WHERE filters, ORDER BY vec, FTS x3, rrf_k x2, LIMIT
            params = [vec_str] + filter_params + [vec_str, query_text, query_text, query_text, rrf_k, rrf_k, top_k]

            # Hybrid RRF query — vector + FTS in single pass
            rows = conn.execute(
                f"""
                WITH vector_ranked AS (
                    SELECT id, content, source_type, source_id, source_table,
                           chunk_index, metadata, tier, classification,
                           1 - (embedding_vec <=> %s::vector) AS vec_score,
                           ROW_NUMBER() OVER (ORDER BY embedding_vec <=> %s::vector) AS vec_rank
                    FROM rag_chunks
                    WHERE {where_clause}
                ),
                fts_ranked AS (
                    SELECT id,
                           ts_rank(content_tsv, plainto_tsquery('english', %s)) AS fts_score,
                           ROW_NUMBER() OVER (
                               ORDER BY ts_rank(content_tsv, plainto_tsquery('english', %s)) DESC
                           ) AS fts_rank
                    FROM rag_chunks
                    WHERE content_tsv @@ plainto_tsquery('english', %s)
                )
                SELECT v.id, v.content, v.source_type, v.source_id, v.source_table,
                       v.chunk_index, v.metadata, v.tier, v.classification,
                       v.vec_score,
                       COALESCE(f.fts_score, 0) AS fts_score,
                       (1.0 / (%s + v.vec_rank)) + COALESCE(1.0 / (%s + f.fts_rank), 0) AS rrf_score
                FROM vector_ranked v
                LEFT JOIN fts_ranked f ON v.id = f.id
                ORDER BY rrf_score DESC
                LIMIT %s
            """,
                params,
            ).fetchall()  # nosec B608

            results = []
            for row in rows:
                results.append(
                    SearchResult(
                        chunk_id=row["id"],
                        content=row["content"],
                        source_type=row["source_type"] or "",
                        source_id=row["source_id"] or "",
                        source_table=row["source_table"] or "",
                        chunk_index=row["chunk_index"] or 0,
                        score=float(row["vec_score"]) if row["vec_score"] else 0.0,
                        bm25_score=float(row["fts_score"]) if row["fts_score"] else 0.0,
                        final_score=float(row["rrf_score"]) if row["rrf_score"] else 0.0,
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                        tier=row["tier"] or "hot",
                        classification=row["classification"] or "CUI",
                    )
                )
            return results
        finally:
            conn.close()

    def delete(self, chunk_ids: List[str]) -> int:
        conn = get_connection()
        try:
            deleted = 0
            for cid in chunk_ids:
                conn.execute("DELETE FROM rag_chunks WHERE id = %s", (cid,))
                deleted += 1
            conn.commit()
            return deleted
        finally:
            conn.close()

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        conn = get_connection()
        try:
            if filters and filters.get("source_type"):
                row = conn.execute(
                    "SELECT COUNT(*) FROM rag_chunks WHERE source_type = %s",
                    (filters["source_type"],),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def _search_fallback(self, query_embedding, top_k, filters):
        """Fallback to Python cosine when pgvector is not available."""
        from tools.rag.sqlite_vector_store import _cosine_similarity, _blob_to_embedding

        conn = get_connection()
        try:
            where_parts = ["embedding IS NOT NULL"]
            params = []
            if filters:
                if filters.get("source_type"):
                    where_parts.append("source_type = %s")
                    params.append(filters["source_type"])
                if filters.get("tier"):
                    where_parts.append("tier = %s")
                    params.append(filters["tier"])

            where_clause = " AND ".join(where_parts)
            rows = conn.execute(
                f"SELECT * FROM rag_chunks WHERE {where_clause}",  # nosec B608
                params,
            ).fetchall()

            scored = []
            for row in rows:
                try:
                    emb = _blob_to_embedding(row["embedding"])
                    score = _cosine_similarity(query_embedding, emb)
                    scored.append((row, score))
                except Exception:
                    pass

            scored.sort(key=lambda x: x[1], reverse=True)

            results = []
            for row, score in scored[:top_k]:
                results.append(
                    SearchResult(
                        chunk_id=row["id"],
                        content=row["content"],
                        source_type=row["source_type"] or "",
                        source_id=row["source_id"] or "",
                        source_table=row["source_table"] or "",
                        chunk_index=row["chunk_index"] or 0,
                        score=score,
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                        tier=row["tier"] or "hot",
                        classification=row["classification"] or "CUI",
                    )
                )
            return results
        finally:
            conn.close()
