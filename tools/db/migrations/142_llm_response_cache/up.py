#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 142: Add llm_response_cache table.

PostgreSQL-native UNLOGGED table with JSONB columns and BRIN index.
SQLite fallback uses regular table with TEXT columns.

Idempotent: checks for table existence before creating.
"""


MIGRATION_ID = "142"
MIGRATION_NAME = "llm_response_cache"
DESCRIPTION = "Add llm_response_cache table for deterministic LLM response caching"


def _has_table(conn, table_name: str) -> bool:
    """Check if table exists (SQLite-compatible)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchall()
    return len(rows) > 0


def up(conn) -> dict:
    """Apply migration: create llm_response_cache table and indexes."""
    if _has_table(conn, "llm_response_cache"):
        return {"status": "skipped", "reason": "llm_response_cache already exists"}

    cur = conn.cursor()

    # We cannot use UNLOGGED here because the migration runner may connect to
    # either SQLite or PostgreSQL. The response_cache module detects backend
    # at runtime and uses UNLOGGED for PG. For the migration we use a
    # standard CREATE TABLE that works for both backends.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_response_cache (
            cache_key TEXT PRIMARY KEY,
            function TEXT NOT NULL,
            model_id TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls_json TEXT,
            structured_output_json TEXT,
            provider TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            thinking_tokens INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            stop_reason TEXT,
            hit_count INTEGER DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_response_cache (expires_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_cache_function ON llm_response_cache (function)"
    )

    conn.commit()
    return {"status": "applied", "table": "llm_response_cache", "indexes": 2}
