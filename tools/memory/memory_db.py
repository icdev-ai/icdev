#!/usr/bin/env python3
# CUI // SP-CTI
"""Keyword search on memory database.

Supports user-scoped queries (D180) and JSON output.
"""

import argparse
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"


def search(query, limit=10, user_id=None, tenant_id=None):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    sql = ("SELECT id, content, type, importance, created_at "
           "FROM memory_entries WHERE content LIKE ?")
    params = [f"%{query}%"]

    if user_id:
        sql += " AND (user_id = ? OR user_id IS NULL)"
        params.append(user_id)
    if tenant_id:
        sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
        params.append(tenant_id)

    sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
    params.append(limit)

    c.execute(sql, params)
    results = c.fetchall()

    # Log the access
    c.execute(
        "INSERT INTO memory_access_log (query, results_count, search_type) VALUES (?, ?, ?)",
        (query, len(results), "keyword"),
    )
    conn.commit()
    conn.close()
    return results


def list_all(limit=20, user_id=None, tenant_id=None):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    sql = "SELECT id, content, type, importance, created_at FROM memory_entries WHERE 1=1"
    params = []

    if user_id:
        sql += " AND (user_id = ? OR user_id IS NULL)"
        params.append(user_id)
    if tenant_id:
        sql += " AND (tenant_id = ? OR tenant_id IS NULL)"
        params.append(tenant_id)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    c.execute(sql, params)
    results = c.fetchall()
    conn.close()
    return results


def format_results(results):
    if not results:
        print("No results found.")
        return
    for id_, content, type_, importance, created_at in results:
        print(f"[#{id_}] ({type_}, importance:{importance}) {content}  — {created_at}")


def format_json(results):
    entries = []
    for id_, content, type_, importance, created_at in results:
        entries.append({
            "id": id_,
            "content": content,
            "type": type_,
            "importance": importance,
            "created_at": created_at,
        })
    print(json.dumps({
        "classification": "CUI // SP-CTI",
        "count": len(entries),
        "entries": entries,
    }, indent=2))


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
    parser.add_argument("--user-id", help="Filter by user ID (D180)")
    parser.add_argument("--tenant-id", help="Filter by tenant ID (D180)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.action == "search":
        if not args.query:
            if args.json:
                print(json.dumps({"error": "--query required for search action"}))
            else:
                print("Error: --query required for search action")
            return
        results = search(args.query, args.limit, user_id=args.user_id, tenant_id=args.tenant_id)
        if args.json:
            format_json(results)
        else:
            format_results(results)
    elif args.action == "list":
        results = list_all(args.limit, user_id=args.user_id, tenant_id=args.tenant_id)
        if args.json:
            format_json(results)
        else:
            format_results(results)


if __name__ == "__main__":
    main()
