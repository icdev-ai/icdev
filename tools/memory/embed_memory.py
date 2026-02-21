#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate embeddings for memory entries that don't have them yet.

Uses vendor-agnostic LLM provider abstraction (D72) with OpenAI fallback.
"""

import argparse
import json
import sqlite3
import struct
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"


def get_embedding_client():
    """Get embedding client (D72: LLM provider first, OpenAI fallback)."""
    # Try LLM provider abstraction first
    try:
        sys.path.insert(0, str(BASE_DIR))
        from tools.llm import get_embedding_provider
        return get_embedding_provider(), "llm_provider"
    except Exception:
        pass

    # Fallback to direct OpenAI (backward compat)
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    import os
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: No embedding provider available. Set OPENAI_API_KEY or configure LLM provider.")
        return None, None

    try:
        import openai
        return openai.OpenAI(api_key=api_key), "openai_direct"
    except ImportError:
        print("Error: openai package not installed. Run: pip install openai")
        return None, None


def embedding_to_blob(embedding):
    return struct.pack(f"{len(embedding)}f", *embedding)


def embed_all(user_id=None, json_output=False):
    client, provider_name = get_embedding_client()
    if not client:
        if json_output:
            print(json.dumps({"error": "no_provider", "embedded": 0}))
        return

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    sql = "SELECT id, content FROM memory_entries WHERE embedding IS NULL"
    params = []
    if user_id:
        sql += " AND user_id = ?"
        params.append(user_id)

    c.execute(sql, params)
    rows = c.fetchall()

    if not rows:
        if json_output:
            print(json.dumps({"classification": "CUI // SP-CTI", "embedded": 0,
                              "status": "all_embedded", "provider": provider_name}))
        else:
            print("All entries already have embeddings.")
        conn.close()
        return

    if not json_output:
        print(f"Generating embeddings for {len(rows)} entries (provider: {provider_name})...")

    # Batch in groups of 20
    batch_size = 20
    total = 0
    errors = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [row[1] for row in batch]
        ids = [row[0] for row in batch]

        try:
            if hasattr(client, "embed"):
                # LLM provider interface (D72)
                for j, text in enumerate(texts):
                    emb = client.embed(text)
                    blob = embedding_to_blob(emb)
                    c.execute(
                        "UPDATE memory_entries SET embedding = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (blob, ids[j]),
                    )
                    total += 1
            else:
                # Direct OpenAI client (fallback)
                response = client.embeddings.create(
                    input=texts, model="text-embedding-3-small"
                )
                for j, emb_data in enumerate(response.data):
                    blob = embedding_to_blob(emb_data.embedding)
                    c.execute(
                        "UPDATE memory_entries SET embedding = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (blob, ids[j]),
                    )
                    total += 1

            conn.commit()
            if not json_output:
                print(f"  Embedded {total}/{len(rows)} entries...")

        except Exception as e:
            errors += 1
            if not json_output:
                print(f"Error embedding batch starting at index {i}: {e}")
            break

    conn.close()

    if json_output:
        print(json.dumps({
            "classification": "CUI // SP-CTI",
            "embedded": total,
            "errors": errors,
            "total_unembedded": len(rows),
            "provider": provider_name,
        }, indent=2))
    else:
        print(f"Done. {total} entries embedded via {provider_name}.")


def main():
    parser = argparse.ArgumentParser(description="Generate embeddings for memory entries")
    parser.add_argument("--all", action="store_true", help="Embed all entries missing embeddings")
    parser.add_argument("--user-id", help="Only embed entries for this user (D180)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.all:
        embed_all(user_id=args.user_id, json_output=args.json)
    else:
        print("Use --all to embed all entries missing embeddings.")


if __name__ == "__main__":
    main()
