#!/usr/bin/env python3
# CUI // SP-CTI
"""Auto-capture memory from hook events and tool usage (D181-D182).

Writes to memory_buffer table for async flush to memory_entries.
Can be invoked from hook handlers or CLI.

Usage:
    python tools/memory/auto_capture.py --content "..." --source hook --tool-name scaffold --json
    python tools/memory/auto_capture.py --flush --json
    python tools/memory/auto_capture.py --buffer-status --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "memory.db"

VALID_SOURCES = ("hook", "manual", "thinking", "auto")
VALID_TYPES = ("fact", "preference", "event", "insight", "task", "relationship", "thinking")


def _load_config():
    """Load auto-capture settings from memory_config.yaml."""
    config_path = BASE_DIR / "args" / "memory_config.yaml"
    defaults = {
        "buffer_flush_threshold": 100,
        "default_importance": 3,
        "max_content_length": 2000,
    }
    try:
        import yaml
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            ac = data.get("auto_capture", {})
            for key in defaults:
                if key in ac:
                    defaults[key] = ac[key]
    except (ImportError, Exception):
        pass
    return defaults


def compute_content_hash(content):
    """SHA-256 hash of content for deduplication (D179)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_connection(db_path=None):
    """Get a DB connection with WAL mode for concurrent safety."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_buffer_table(conn):
    """Create memory_buffer table if it doesn't exist."""
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


def capture(
    content,
    source="hook",
    memory_type="event",
    importance=3,
    user_id=None,
    tenant_id=None,
    session_id=None,
    tool_name=None,
    metadata=None,
    db_path=None,
):
    """Capture content into the memory buffer.

    Returns:
        dict with status, buffer_id, and auto_flushed flag.
    """
    cfg = _load_config()
    max_len = cfg.get("max_content_length", 2000)
    if len(content) > max_len:
        content = content[:max_len]

    content_hash = compute_content_hash(content)
    conn = _get_connection(db_path)
    _ensure_buffer_table(conn)

    try:
        # Dedup within buffer
        existing = conn.execute(
            "SELECT id FROM memory_buffer WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            conn.close()
            return {"status": "duplicate", "buffer_id": existing["id"]}

        conn.execute(
            """INSERT INTO memory_buffer
               (content, content_hash, type, importance, source,
                user_id, tenant_id, session_id, tool_name, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (content, content_hash, memory_type, importance, source,
             user_id, tenant_id, session_id, tool_name,
             json.dumps(metadata) if metadata else None),
        )
        conn.commit()
        buffer_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Check buffer size for auto-flush threshold
        count = conn.execute("SELECT COUNT(*) FROM memory_buffer").fetchone()[0]
        conn.close()

        result = {
            "status": "captured",
            "buffer_id": buffer_id,
            "content_hash": content_hash[:16],
            "buffer_size": count,
        }

        threshold = cfg.get("buffer_flush_threshold", 100)
        if count >= threshold:
            flush_result = flush_buffer(db_path=db_path)
            result["auto_flushed"] = True
            result["flush_result"] = flush_result

        return result

    except Exception as exc:
        conn.close()
        return {"status": "error", "error": str(exc)}


def flush_buffer(db_path=None):
    """Flush buffer entries to memory_entries with dedup check.

    Returns:
        dict with flushed, duplicates, and errors counts.
    """
    conn = _get_connection(db_path)
    _ensure_buffer_table(conn)

    try:
        rows = conn.execute(
            "SELECT id, content, content_hash, type, importance, source, "
            "user_id, tenant_id, session_id, tool_name, metadata, created_at "
            "FROM memory_buffer ORDER BY created_at ASC"
        ).fetchall()

        if not rows:
            conn.close()
            return {"flushed": 0, "duplicates": 0, "errors": 0}

        flushed = 0
        duplicates = 0
        errors = 0
        flushed_ids = []

        for row in rows:
            try:
                # Check dedup against memory_entries
                if row["user_id"]:
                    existing = conn.execute(
                        "SELECT id FROM memory_entries WHERE content_hash = ? AND user_id = ?",
                        (row["content_hash"], row["user_id"]),
                    ).fetchone()
                else:
                    existing = conn.execute(
                        "SELECT id FROM memory_entries WHERE content_hash = ? AND user_id IS NULL",
                        (row["content_hash"],),
                    ).fetchone()

                if existing:
                    duplicates += 1
                    flushed_ids.append(row["id"])
                    continue

                conn.execute(
                    """INSERT INTO memory_entries
                       (content, type, importance, content_hash,
                        user_id, tenant_id, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (row["content"], row["type"], row["importance"],
                     row["content_hash"], row["user_id"], row["tenant_id"],
                     row["source"]),
                )
                flushed += 1
                flushed_ids.append(row["id"])

            except Exception:
                errors += 1

        # Remove flushed entries from buffer
        if flushed_ids:
            placeholders = ",".join("?" * len(flushed_ids))
            conn.execute(
                f"DELETE FROM memory_buffer WHERE id IN ({placeholders})",
                flushed_ids,
            )

        conn.commit()
        conn.close()

        return {
            "flushed": flushed,
            "duplicates": duplicates,
            "errors": errors,
            "total_processed": len(rows),
        }

    except Exception as exc:
        conn.close()
        return {"flushed": 0, "errors": 1, "error": str(exc)}


def buffer_status(db_path=None):
    """Return buffer statistics."""
    cfg = _load_config()
    conn = _get_connection(db_path)
    _ensure_buffer_table(conn)

    try:
        total = conn.execute("SELECT COUNT(*) FROM memory_buffer").fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM memory_buffer GROUP BY source"
        ).fetchall()
        oldest = conn.execute(
            "SELECT MIN(created_at) FROM memory_buffer"
        ).fetchone()[0]
        conn.close()

        return {
            "classification": "CUI // SP-CTI",
            "total_buffered": total,
            "by_source": {row["source"]: row["cnt"] for row in by_source},
            "oldest_entry": oldest,
            "flush_threshold": cfg.get("buffer_flush_threshold", 100),
        }
    except Exception as exc:
        conn.close()
        return {"total_buffered": 0, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(
        description="Auto-capture memory from hooks/tools (D181)"
    )
    parser.add_argument("--content", help="Content to capture")
    parser.add_argument("--source", choices=VALID_SOURCES, default="hook",
                        help="Capture source")
    parser.add_argument("--type", choices=VALID_TYPES, default="event",
                        dest="memory_type", help="Memory type")
    parser.add_argument("--importance", type=int, default=3, help="Importance 1-10")
    parser.add_argument("--user-id", help="User ID (D180)")
    parser.add_argument("--tenant-id", help="Tenant ID (D180)")
    parser.add_argument("--session-id", help="Session/correlation ID")
    parser.add_argument("--tool-name", help="Tool name for context")
    parser.add_argument("--flush", action="store_true", help="Flush buffer to memory_entries")
    parser.add_argument("--buffer-status", action="store_true", help="Show buffer stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.buffer_status:
        result = buffer_status()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Buffered: {result['total_buffered']} entries")
            for src, cnt in result.get("by_source", {}).items():
                print(f"  {src}: {cnt}")
            if result.get("oldest_entry"):
                print(f"  Oldest: {result['oldest_entry']}")
            print(f"  Flush threshold: {result['flush_threshold']}")
        return

    if args.flush:
        result = flush_buffer()
        if args.json:
            print(json.dumps({"classification": "CUI // SP-CTI", **result}, indent=2))
        else:
            print(f"Flushed {result['flushed']} entries "
                  f"({result['duplicates']} duplicates, {result['errors']} errors)")
        return

    if not args.content:
        print("Error: --content required (or use --flush / --buffer-status)")
        sys.exit(1)

    result = capture(
        content=args.content,
        source=args.source,
        memory_type=args.memory_type,
        importance=args.importance,
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        session_id=args.session_id,
        tool_name=args.tool_name,
    )
    if args.json:
        print(json.dumps({"classification": "CUI // SP-CTI", **result}, indent=2))
    else:
        if result["status"] == "captured":
            print(f"Captured to buffer (id: {result['buffer_id']}, "
                  f"buffer size: {result['buffer_size']})")
            if result.get("auto_flushed"):
                fr = result["flush_result"]
                print(f"  Auto-flushed: {fr['flushed']} entries")
        elif result["status"] == "duplicate":
            print(f"Duplicate (existing buffer id: {result['buffer_id']})")
        else:
            print(f"Error: {result.get('error', 'unknown')}")


if __name__ == "__main__":
    main()
