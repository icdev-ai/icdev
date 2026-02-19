#!/usr/bin/env python3
# CUI // SP-CTI
"""Hybrid search: combines BM25 keyword search + semantic vector search."""

import argparse
import sqlite3
import struct
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"


def get_all_entries():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT id, content, type, importance, embedding, created_at FROM memory_entries")
    rows = c.fetchall()
    conn.close()
    return rows


def bm25_search(query, entries):
    """BM25 keyword ranking with fallback to simple term frequency."""
    documents = [entry[1] for entry in entries]

    try:
        from rank_bm25 import BM25Okapi
        tokenized = [doc.lower().split() for doc in documents]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(query.lower().split())
    except ImportError:
        # Fallback: simple term frequency
        query_terms = query.lower().split()
        scores = []
        for doc in documents:
            doc_lower = doc.lower()
            score = sum(doc_lower.count(term) for term in query_terms)
            scores.append(float(score))

    # Normalize scores to 0-1
    max_score = max(scores) if scores and max(scores) > 0 else 1.0
    return [s / max_score for s in scores]


def semantic_search(query, entries):
    """Semantic similarity using embeddings (vendor-agnostic via LLM provider)."""
    # Try LLM provider system first (supports OpenAI, Ollama, Bedrock Titan)
    query_emb = None
    try:
        from tools.llm import get_embedding_provider
        provider = get_embedding_provider()
        query_emb = provider.embed(query)
    except Exception:
        pass

    # Fallback to direct OpenAI
    if query_emb is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        import os
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            response = client.embeddings.create(input=query, model="text-embedding-3-small")
            query_emb = response.data[0].embedding
        except (ImportError, Exception):
            return None

    if query_emb is None:
        return None

    scores = []
    for entry in entries:
        emb_blob = entry[4]
        if emb_blob is None:
            scores.append(0.0)
            continue
        n = len(emb_blob) // 4
        stored_emb = list(struct.unpack(f"{n}f", emb_blob))

        # Cosine similarity
        try:
            import numpy as np
            a, b = np.array(query_emb), np.array(stored_emb)
            score = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        except ImportError:
            dot = sum(x * y for x, y in zip(query_emb, stored_emb))
            norm_a = sum(x * x for x in query_emb) ** 0.5
            norm_b = sum(x * x for x in stored_emb) ** 0.5
            score = dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

        scores.append(score)

    # Normalize to 0-1
    max_score = max(scores) if scores and max(scores) > 0 else 1.0
    return [s / max_score for s in scores]


def hybrid_rank(entries, bm25_scores, semantic_scores, bm25_weight, semantic_weight):
    """Combine BM25 and semantic scores."""
    results = []
    for i, entry in enumerate(entries):
        bm25_s = bm25_scores[i] if bm25_scores else 0.0
        sem_s = semantic_scores[i] if semantic_scores else 0.0

        if semantic_scores is None:
            # No semantic available, use BM25 only
            combined = bm25_s
        else:
            combined = (bm25_weight * bm25_s) + (semantic_weight * sem_s)

        id_, content, type_, importance, _, created_at = entry
        results.append((combined, id_, content, type_, importance, created_at))

    results.sort(reverse=True, key=lambda x: x[0])
    return results


def main():
    parser = argparse.ArgumentParser(description="Hybrid search (BM25 + semantic)")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    parser.add_argument("--bm25-weight", type=float, default=0.7, help="BM25 weight (default 0.7)")
    parser.add_argument("--semantic-weight", type=float, default=0.3, help="Semantic weight (default 0.3)")
    args = parser.parse_args()

    entries = get_all_entries()
    if not entries:
        print("No memory entries found.")
        return

    bm25_scores = bm25_search(args.query, entries)
    semantic_scores = semantic_search(args.query, entries)

    if semantic_scores is None:
        print("(Semantic search unavailable — using keyword search only)")

    results = hybrid_rank(entries, bm25_scores, semantic_scores, args.bm25_weight, args.semantic_weight)

    # Log access
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT INTO memory_access_log (query, results_count, search_type) VALUES (?, ?, ?)",
        (args.query, min(args.limit, len(results)), "hybrid"),
    )
    conn.commit()
    conn.close()

    for score, id_, content, type_, importance, created_at in results[: args.limit]:
        if score > 0:
            print(f"[#{id_}] (score:{score:.3f}, {type_}, importance:{importance}) {content}  — {created_at}")


if __name__ == "__main__":
    main()
