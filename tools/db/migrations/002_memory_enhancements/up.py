#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 002: Memory enhancements — dedup, user scoping, buffer table.

Targets data/memory.db (not icdev.db).
Adds: content_hash (D179), user_id/tenant_id (D180), memory_buffer table (D181),
      source column, thinking type support (D182).
"""

import hashlib
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent


def _column_exists(conn, table, column):
    """Check if a column exists in a table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _table_exists(conn, table):
    """Check if a table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def _compute_content_hash(content):
    """SHA-256 hash of content string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def up(conn):
    """Apply memory enhancements to memory.db."""
    # 1. Add columns to memory_entries (idempotent)
    if _table_exists(conn, "memory_entries"):
        for col, col_def in [
            ("content_hash", "TEXT"),
            ("user_id", "TEXT"),
            ("tenant_id", "TEXT"),
            ("source", "TEXT DEFAULT 'manual'"),
        ]:
            if not _column_exists(conn, "memory_entries", col):
                try:
                    conn.execute(
                        f"ALTER TABLE memory_entries ADD COLUMN {col} {col_def}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists

        # 2. Create indexes
        for idx_sql in [
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_content_hash_user "
            "ON memory_entries(content_hash, user_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_user_id "
            "ON memory_entries(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_tenant_id "
            "ON memory_entries(tenant_id)",
        ]:
            try:
                conn.execute(idx_sql)
            except sqlite3.OperationalError:
                pass

        # 3. Backfill content_hash for existing entries
        cursor = conn.execute(
            "SELECT id, content FROM memory_entries WHERE content_hash IS NULL"
        )
        rows = cursor.fetchall()
        for row_id, content in rows:
            h = _compute_content_hash(content or "")
            conn.execute(
                "UPDATE memory_entries SET content_hash = ? WHERE id = ?",
                (h, row_id),
            )

    # 4. Create memory_buffer table (D181)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_buffer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            type TEXT DEFAULT 'event',
            importance INTEGER DEFAULT 3,
            source TEXT NOT NULL DEFAULT 'hook'
                CHECK(source IN ('hook', 'manual', 'thinking', 'auto')),
            user_id TEXT,
            tenant_id TEXT,
            session_id TEXT,
            tool_name TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_buffer_created
            ON memory_buffer(created_at);
        CREATE INDEX IF NOT EXISTS idx_buffer_source
            ON memory_buffer(source);
        CREATE INDEX IF NOT EXISTS idx_buffer_user
            ON memory_buffer(user_id);
    """)

    conn.commit()


def down(conn):
    """Rollback: drop buffer table, remove indexes.

    Note: SQLite cannot DROP COLUMN. Columns remain but indexes are removed.
    """
    conn.execute("DROP TABLE IF EXISTS memory_buffer")
    conn.execute("DROP INDEX IF EXISTS idx_memory_content_hash_user")
    conn.execute("DROP INDEX IF EXISTS idx_memory_user_id")
    conn.execute("DROP INDEX IF EXISTS idx_memory_tenant_id")
    conn.commit()
