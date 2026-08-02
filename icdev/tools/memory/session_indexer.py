#!/usr/bin/env python3
# CUI // SP-CTI
"""FTS5-backed session history indexer (adapt-hermes-02).

Provides:
  - index_session_turn(): write a session turn to memory_entries + refresh FTS5
  - search_history(): FTS5 full-text search over session history
  - reindex_all(): rebuild the memory_fts FTS5 table from memory_entries

FTS5 is SQLite-only. On PostgreSQL deployments search_history() falls back to
an ILIKE query against memory_entries.content so the interface is always usable.

Usage (standalone):
    python tools/memory/session_indexer.py --search "context compression" --json
    python tools/memory/session_indexer.py --reindex --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from tools.logging.icdev_logger import get_logger
import sys
from datetime import datetime, timezone

logger = get_logger(__name__)

BASE_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn():
    from tools.db.storage import get_connection
    return get_connection()


def _is_sqlite(conn) -> bool:
    try:
        conn.execute("SELECT sqlite_version()")
        return True
    except Exception:
        return False


def _fts5_available(conn) -> bool:
    if not _is_sqlite(conn):
        return False
    try:
        conn.execute("SELECT * FROM memory_fts WHERE memory_fts MATCH 'test' LIMIT 1")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Index a single session turn
# ---------------------------------------------------------------------------

def index_session_turn(
    session_id: str,
    role: str,
    content: str,
    *,
    importance: int = 5,
    tags: str = "",
    classification: str = "CUI",
) -> str:
    """Write a session turn to memory_entries and refresh the FTS5 index.

    Args:
        session_id: Identifier for the conversation session.
        role: "user", "assistant", or "system".
        content: Text content of the turn.
        importance: 1–10 relevance score.
        tags: Comma-separated tags for this turn.
        classification: CUI, SECRET, etc.

    Returns:
        The generated memory entry ID.
    """
    entry_id = hashlib.sha256(f"{session_id}:{role}:{content[:100]}:{_now_iso()}".encode()).hexdigest()[:20]
    entry_type = f"session_{role}"
    now = _now_iso()

    conn = _get_conn()
    try:
        conn.execute(
            # `topics`, not `tags` (swp-scan-01) — every indexed session turn
            # raised UndefinedColumn, so the session index was always empty.
            """INSERT INTO memory_entries
               (id, type, content, importance, topics, classification, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (entry_id, entry_type, content, importance, tags, classification, now),
        )
        conn.commit()

        # Refresh FTS5 if available (SQLite only)
        if _is_sqlite(conn) and _fts5_available(conn):
            conn.execute(
                "INSERT OR REPLACE INTO memory_fts(id, content, type, tags) VALUES (%s, %s, %s, %s)",
                (entry_id, content, entry_type, tags),
            )
            conn.commit()

    except Exception as exc:
        logger.warning("session_indexer: index_session_turn failed: %s", exc)
    finally:
        conn.close()

    return entry_id


# ---------------------------------------------------------------------------
# Search history
# ---------------------------------------------------------------------------

def search_history(query: str, limit: int = 20) -> list[dict]:
    """FTS5 full-text search over session history. Falls back to ILIKE on PG.

    Args:
        query: Search terms. Multi-word queries AND all tokens.
        limit: Maximum results to return.

    Returns:
        List of {id, content, type, tags, score, created_at} dicts,
        ranked by FTS5 relevance (SQLite) or recency (PostgreSQL fallback).
    """
    conn = _get_conn()
    results: list[dict] = []
    try:
        is_sqlite = _is_sqlite(conn)

        if is_sqlite and _fts5_available(conn):
            # FTS5 path — sanitize query (strip special FTS5 chars)
            safe_q = " ".join(
                w for w in query.split() if w.replace("-", "").replace("_", "").isalnum()
            )
            if not safe_q:
                safe_q = query[:50]
            rows = conn.execute(
                """SELECT id, content, type, tags,
                           bm25(memory_fts) AS score
                    FROM   memory_fts
                    WHERE  memory_fts MATCH %s
                    ORDER  BY score
                    LIMIT  %s""",
                (safe_q, limit),
            ).fetchall()
            for row in rows:
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "type": row[2],
                    "tags": row[3],
                    "score": abs(float(row[4])) if row[4] else 0.0,
                    "created_at": None,
                    "backend": "fts5",
                })

        else:
            # PostgreSQL / no FTS5 fallback: ILIKE substring search
            placeholder = "%s" if not is_sqlite else "?"
            like_val = f"%{query}%"
            rows = conn.execute(
                f"""SELECT id, content, type, tags, NULL AS score, created_at
                    FROM memory_entries
                    WHERE content ILIKE {placeholder}
                       OR tags ILIKE {placeholder}
                    ORDER BY created_at DESC
                    LIMIT {placeholder}""",
                (like_val, like_val, limit),
            ).fetchall()
            for row in rows:
                results.append({
                    "id": row[0] if hasattr(row, "__getitem__") else row[0],
                    "content": row[1] if hasattr(row, "__getitem__") else row[1],
                    "type": row[2] if hasattr(row, "__getitem__") else row[2],
                    "tags": row[3] if hasattr(row, "__getitem__") else row[3],
                    "score": 1.0,
                    "created_at": str(row[5]) if row[5] else None,
                    "backend": "ilike",
                })

    except Exception as exc:
        logger.warning("session_indexer: search_history failed: %s", exc)
    finally:
        conn.close()

    return results


# ---------------------------------------------------------------------------
# Reindex
# ---------------------------------------------------------------------------

def reindex_all() -> dict:
    """Rebuild memory_fts from scratch from memory_entries (SQLite only).

    Returns:
        {indexed: int, skipped: str|None, backend: str}
    """
    conn = _get_conn()
    try:
        if not _is_sqlite(conn):
            return {"indexed": 0, "skipped": "postgresql-no-fts5", "backend": "ilike"}

        if not _fts5_available(conn):
            return {"indexed": 0, "skipped": "fts5-unavailable", "backend": "none"}

        conn.execute("DELETE FROM memory_fts")
        conn.execute(
            """INSERT INTO memory_fts(id, content, type, tags)
               SELECT id, COALESCE(content, ''), COALESCE(type, ''), COALESCE(tags, '')
               FROM memory_entries"""
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        return {"indexed": count, "skipped": None, "backend": "fts5"}
    except Exception as exc:
        logger.warning("session_indexer: reindex_all failed: %s", exc)
        return {"indexed": 0, "skipped": str(exc), "backend": "error"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="ICDEV Session History Indexer (adapt-hermes-02)")
    parser.add_argument("--search", metavar="QUERY", help="FTS5 search over session history")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--reindex", action="store_true", help="Rebuild FTS5 index from memory_entries")
    parser.add_argument("--index-turn", metavar="CONTENT", help="Index a single turn")
    parser.add_argument("--session-id", default="cli", help="Session ID for --index-turn")
    parser.add_argument("--role", default="user", help="Role for --index-turn")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    if args.search:
        results = search_history(args.search, limit=args.limit)
        if args.as_json:
            print(json.dumps({"query": args.search, "count": len(results), "results": results}))
        else:
            for r in results:
                print(f"[{r['type']}] score={r['score']:.3f} {r['content'][:100]}")

    elif args.reindex:
        result = reindex_all()
        if args.as_json:
            print(json.dumps(result))
        else:
            print(f"Reindex complete: {result}")

    elif args.index_turn:
        entry_id = index_session_turn(args.session_id, args.role, args.index_turn)
        if args.as_json:
            print(json.dumps({"entry_id": entry_id}))
        else:
            print(f"Indexed: {entry_id}")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
