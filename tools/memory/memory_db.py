#!/usr/bin/env python3
# CUI // SP-CTI
"""Keyword search on memory database.

Supports user-scoped queries (D180) and JSON output.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import StorageConnection, get_connection
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Overridable in tests via monkeypatch
DB_PATH = None


def _connect():
    if DB_PATH is not None:
        # DB_PATH is a stand-in for get_connection(), which returns a
        # StorageConnection that rewrites PostgreSQL ``%s`` placeholders to
        # ``?`` for SQLite. Returning the bare sqlite3 connection made the seam
        # lie: every parameterised statement raised ``near "%": syntax error``,
        # and callers that swallow write failures reported a no-op as success.
        return StorageConnection(sqlite3.connect(str(DB_PATH)), "sqlite")
    return get_connection()


def search(query, limit=10, user_id=None, tenant_id=None):
    conn = _connect()
    c = conn.cursor()

    sql = "SELECT id, content, type, importance, created_at FROM memory_entries WHERE content LIKE ?"
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
        "INSERT INTO memory_access_log (query, results_count, search_type) VALUES (%s, %s, %s)",
        (query, len(results), "keyword"),
    )
    conn.commit()
    conn.close()
    return results


def list_all(limit=20, user_id=None, tenant_id=None):
    conn = _connect()
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
        entries.append(
            {
                "id": id_,
                "content": content,
                "type": type_,
                "importance": importance,
                "created_at": created_at,
            }
        )
    print(
        json.dumps(
            {
                "classification": "CUI // SP-CTI",
                "count": len(entries),
                "entries": entries,
            },
            indent=2,
        )
    )


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
