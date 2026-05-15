#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 152 — Memory Classification Tagging and Retrieval Filtering.

Adds classification and compartment columns to memory_entries for
security-context-aware read/search filtering. Backfills existing rows.
Also back-adds decay_weight which was referenced by memory_write.py
but never included in the base schema.
"""
from __future__ import annotations

MIGRATION_ID = "152"
MIGRATION_NAME = "memory_classification"
DESCRIPTION = (
    "Add classification, compartment, and decay_weight columns to memory_entries; "
    "backfill classification='CUI', compartment='', decay_weight=1.0."
)


def _is_postgresql(conn):
    try:
        cur = conn.execute("SELECT version()")
        return "postgresql" in cur.fetchone()[0].lower()
    except Exception:
        return False


def _table_exists(conn, table_name):
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row[0] > 0
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=%s",
                (table_name,),
            ).fetchone()
            return row[0] > 0
        except Exception:
            return False


def _column_exists(conn, table_name, column_name):
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(r[1] == column_name for r in rows)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                (table_name, column_name),
            ).fetchone()
            return row[0] > 0
        except Exception:
            return False


def _add_column_safe(conn, table, column, ddl):
    if _column_exists(conn, table, column):
        print(f"[migration-{MIGRATION_ID}] {table}.{column} already exists — skip")
        return True
    try:
        if _is_postgresql(conn):
            conn.execute("SAVEPOINT add_col")
        conn.execute(ddl)
        if _is_postgresql(conn):
            conn.execute("RELEASE SAVEPOINT add_col")
        print(f"[migration-{MIGRATION_ID}] {table}: added {column}")
        return True
    except Exception as exc:
        if _is_postgresql(conn):
            try:
                conn.execute("ROLLBACK TO SAVEPOINT add_col")
            except Exception:
                pass
        print(f"[migration-{MIGRATION_ID}] WARN: {table}: failed to add {column}: {exc}")
        return False


def up(conn=None) -> None:
    from tools.db.storage import get_connection

    c = conn or get_connection()
    is_pg = _is_postgresql(c)
    if is_pg:
        c.execute("SET LOCAL lock_timeout = '10s'")

    stats = {"added": 0, "skipped": 0, "errors": 0}

    if _table_exists(c, "memory_entries"):
        columns = [
            ("decay_weight", "ALTER TABLE memory_entries ADD COLUMN decay_weight REAL DEFAULT 1.0"),
            ("classification", "ALTER TABLE memory_entries ADD COLUMN classification TEXT DEFAULT 'CUI'"),
            ("compartment", "ALTER TABLE memory_entries ADD COLUMN compartment TEXT DEFAULT ''"),
        ]
        for col, ddl in columns:
            if _column_exists(c, "memory_entries", col):
                stats["skipped"] += 1
                continue
            ok = _add_column_safe(c, "memory_entries", col, ddl)
            if ok:
                stats["added"] += 1
            else:
                stats["errors"] += 1

        # Backfill existing rows
        if _column_exists(c, "memory_entries", "classification"):
            try:
                c.execute(
                    "UPDATE memory_entries SET classification = 'CUI', compartment = '', decay_weight = 1.0 "
                    "WHERE classification IS NULL"
                )
                print(f"[migration-{MIGRATION_ID}] backfilled existing rows")
            except Exception as exc:
                print(f"[migration-{MIGRATION_ID}] WARN: backfill failed: {exc}")

        # Add indexes for filtering performance
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_memory_classification ON memory_entries(classification)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memory_compartment ON memory_entries(compartment)")
            print(f"[migration-{MIGRATION_ID}] indexes created")
        except Exception as exc:
            print(f"[migration-{MIGRATION_ID}] WARN: index creation failed: {exc}")
    else:
        print(f"[migration-{MIGRATION_ID}] memory_entries does not exist — skip")

    if conn is None:
        c.commit()
        c.close()

    print(
        f"[migration-{MIGRATION_ID}] done: added={stats['added']}, skipped={stats['skipped']}, errors={stats['errors']}"
    )


def run(conn=None) -> None:
    up(conn)


if __name__ == "__main__":
    up()
