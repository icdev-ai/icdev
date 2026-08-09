#!/usr/bin/env python3
# CUI // SP-CTI
"""Semantic (vector) search on memory entries. Requires OpenAI API key for embeddings."""

import argparse
import struct
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def get_openai_client():
    """Get an embedding client (tries LLM provider system first, then direct OpenAI)."""
    try:
        from tools.llm import get_embedding_provider

        return get_embedding_provider()
    except Exception:
        pass
    # Fallback to direct OpenAI
    try:
        from dotenv import load_dotenv

        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    import os

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY not set. Add it to .env or environment.")
        return None

    try:
        import openai

        return openai.OpenAI(api_key=api_key)
    except ImportError:
        print("Error: openai package not installed. Run: pip install openai")
        return None


def get_embedding(client, text, model="text-embedding-3-small"):
    """Generate embedding using LLM provider or direct OpenAI client."""
    # If client is an EmbeddingProvider from tools.llm, use its interface
    if hasattr(client, "embed"):
        return client.embed(text)
    # Otherwise it's a direct OpenAI client
    response = client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def cosine_similarity(a, b):
    try:
        import numpy as np

        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    except ImportError:
        # Pure Python fallback
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


def blob_to_embedding(blob):
    """Convert BLOB back to list of floats."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def embedding_to_blob(embedding):
    """Convert list of floats to BLOB for storage."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def search(query, limit=10, user_id=None, tenant_id=None):
    client = get_openai_client()
    if not client:
        return []

    query_embedding = get_embedding(client, query)

    conn = get_connection()
    c = conn.cursor()

    sql = "SELECT id, content, type, importance, embedding, created_at FROM memory_entries WHERE embedding IS NOT NULL"
    params = []
    if user_id:
        sql += " AND (user_id = ? OR user_id IS NULL)"
        params.append(user_id)
    if tenant_id:
        sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
        params.append(tenant_id)
    c.execute(sql, params)
    rows = c.fetchall()

    results = []
    for id_, content, type_, importance, emb_blob, created_at in rows:
        stored_embedding = blob_to_embedding(emb_blob)
        score = cosine_similarity(query_embedding, stored_embedding)
        results.append((score, id_, content, type_, importance, created_at))

    results.sort(reverse=True, key=lambda x: x[0])

    # Log access
    c.execute(
        "INSERT INTO memory_access_log (query, results_count, search_type) VALUES (%s, %s, %s)",
        (query, min(limit, len(results)), "semantic"),
    )
    conn.commit()
    conn.close()

    return results[:limit]


def main():
    parser = argparse.ArgumentParser(description="Semantic search on memory")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    parser.add_argument("--user-id", help="Filter by user ID (D180)")
    parser.add_argument("--tenant-id", help="Filter by tenant ID (D180)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    results = search(args.query, args.limit, user_id=args.user_id, tenant_id=args.tenant_id)
    if not results:
        print("No results (entries may need embeddings — run embed_memory.py --all)")
        return

    for score, id_, content, type_, importance, created_at in results:
        print(f"[#{id_}] (score:{score:.3f}, {type_}, importance:{importance}) {content}  — {created_at}")


if __name__ == "__main__":
    main()
