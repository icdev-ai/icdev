#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for memory system enhancements: dedup (D179), user scoping (D180),
auto-capture buffer (D181), thinking type (D182), and D72 embed fix."""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_db(tmp_path):
    """Create a temporary memory.db with the full schema including enhancements."""
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT DEFAULT 'event',
            importance INTEGER DEFAULT 5,
            embedding BLOB,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            content_hash TEXT,
            user_id TEXT,
            tenant_id TEXT,
            source TEXT DEFAULT 'manual'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_content_hash_user
            ON memory_entries(content_hash, user_id);
        CREATE INDEX IF NOT EXISTS idx_memory_user_id
            ON memory_entries(user_id);

        CREATE TABLE daily_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE memory_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT,
            query TEXT,
            results_count INTEGER,
            search_type TEXT,
            accessed_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE memory_buffer (
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
    """)
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Feature 1: SHA-256 Content-Hash Deduplication (D179)
# ---------------------------------------------------------------------------

class TestContentHashDedup:
    def test_compute_content_hash_deterministic(self):
        from tools.memory.memory_write import compute_content_hash
        h1 = compute_content_hash("hello world")
        h2 = compute_content_hash("hello world")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex length

    def test_compute_content_hash_different_inputs(self):
        from tools.memory.memory_write import compute_content_hash
        h1 = compute_content_hash("hello")
        h2 = compute_content_hash("world")
        assert h1 != h2

    def test_write_with_dedup_first_write_succeeds(self, memory_db, monkeypatch):
        from tools.memory import memory_write
        monkeypatch.setattr(memory_write, "DB_PATH", memory_db)

        entry_id, is_dup = memory_write.write_to_db("test content", "fact", 5)
        assert entry_id > 0
        assert is_dup is False

    def test_write_with_dedup_duplicate_returns_existing(self, memory_db, monkeypatch):
        from tools.memory import memory_write
        monkeypatch.setattr(memory_write, "DB_PATH", memory_db)

        id1, dup1 = memory_write.write_to_db("same content", "fact", 5)
        id2, dup2 = memory_write.write_to_db("same content", "fact", 5)
        assert dup1 is False
        assert dup2 is True
        assert id1 == id2

    def test_different_users_same_content_no_dedup(self, memory_db, monkeypatch):
        from tools.memory import memory_write
        monkeypatch.setattr(memory_write, "DB_PATH", memory_db)

        id1, dup1 = memory_write.write_to_db("shared fact", "fact", 5, user_id="user-a")
        id2, dup2 = memory_write.write_to_db("shared fact", "fact", 5, user_id="user-b")
        assert dup1 is False
        assert dup2 is False
        assert id1 != id2

    def test_null_user_dedup(self, memory_db, monkeypatch):
        from tools.memory import memory_write
        monkeypatch.setattr(memory_write, "DB_PATH", memory_db)

        id1, _ = memory_write.write_to_db("no user content", "event", 3)
        id2, dup2 = memory_write.write_to_db("no user content", "event", 3)
        assert dup2 is True
        assert id1 == id2


# ---------------------------------------------------------------------------
# Feature 2: User-Scoped Memory (D180)
# ---------------------------------------------------------------------------

class TestUserScopedMemory:
    def _seed_entries(self, db_path):
        """Insert test entries with different user_ids."""
        conn = sqlite3.connect(str(db_path))
        h = hashlib.sha256
        for content, uid, tid in [
            ("user1 fact", "u1", "t1"),
            ("user2 fact", "u2", "t1"),
            ("shared legacy fact", None, None),
            ("user1 event", "u1", "t1"),
        ]:
            ch = h(content.encode()).hexdigest()
            conn.execute(
                "INSERT INTO memory_entries (content, type, importance, content_hash, user_id, tenant_id) "
                "VALUES (?, 'fact', 5, ?, ?, ?)",
                (content, ch, uid, tid),
            )
        conn.commit()
        conn.close()

    def test_search_filters_by_user(self, memory_db, monkeypatch):
        from tools.memory import memory_db as mdb
        monkeypatch.setattr(mdb, "DB_PATH", memory_db)
        self._seed_entries(memory_db)

        results = mdb.search("fact", limit=10, user_id="u1")
        contents = [r[1] for r in results]
        assert "user1 fact" in contents
        assert "shared legacy fact" in contents  # NULL user_id included
        assert "user2 fact" not in contents

    def test_search_without_user_returns_all(self, memory_db, monkeypatch):
        from tools.memory import memory_db as mdb
        monkeypatch.setattr(mdb, "DB_PATH", memory_db)
        self._seed_entries(memory_db)

        results = mdb.search("fact", limit=10)
        assert len(results) >= 3  # All fact entries

    def test_list_all_user_scoped(self, memory_db, monkeypatch):
        from tools.memory import memory_db as mdb
        monkeypatch.setattr(mdb, "DB_PATH", memory_db)
        self._seed_entries(memory_db)

        results = mdb.list_all(limit=20, user_id="u2")
        contents = [r[1] for r in results]
        assert "user2 fact" in contents
        assert "shared legacy fact" in contents
        assert "user1 fact" not in contents

    def test_hybrid_search_user_scoped(self, memory_db, monkeypatch):
        from tools.memory import hybrid_search
        monkeypatch.setattr(hybrid_search, "DB_PATH", memory_db)
        self._seed_entries(memory_db)

        entries = hybrid_search.get_all_entries(user_id="u1")
        contents = [e[1] for e in entries]
        assert "user1 fact" in contents
        assert "user2 fact" not in contents

    def test_memory_read_user_scoped(self, memory_db, monkeypatch):
        from tools.memory import memory_read
        monkeypatch.setattr(memory_read, "DB_PATH", memory_db)
        self._seed_entries(memory_db)

        entries = memory_read.read_db_recent(limit=10, user_id="u1")
        contents = [e[0] for e in entries]
        assert any("user1" in c for c in contents)
        assert all("user2" not in c for c in contents)


# ---------------------------------------------------------------------------
# Feature 3: Auto-Capture Buffer (D181)
# ---------------------------------------------------------------------------

class TestAutoCapture:
    def test_capture_writes_to_buffer(self, memory_db):
        from tools.memory.auto_capture import capture
        result = capture("test capture", source="hook", db_path=memory_db)
        assert result["status"] == "captured"
        assert result["buffer_id"] > 0
        assert result["buffer_size"] == 1

    def test_capture_dedup_in_buffer(self, memory_db):
        from tools.memory.auto_capture import capture
        r1 = capture("duplicate content", source="hook", db_path=memory_db)
        r2 = capture("duplicate content", source="hook", db_path=memory_db)
        assert r1["status"] == "captured"
        assert r2["status"] == "duplicate"

    def test_flush_buffer_to_entries(self, memory_db):
        from tools.memory.auto_capture import capture, flush_buffer
        capture("flush me", source="hook", db_path=memory_db)
        capture("flush me too", source="auto", db_path=memory_db)

        result = flush_buffer(db_path=memory_db)
        assert result["flushed"] == 2
        assert result["duplicates"] == 0

        # Verify entries moved to memory_entries
        conn = sqlite3.connect(str(memory_db))
        count = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
        conn.close()
        assert count == 2

        # Verify buffer is empty
        conn = sqlite3.connect(str(memory_db))
        buf_count = conn.execute("SELECT COUNT(*) FROM memory_buffer").fetchone()[0]
        conn.close()
        assert buf_count == 0

    def test_flush_dedup_against_entries(self, memory_db):
        from tools.memory.auto_capture import capture, flush_buffer
        from tools.memory.memory_write import write_to_db, DB_PATH
        import tools.memory.memory_write as mw

        # Monkeypatch DB_PATH for write_to_db
        original = mw.DB_PATH
        mw.DB_PATH = memory_db

        # Write directly to entries
        write_to_db("already exists", "fact", 5)

        # Capture same content to buffer
        capture("already exists", source="hook", db_path=memory_db)

        # Flush — should detect duplicate
        result = flush_buffer(db_path=memory_db)
        assert result["duplicates"] == 1
        assert result["flushed"] == 0

        mw.DB_PATH = original

    def test_buffer_status(self, memory_db):
        from tools.memory.auto_capture import capture, buffer_status
        capture("entry 1", source="hook", db_path=memory_db)
        capture("entry 2", source="thinking", db_path=memory_db)

        status = buffer_status(db_path=memory_db)
        assert status["total_buffered"] == 2
        assert status["by_source"]["hook"] == 1
        assert status["by_source"]["thinking"] == 1

    def test_thinking_source_type(self, memory_db):
        from tools.memory.auto_capture import capture
        result = capture(
            "chain of thought reasoning",
            source="thinking",
            memory_type="thinking",
            db_path=memory_db,
        )
        assert result["status"] == "captured"

        conn = sqlite3.connect(str(memory_db))
        row = conn.execute(
            "SELECT source, type FROM memory_buffer WHERE id = ?",
            (result["buffer_id"],),
        ).fetchone()
        conn.close()
        assert row[0] == "thinking"
        assert row[1] == "thinking"


# ---------------------------------------------------------------------------
# Feature 4: Thinking Type (D182) — Time-Decay
# ---------------------------------------------------------------------------

class TestThinkingType:
    def test_thinking_type_in_valid_types(self):
        from tools.memory.memory_write import VALID_TYPES
        assert "thinking" in VALID_TYPES

    def test_thinking_half_life_in_config(self):
        from tools.memory.time_decay import DEFAULT_CONFIG
        assert "thinking" in DEFAULT_CONFIG["half_lives"]
        assert DEFAULT_CONFIG["half_lives"]["thinking"] == 3

    def test_thinking_decays_fast(self):
        from tools.memory.time_decay import compute_decay_factor
        from datetime import datetime, timezone, timedelta

        ref = datetime.now(timezone.utc)
        # 6 days ago (2 half-lives for thinking=3d)
        old = (ref - timedelta(days=6)).isoformat()

        decay = compute_decay_factor(old, memory_type="thinking", reference_time=ref)
        # After 2 half-lives, decay should be ~0.25
        assert decay < 0.3

        # Compare with fact (half-life 90d) — should decay much less
        fact_decay = compute_decay_factor(old, memory_type="fact", reference_time=ref)
        assert fact_decay > 0.9


# ---------------------------------------------------------------------------
# D72 Fix: embed_memory.py provider abstraction
# ---------------------------------------------------------------------------

class TestEmbedMemoryD72:
    def test_get_embedding_client_tries_provider_first(self, monkeypatch):
        """Verify embed_memory tries LLM provider before OpenAI."""
        from tools.memory import embed_memory

        class MockProvider:
            def embed(self, text):
                return [0.1] * 10

        called = {"provider": False}

        def mock_get_provider():
            called["provider"] = True
            return MockProvider()

        monkeypatch.setattr("tools.llm.get_embedding_provider", mock_get_provider)
        client, name = embed_memory.get_embedding_client()
        assert called["provider"] is True
        assert name == "llm_provider"
        assert hasattr(client, "embed")


# ---------------------------------------------------------------------------
# Migration: schema changes
# ---------------------------------------------------------------------------

class TestMigration:
    def test_migration_up_adds_columns(self, tmp_path):
        """Test that migration 002 adds required columns and buffer table."""
        db_path = tmp_path / "memory.db"
        conn = sqlite3.connect(str(db_path))
        # Create minimal pre-migration schema
        conn.executescript("""
            CREATE TABLE memory_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                type TEXT DEFAULT 'event',
                importance INTEGER DEFAULT 5,
                embedding BLOB,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.execute("INSERT INTO memory_entries (content) VALUES ('pre-existing')")
        conn.commit()

        # Run migration
        from tools.db.migrations import __path__ as _  # noqa: ensure importable
        sys.path.insert(0, str(BASE_DIR / "tools" / "db" / "migrations" / "002_memory_enhancements"))
        from up import up as migrate_up
        migrate_up(conn)

        # Verify columns added
        cols = [row[1] for row in conn.execute("PRAGMA table_info(memory_entries)")]
        assert "content_hash" in cols
        assert "user_id" in cols
        assert "tenant_id" in cols
        assert "source" in cols

        # Verify backfill
        row = conn.execute(
            "SELECT content_hash FROM memory_entries WHERE content = 'pre-existing'"
        ).fetchone()
        assert row[0] is not None
        assert len(row[0]) == 64  # SHA-256 hex

        # Verify buffer table exists
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )]
        assert "memory_buffer" in tables

        conn.close()
