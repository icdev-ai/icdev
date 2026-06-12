#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 018: Consolidate memory.db and activity.db into icdev.db.

Creates memory_entries, daily_logs, memory_access_log, and activity_tasks in
icdev.db (the main get_connection() target) and migrates any existing rows from
the standalone SQLite files.

After this migration, all tools/memory/*.py use get_connection() with no
db_path overrides — a single connection point for all backends (SQLite + PG).
"""

import hashlib
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
MEMORY_DB = BASE_DIR / "data" / "memory.db"
ACTIVITY_DB = BASE_DIR / "data" / "activity.db"


def _compute_content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def up(conn):
    """Create memory/activity tables in icdev.db and migrate data."""

    # ------------------------------------------------------------------
    # 1. Create tables (idempotent)
    # ------------------------------------------------------------------
    conn.executescript("""
        -- Memory entries (core memory store)
        CREATE TABLE IF NOT EXISTS memory_entries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            content      TEXT NOT NULL,
            type         TEXT DEFAULT 'event',
            importance   INTEGER DEFAULT 5,
            embedding    BLOB,
            created_at   TEXT DEFAULT (datetime('now')),
            updated_at   TEXT DEFAULT (datetime('now')),
            content_hash TEXT,
            user_id      TEXT,
            tenant_id    TEXT,
            source       TEXT DEFAULT 'manual'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_content_hash_user
            ON memory_entries(content_hash, user_id);
        CREATE INDEX IF NOT EXISTS idx_memory_user_id ON memory_entries(user_id);
        CREATE INDEX IF NOT EXISTS idx_memory_tenant_id ON memory_entries(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at);

        -- Daily session logs
        CREATE TABLE IF NOT EXISTS daily_logs (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT,
            content    TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_daily_logs_date ON daily_logs(date);

        -- Memory search access log
        CREATE TABLE IF NOT EXISTS memory_access_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id      TEXT,
            query         TEXT,
            results_count INTEGER,
            search_type   TEXT,
            accessed_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mem_access_search
            ON memory_access_log(search_type);

        -- Activity task tracking (consolidated from activity.db)
        CREATE TABLE IF NOT EXISTS activity_tasks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            description  TEXT,
            status       TEXT DEFAULT 'pending'
                CHECK(status IN ('pending','in_progress','completed','cancelled')),
            priority     INTEGER DEFAULT 5,
            created_at   TEXT DEFAULT (datetime('now')),
            updated_at   TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_activity_tasks_status
            ON activity_tasks(status);

        -- Memory consolidation audit log (append-only, D152)
        CREATE TABLE IF NOT EXISTS memory_consolidation_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            action      TEXT NOT NULL,
            source_id   INTEGER,
            target_id   INTEGER,
            reason      TEXT,
            similarity  REAL,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mem_consolidation_created
            ON memory_consolidation_log(created_at);

        -- Auto-capture buffer for async processing (Phase 44, D181)
        CREATE TABLE IF NOT EXISTS memory_buffer (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            content      TEXT NOT NULL,
            content_hash TEXT,
            type         TEXT DEFAULT 'event',
            importance   INTEGER DEFAULT 5,
            source       TEXT,
            user_id      TEXT,
            tenant_id    TEXT,
            session_id   TEXT,
            tool_name    TEXT,
            metadata     TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_memory_buffer_created
            ON memory_buffer(created_at);
        CREATE INDEX IF NOT EXISTS idx_memory_buffer_hash
            ON memory_buffer(content_hash);
    """)

    # ------------------------------------------------------------------
    # 2. Backfill content_hash for any un-hashed memory_entries
    # ------------------------------------------------------------------
    cursor = conn.execute(
        "SELECT id, content FROM memory_entries WHERE content_hash IS NULL"
    )
    for row_id, content in cursor.fetchall():
        conn.execute(
            "UPDATE memory_entries SET content_hash = ? WHERE id = ?",
            (_compute_content_hash(content), row_id),
        )

    # ------------------------------------------------------------------
    # 3. Migrate rows from memory.db (if it exists and has data)
    # ------------------------------------------------------------------
    if MEMORY_DB.exists():
        src = sqlite3.connect(str(MEMORY_DB))  # sqlite3-ok — reading old memory.db source file for one-time migration
        src.row_factory = sqlite3.Row
        _migrate_memory_entries(src, conn)
        _migrate_daily_logs(src, conn)
        _migrate_memory_access_log(src, conn)
        src.close()

    # ------------------------------------------------------------------
    # 4. Migrate rows from activity.db (if it exists and has data)
    # ------------------------------------------------------------------
    if ACTIVITY_DB.exists():
        src = sqlite3.connect(str(ACTIVITY_DB))  # sqlite3-ok — reading old activity.db source file for one-time migration
        src.row_factory = sqlite3.Row
        _migrate_activity_tasks(src, conn)
        src.close()

    conn.commit()


def _migrate_memory_entries(src, dst):
    """Copy memory_entries rows, skipping duplicates by content_hash + user_id."""
    try:
        rows = src.execute(
            "SELECT content, type, importance, embedding, created_at, updated_at, "
            "content_hash, user_id, tenant_id, source "
            "FROM memory_entries"
        ).fetchall()
    except sqlite3.OperationalError:
        return  # Table doesn't exist in source

    for row in rows:
        content = row["content"]
        content_hash = row["content_hash"] or _compute_content_hash(content)
        user_id = row["user_id"]

        # Check for duplicate (unique on content_hash + user_id)
        exists = dst.execute(
            "SELECT 1 FROM memory_entries WHERE content_hash = ? AND (user_id = ? OR (user_id IS NULL AND ? IS NULL))",
            (content_hash, user_id, user_id),
        ).fetchone()
        if exists:
            continue

        dst.execute(
            "INSERT INTO memory_entries "
            "(content, type, importance, embedding, created_at, updated_at, "
            " content_hash, user_id, tenant_id, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                content,
                row["type"] or "event",
                row["importance"] if row["importance"] is not None else 5,
                row["embedding"],
                row["created_at"],
                row["updated_at"],
                content_hash,
                user_id,
                row["tenant_id"],
                row["source"] or "manual",
            ),
        )


def _migrate_daily_logs(src, dst):
    """Copy daily_logs rows (skip exact duplicates by date + content)."""
    try:
        rows = src.execute(
            "SELECT date, content, created_at FROM daily_logs"
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for row in rows:
        exists = dst.execute(
            "SELECT 1 FROM daily_logs WHERE date = ? AND content = ?",
            (row["date"], row["content"]),
        ).fetchone()
        if exists:
            continue
        dst.execute(
            "INSERT INTO daily_logs (date, content, created_at) VALUES (?, ?, ?)",
            (row["date"], row["content"], row["created_at"]),
        )


def _migrate_memory_access_log(src, dst):
    """Copy memory_access_log rows (no dedup — all are valid audit entries)."""
    try:
        rows = src.execute(
            "SELECT query, results_count, search_type, accessed_at FROM memory_access_log"
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for row in rows:
        dst.execute(
            "INSERT INTO memory_access_log (query, results_count, search_type, accessed_at) "
            "VALUES (?, ?, ?, ?)",
            (row["query"], row["results_count"], row["search_type"], row["accessed_at"]),
        )


def _migrate_activity_tasks(src, dst):
    """Copy tasks from activity.db into activity_tasks (skip empty tables)."""
    try:
        rows = src.execute(
            "SELECT title, description, status, priority, created_at, updated_at, completed_at "
            "FROM tasks"
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for row in rows:
        dst.execute(
            "INSERT INTO activity_tasks "
            "(title, description, status, priority, created_at, updated_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["title"],
                row["description"],
                row["status"] or "pending",
                row["priority"] if row["priority"] is not None else 5,
                row["created_at"],
                row["updated_at"],
                row["completed_at"],
            ),
        )


def down(conn):
    """Rollback: drop the tables added by this migration.

    Note: does NOT restore memory.db / activity.db data.
    """
    conn.execute("DROP TABLE IF EXISTS activity_tasks")
    # memory_entries, daily_logs, memory_access_log are not dropped to avoid
    # data loss — they may have been populated since the migration ran.
    conn.commit()
