#!/usr/bin/env python3
# CUI // SP-PROPIN
"""RAG Service: embed document chunks and perform semantic similarity search.

Uses sentence-transformers (all-MiniLM-L6-v2, 384-dim) for embeddings,
stored as raw numpy float32 BLOBs in rfx_document_chunks.embedding.
Cosine similarity is computed in-process via numpy — no vector DB required.

Also searches kb_entries (GovProposal Knowledge Base) so past performance,
capabilities, and boilerplate are available as RAG sources.

Graceful degradation: if sentence-transformers is not installed, semantic
search falls back to BM25 keyword search (rank_bm25).
"""

import json
import os
import sqlite3
import struct
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

_MODEL = None  # lazy-loaded sentence-transformers model
_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBED_DIM = 384


def _get_model():
    """Lazy-load the embedding model (sentence-transformers)."""
    global _MODEL
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            _MODEL = SentenceTransformer(_MODEL_NAME)
        except ImportError:
            _MODEL = None
    return _MODEL


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── embedding helpers ──────────────────────────────────────────────────────────

def embed_text(text: str) -> Optional[np.ndarray]:
    """Embed a string. Returns float32 ndarray or None if model unavailable."""
    model = _get_model()
    if model is None:
        return None
    vec = model.encode(text, normalize_embeddings=True)
    return vec.astype(np.float32)


def _to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity (both vectors assumed unit-normalized)."""
    return float(np.dot(a, b))


# ── vectorization ──────────────────────────────────────────────────────────────

def vectorize_document(doc_id: str) -> dict:
    """Embed all chunks of a document and store BLOBs in the DB.

    Returns {"doc_id": ..., "chunks_embedded": int, "skipped": int}.
    """
    model = _get_model()
    if model is None:
        return {"error": "sentence-transformers not installed",
                "doc_id": doc_id, "chunks_embedded": 0}

    conn = _conn()
    try:
        chunks = conn.execute(
            "SELECT id, content FROM rfx_document_chunks "
            "WHERE document_id = ? AND embedding IS NULL",
            (doc_id,)
        ).fetchall()

        embedded = 0
        for chunk in chunks:
            vec = embed_text(chunk["content"])
            if vec is not None:
                conn.execute(
                    "UPDATE rfx_document_chunks SET embedding = ?, "
                    "embedding_model = ? WHERE id = ?",
                    (_to_blob(vec), _MODEL_NAME, chunk["id"])
                )
                embedded += 1

        conn.execute(
            "UPDATE rfx_documents SET vectorized = 1, "
            "vectorized_at = datetime('now') WHERE id = ?",
            (doc_id,)
        )
        conn.commit()
        return {"doc_id": doc_id, "chunks_embedded": embedded,
                "skipped": len(chunks) - embedded}
    finally:
        conn.close()


def vectorize_kb_entry(entry_id: str) -> dict:
    """Embed a Knowledge Base entry and store in kb_embeddings."""
    model = _get_model()
    if model is None:
        return {"error": "sentence-transformers not installed"}

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, title, content FROM kb_entries WHERE id = ?",
            (entry_id,)
        ).fetchone()
        if not row:
            return {"error": "KB entry not found"}

        text = f"{row['title']}\n\n{row['content']}"
        vec = embed_text(text)
        if vec is None:
            return {"error": "embedding failed"}

        # Upsert into kb_embeddings
        existing = conn.execute(
            "SELECT id FROM kb_embeddings WHERE kb_entry_id = ?",
            (entry_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE kb_embeddings SET embedding = ?, model = ?, "
                "dimensions = ? WHERE kb_entry_id = ?",
                (_to_blob(vec), _MODEL_NAME, _EMBED_DIM, entry_id)
            )
        else:
            conn.execute(
                "INSERT INTO kb_embeddings (id, kb_entry_id, embedding, "
                "model, dimensions, created_at) VALUES (?,?,?,?,?,datetime('now'))",
                (str(uuid.uuid4()), entry_id, _to_blob(vec), _MODEL_NAME, _EMBED_DIM)
            )
        conn.commit()
        return {"entry_id": entry_id, "status": "ok"}
    finally:
        conn.close()


# ── semantic search ────────────────────────────────────────────────────────────

def search_chunks(query: str, top_k: int = 5,
                  doc_ids: Optional[list[str]] = None,
                  min_score: float = 0.25) -> list[dict]:
    """Search rfx_document_chunks by semantic similarity.

    Falls back to BM25 keyword search if embeddings unavailable.
    Returns list of dicts with keys: chunk_id, document_id, content,
    score, chunk_index, filename.
    """
    q_vec = embed_text(query)

    conn = _conn()
    try:
        sql = """
            SELECT c.id, c.document_id, c.content, c.chunk_index,
                   c.embedding, d.filename
            FROM rfx_document_chunks c
            JOIN rfx_documents d ON c.document_id = d.id
            WHERE c.embedding IS NOT NULL
        """
        params: list = []
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            sql += f" AND c.document_id IN ({placeholders})"
            params.extend(doc_ids)

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    if q_vec is None or not rows:
        # BM25 fallback
        return _bm25_search(query, top_k, doc_ids)

    scored = []
    for row in rows:
        try:
            chunk_vec = _from_blob(row["embedding"])
            score = _cosine(q_vec, chunk_vec)
            if score >= min_score:
                scored.append({
                    "chunk_id": row["id"],
                    "document_id": row["document_id"],
                    "content": row["content"],
                    "chunk_index": row["chunk_index"],
                    "filename": row["filename"],
                    "score": round(score, 4),
                    "source": "rfx_doc",
                })
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def search_kb(query: str, top_k: int = 5,
              entry_types: Optional[list[str]] = None,
              min_score: float = 0.25) -> list[dict]:
    """Search GovProposal Knowledge Base entries by semantic similarity."""
    q_vec = embed_text(query)

    conn = _conn()
    try:
        sql = """
            SELECT k.id, k.title, k.content, k.entry_type, k.tags,
                   e.embedding
            FROM kb_entries k
            JOIN kb_embeddings e ON k.id = e.kb_entry_id
            WHERE k.is_active = 1
        """
        params: list = []
        if entry_types:
            placeholders = ",".join("?" * len(entry_types))
            sql += f" AND k.entry_type IN ({placeholders})"
            params.extend(entry_types)

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    if q_vec is None or not rows:
        return []

    scored = []
    for row in rows:
        try:
            chunk_vec = _from_blob(row["embedding"])
            score = _cosine(q_vec, chunk_vec)
            if score >= min_score:
                scored.append({
                    "entry_id": row["id"],
                    "title": row["title"],
                    "content": row["content"][:500],
                    "entry_type": row["entry_type"],
                    "tags": row["tags"],
                    "score": round(score, 4),
                    "source": "kb",
                })
        except Exception:
            continue

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def search_all(query: str, top_k: int = 8,
               proposal_id: Optional[str] = None) -> dict:
    """Combined search: rfx chunks + KB entries, merged and ranked.

    Returns {"chunks": [...], "kb": [...], "combined": [top_k merged]}.
    """
    # Get doc_ids scoped to proposal if given
    doc_ids = None
    if proposal_id:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT id FROM rfx_documents WHERE proposal_id = ?",
                (proposal_id,)
            ).fetchall()
            doc_ids = [r["id"] for r in rows]
        finally:
            conn.close()

    chunk_results = search_chunks(query, top_k=top_k, doc_ids=doc_ids)
    kb_results = search_kb(query, top_k=top_k // 2)

    # Merge and re-rank
    combined = chunk_results + kb_results
    combined.sort(key=lambda x: x["score"], reverse=True)

    return {
        "query": query,
        "chunks": chunk_results,
        "kb": kb_results,
        "combined": combined[:top_k],
    }


# ── BM25 fallback ──────────────────────────────────────────────────────────────

def _bm25_search(query: str, top_k: int = 5,
                 doc_ids: Optional[list[str]] = None) -> list[dict]:
    """BM25 keyword search fallback when no embeddings available."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return []

    conn = _conn()
    try:
        sql = """
            SELECT c.id, c.document_id, c.content, c.chunk_index, d.filename
            FROM rfx_document_chunks c
            JOIN rfx_documents d ON c.document_id = d.id
        """
        params: list = []
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            sql += f" WHERE c.document_id IN ({placeholders})"
            params.extend(doc_ids)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    corpus = [r["content"].lower().split() for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query.lower().split())

    ranked = sorted(
        zip(scores, rows), key=lambda x: x[0], reverse=True
    )[:top_k]

    return [
        {
            "chunk_id": row["id"],
            "document_id": row["document_id"],
            "content": row["content"],
            "chunk_index": row["chunk_index"],
            "filename": row["filename"],
            "score": round(float(score), 4),
            "source": "rfx_doc_bm25",
        }
        for score, row in ranked
        if score > 0
    ]
