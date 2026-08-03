"""Tests for FTS5 session history search (adapt-hermes-04)."""
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tests._sql_compat import connect as translating_connect


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def _load_migration_up():
    import importlib.util
    p = Path(__file__).resolve().parent.parent / "tools" / "db" / "migrations" / "184_memory_fts5" / "up.py"
    spec = importlib.util.spec_from_file_location("migration_184_up", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_up_down_importable():
    mod = _load_migration_up()
    assert callable(mod.up)
    assert callable(mod.down)


def test_migration_up_creates_fts5_on_sqlite():
    """migration 184: up() creates memory_fts on SQLite."""
    up_mod = _load_migration_up()

    conn = sqlite3.connect(":memory:")
    # Create a minimal memory_entries table
    conn.execute("""
        CREATE TABLE memory_entries (
            id TEXT PRIMARY KEY,
            type TEXT,
            content TEXT,
            tags TEXT,
            importance INTEGER,
            classification TEXT,
            created_at TEXT
        )
    """)
    conn.execute("INSERT INTO memory_entries VALUES ('id1','event','hello world','tag1',5,'CUI','2026-01-01')")
    conn.commit()

    up_mod.up(conn)

    rows = conn.execute("SELECT * FROM memory_fts WHERE memory_fts MATCH 'hello'").fetchall()
    assert len(rows) == 1
    conn.close()


def test_migration_up_noop_on_postgresql():
    """migration 184: up() skips gracefully on non-SQLite connections."""
    up_mod = _load_migration_up()

    class FakePGConn:
        def execute(self, sql, params=None):
            if "sqlite_version" in sql:
                raise Exception("unknown function sqlite_version")
            return self
        def fetchone(self): return None

    conn = FakePGConn()
    # Should not raise
    up_mod.up(conn)


# ---------------------------------------------------------------------------
# session_indexer
# ---------------------------------------------------------------------------

def test_session_indexer_importable():
    from tools.memory.session_indexer import index_session_turn, search_history, reindex_all
    assert callable(index_session_turn)
    assert callable(search_history)
    assert callable(reindex_all)


def _make_sqlite_db():
    """Return an in-memory SQLite conn with memory_entries + memory_fts.

    ``session_indexer`` authors PostgreSQL ``%s`` placeholders and swallows
    failures, so a bare sqlite3 connection makes ``search_history`` raise
    ``near "%": syntax error`` inside its except block and return ``[]`` —
    the test then asserts against a no-op it caused itself.
    """
    conn = translating_connect(":memory:")
    conn.execute("""
        CREATE TABLE memory_entries (
            id TEXT PRIMARY KEY,
            type TEXT,
            content TEXT,
            tags TEXT,
            importance INTEGER DEFAULT 5,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE memory_fts
            USING fts5(id UNINDEXED, content, type, tags)
    """)
    conn.commit()
    return conn


def test_search_history_fts5_returns_results():
    from tools.memory import session_indexer

    conn = _make_sqlite_db()
    conn.execute("INSERT INTO memory_entries VALUES ('x1','event','context compression rocks','ai',5,'CUI','2026-01-01')")
    conn.execute("INSERT INTO memory_fts(id,content,type,tags) VALUES ('x1','context compression rocks','event','ai')")
    conn.commit()

    with patch.object(session_indexer, "_get_conn", return_value=conn):
        results = session_indexer.search_history("context compression")

    assert len(results) >= 1
    assert any("compression" in r["content"] for r in results)
    assert results[0]["backend"] == "fts5"


def test_search_history_returns_empty_on_no_match():
    from tools.memory import session_indexer

    conn = _make_sqlite_db()
    conn.execute("INSERT INTO memory_entries VALUES ('x2','event','totally unrelated content','misc',5,'CUI','2026-01-01')")
    conn.execute("INSERT INTO memory_fts(id,content,type,tags) VALUES ('x2','totally unrelated content','event','misc')")
    conn.commit()

    with patch.object(session_indexer, "_get_conn", return_value=conn):
        results = session_indexer.search_history("context compression agents")

    assert results == []


def test_reindex_all_on_sqlite():
    from tools.memory import session_indexer

    conn = _make_sqlite_db()
    conn.execute("INSERT INTO memory_entries VALUES ('y1','session_user','agent context window','llm',5,'CUI','2026-01-01')")
    conn.execute("INSERT INTO memory_entries VALUES ('y2','session_assistant','compression saves tokens','llm',5,'CUI','2026-01-01')")
    conn.commit()

    with patch.object(session_indexer, "_get_conn", return_value=conn):
        result = session_indexer.reindex_all()

    assert result["indexed"] == 2
    assert result["backend"] == "fts5"


def test_reindex_all_returns_skipped_on_pg():
    from tools.memory import session_indexer

    class FakePGConn:
        def execute(self, sql, params=None):
            if "sqlite_version" in sql:
                raise Exception("pg does not have sqlite_version")
            return self
        def fetchone(self): return None
        def close(self): pass

    with patch.object(session_indexer, "_get_conn", return_value=FakePGConn()):
        result = session_indexer.reindex_all()

    assert result["skipped"] is not None
    assert result["indexed"] == 0


# ---------------------------------------------------------------------------
# fts5_search in hybrid_search.py
# ---------------------------------------------------------------------------

def test_fts5_search_in_hybrid_search_importable():
    from tools.memory.hybrid_search import fts5_search
    assert callable(fts5_search)


def test_fts5_search_short_query_returns_empty():
    from tools.memory.hybrid_search import fts5_search
    # Query with ≤2 tokens should skip FTS5 entirely
    results = fts5_search("AI")
    assert results == []

    results = fts5_search("AI agents")
    assert results == []


def test_fts5_search_long_query_delegates_to_session_indexer():
    from tools.memory import hybrid_search
    from tools.memory import session_indexer

    mock_results = [{"id": "z1", "content": "test content here", "type": "event",
                     "tags": "", "score": 1.5, "created_at": None, "backend": "fts5"}]

    with patch.object(session_indexer, "search_history", return_value=mock_results) as mock_sh:
        results = hybrid_search.fts5_search("context compression agents test")

    mock_sh.assert_called_once()
    assert results == mock_results


def test_fts5_search_graceful_on_import_error():
    from tools.memory import hybrid_search
    import sys

    # Simulate session_indexer import failure
    with patch.dict(sys.modules, {"tools.memory.session_indexer": None}):
        results = hybrid_search.fts5_search("context compression agents long query")

    assert isinstance(results, list)
