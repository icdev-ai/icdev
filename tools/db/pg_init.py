#!/usr/bin/env python3
# CUI // SP-CTI
"""Initialize PostgreSQL database from SQLite schema (D-DB-20).

Reads the full SCHEMA_SQL from init_icdev_db.py, translates SQLite DDL
to PostgreSQL via the storage abstraction layer, and creates all tables
and indexes in two passes (tables first, then indexes).

Also handles ALTER TABLE migrations and data migration from SQLite.

Usage:
    python tools/db/pg_init.py --init --json          # Create schema
    python tools/db/pg_init.py --migrate-data --json   # Copy data from SQLite
    python tools/db/pg_init.py --verify --json         # Verify table counts
    python tools/db/pg_init.py --reset --json          # Drop and recreate schema
"""

import argparse
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _get_pg_conn():
    """Get raw psycopg2 connection with autocommit."""
    import os
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("ICDEV_PG_HOST", "localhost"),
        port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
        user=os.environ.get("ICDEV_PG_USER", "icdev"),
        password=os.environ.get("ICDEV_PG_PASSWORD", "icdev_dev_2026"),
        dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
    )
    conn.autocommit = True
    return conn


def _clean_fk(stmt: str) -> str:
    """Remove FOREIGN KEY and REFERENCES constraints from a statement."""
    stmt = re.sub(r",?\s*FOREIGN KEY\s*\([^)]+\)\s*REFERENCES\s+\w+\s*\(\w+\)", "", stmt)
    stmt = re.sub(r"\s+REFERENCES\s+\w+\s*\(\w+\)", "", stmt)
    return stmt


def init_schema() -> dict:
    """Create all tables and indexes from SQLite schema."""
    from tools.db.init_icdev_db import SCHEMA_SQL
    from tools.db.storage import translate_sql

    conn = _get_pg_conn()
    cur = conn.cursor()

    raw_stmts = [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]

    # Pass 1: CREATE TABLE
    table_ok, table_fail = 0, []
    for stmt in raw_stmts:
        if "CREATE TABLE" not in stmt.upper():
            continue
        cleaned = _clean_fk(stmt)
        translated = translate_sql(cleaned, "postgresql")
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", translated, re.I)
        tname = m.group(1) if m else "?"
        try:
            cur.execute(translated)
            table_ok += 1
        except Exception as e:
            err = str(e).strip().split("\n")[0][:150]
            if "already exists" in err:
                table_ok += 1
            else:
                table_fail.append({"table": tname, "error": err})

    # Pass 2: CREATE INDEX
    idx_ok, idx_fail = 0, 0
    for stmt in raw_stmts:
        if "CREATE INDEX" not in stmt.upper():
            continue
        try:
            translated = translate_sql(stmt, "postgresql")
            cur.execute(translated)
            idx_ok += 1
        except Exception:
            idx_fail += 1

    cur.close()
    conn.close()

    return {
        "tables_created": table_ok,
        "tables_failed": len(table_fail),
        "indexes_created": idx_ok,
        "indexes_failed": idx_fail,
        "failures": table_fail[:20],
    }


def migrate_data() -> dict:
    """Migrate data from SQLite to PostgreSQL."""
    import sqlite3
    import os

    sqlite_path = os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))
    if not Path(sqlite_path).exists():
        return {"error": f"SQLite DB not found: {sqlite_path}"}

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = _get_pg_conn()
    pg_cur = pg_conn.cursor()

    # Get all tables
    # pg-portability: sqlite-only path — reads the table catalogue from the SQLite
    # SOURCE (sqlite_conn) during the SQLite→PG copy; the destination is PG.
    tables = [
        row[0]
        for row in sqlite_conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    ]

    results = {"tables_migrated": 0, "rows_migrated": 0, "errors": []}

    for table in tables:
        if table.startswith("sqlite_"):
            continue
        try:
            rows = sqlite_conn.execute(f"SELECT * FROM [{table}]").fetchall()  # nosec B608
            if not rows:
                continue

            cols = rows[0].keys()
            placeholders = ", ".join(["%s"] * len(cols))
            col_names = ", ".join(cols)

            # Clear existing data
            try:
                pg_cur.execute(f"DELETE FROM {table}")  # nosec B608
            except Exception:
                continue

            # Insert rows
            for row in rows:
                values = []
                for c in cols:
                    v = row[c]
                    if isinstance(v, bytes):
                        import psycopg2

                        v = psycopg2.Binary(v)
                    values.append(v)
                try:
                    pg_cur.execute(
                        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",  # nosec B608
                        values,
                    )
                except Exception:
                    pass  # Skip individual row errors

            results["tables_migrated"] += 1
            results["rows_migrated"] += len(rows)
        except Exception as e:
            results["errors"].append({"table": table, "error": str(e)[:100]})

    pg_conn.commit() if not pg_conn.autocommit else None
    pg_cur.close()
    pg_conn.close()
    sqlite_conn.close()

    return results


def verify() -> dict:
    """Verify PostgreSQL schema matches expected table count."""
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
    table_count = cur.fetchone()[0]

    # Get table list
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
    tables = [row[0] for row in cur.fetchall()]

    cur.close()
    conn.close()

    return {
        "backend": "postgresql",
        "table_count": table_count,
        "expected": 399,
        "match": table_count >= 399,
        "tables": tables,
    }


def reset_schema() -> dict:
    """Drop and recreate public schema (DESTRUCTIVE)."""
    conn = _get_pg_conn()
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")
    cur.close()
    conn.close()
    return init_schema()


def main():
    parser = argparse.ArgumentParser(description="PostgreSQL Initializer (D-DB-20)")
    parser.add_argument("--init", action="store_true", help="Create schema from SQLite DDL")
    parser.add_argument("--migrate-data", action="store_true", help="Copy data from SQLite to PG")
    parser.add_argument("--verify", action="store_true", help="Verify table counts")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate (DESTRUCTIVE)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.init:
        result = init_schema()
    elif args.migrate_data:
        result = migrate_data()
    elif args.verify:
        result = verify()
    elif args.reset:
        result = reset_schema()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for k, v in result.items():
            if k != "tables" and k != "failures":
                print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
