#!/usr/bin/env python3
# CUI // SP-CTI
"""Keyword search on memory database."""

import argparse
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"


def search(query, limit=10):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    # Simple keyword search using LIKE
    search_term = f"%{query}%"
    c.execute(
        """SELECT id, content, type, importance, created_at
           FROM memory_entries
           WHERE content LIKE ?
           ORDER BY importance DESC, created_at DESC
           LIMIT ?""",
        (search_term, limit),
    )
    results = c.fetchall()

    # Log the access
    c.execute(
        "INSERT INTO memory_access_log (query, results_count, search_type) VALUES (?, ?, ?)",
        (query, len(results), "keyword"),
    )
    conn.commit()
    conn.close()
    return results


def list_all(limit=20):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        """SELECT id, content, type, importance, created_at
           FROM memory_entries
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    )
    results = c.fetchall()
    conn.close()
    return results


def format_results(results):
    if not results:
        print("No results found.")
        return
    for id_, content, type_, importance, created_at in results:
        print(f"[#{id_}] ({type_}, importance:{importance}) {content}  — {created_at}")


def main():
    parser = argparse.ArgumentParser(description="Memory database operations")
    parser.add_argument(
        "--action",
        choices=["search", "list"],
        required=True,
        help="Action to perform",
    )
    parser.add_argument("--query", help="Search query (required for search)")
    parser.add_argument("--limit", type=int, default=10, help="Max results")
    args = parser.parse_args()

    if args.action == "search":
        if not args.query:
            print("Error: --query required for search action")
            return
        results = search(args.query, args.limit)
        format_results(results)
    elif args.action == "list":
        results = list_all(args.limit)
        format_results(results)


if __name__ == "__main__":
    main()
