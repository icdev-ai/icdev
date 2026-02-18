#!/usr/bin/env python3
"""Generate embeddings for memory entries that don't have them yet."""

import argparse
import sqlite3
import struct
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"


def get_openai_client():
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


def embedding_to_blob(embedding):
    return struct.pack(f"{len(embedding)}f", *embedding)


def embed_all():
    client = get_openai_client()
    if not client:
        return

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT id, content FROM memory_entries WHERE embedding IS NULL")
    rows = c.fetchall()

    if not rows:
        print("All entries already have embeddings.")
        conn.close()
        return

    print(f"Generating embeddings for {len(rows)} entries...")

    # Batch in groups of 20 to respect rate limits
    batch_size = 20
    total = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [row[1] for row in batch]
        ids = [row[0] for row in batch]

        try:
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
            print(f"  Embedded {total}/{len(rows)} entries...")

        except Exception as e:
            print(f"Error embedding batch starting at index {i}: {e}")
            break

    conn.close()
    print(f"Done. {total} entries embedded.")


def main():
    parser = argparse.ArgumentParser(description="Generate embeddings for memory entries")
    parser.add_argument("--all", action="store_true", help="Embed all entries missing embeddings")
    args = parser.parse_args()

    if args.all:
        embed_all()
    else:
        print("Use --all to embed all entries missing embeddings.")


if __name__ == "__main__":
    main()
