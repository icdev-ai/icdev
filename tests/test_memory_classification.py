#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for memory system classification tagging and retrieval filtering.

sec-eco-06: Tag every memory entry with classification and compartment on write.
Filter retrieval by current security context and apply hybrid-search scoring
downgrade to higher-classification entries.
"""

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
    """Create a temporary memory.db with classification/compartment columns."""
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
            source TEXT DEFAULT 'manual',
            decay_weight REAL DEFAULT 1.0,
            classification TEXT DEFAULT 'CUI',
            compartment TEXT DEFAULT ''
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
    """)
    conn.close()
    return db_path


@pytest.fixture
def legacy_memory_db(tmp_path):
    """Pre-migration schema without classification/compartment columns."""
    db_path = tmp_path / "legacy_memory.db"
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
            source TEXT DEFAULT 'manual',
            decay_weight REAL DEFAULT 1.0
        );
    """)
    conn.execute("INSERT INTO memory_entries (content) VALUES ('legacy entry')")
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_entries(db_path):
    """Insert entries with varied classifications and compartments."""
    conn = sqlite3.connect(str(db_path))
    rows = [
        ("public fact", "PUBLIC", ""),
        ("cui fact", "CUI", ""),
        ("cui fact compartment A", "CUI", "A"),
        ("secret fact", "SECRET", ""),
        ("secret fact compartment B", "SECRET", "B"),
        ("top secret fact", "TOP SECRET", ""),
        ("top secret sci", "TOP SECRET//SCI", "SCI"),
        ("cui multi compartment", "CUI", "A,B"),
    ]
    for content, classification, compartment in rows:
        conn.execute(
            "INSERT INTO memory_entries (content, type, classification, compartment) "
            "VALUES (?, 'fact', ?, ?)",
            (content, classification, compartment),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migration_adds_columns(self, legacy_memory_db):
        sys.path.insert(
            0,
            str(BASE_DIR / "tools" / "db" / "migrations" / "152_memory_classification"),
        )
        from migration import up

        conn = sqlite3.connect(str(legacy_memory_db))
        up(conn)

        cols = [row[1] for row in conn.execute("PRAGMA table_info(memory_entries)")]
        assert "classification" in cols
        assert "compartment" in cols

        # Backfill
        row = conn.execute(
            "SELECT classification, compartment FROM memory_entries WHERE content = 'legacy entry'"
        ).fetchone()
        assert row[0] == "CUI"
        assert row[1] == ""
        conn.close()


# ---------------------------------------------------------------------------
# Write tests
# ---------------------------------------------------------------------------


class TestWriteClassification:
    def test_write_defaults_to_cui(self, memory_db, monkeypatch):
        from tools.memory import memory_write

        monkeypatch.setattr(memory_write, "DB_PATH", memory_db)
        result = memory_write.write_to_db("default classified", "fact", 5)
        assert result["id"] > 0

        conn = sqlite3.connect(str(memory_db))
        row = conn.execute(
            "SELECT classification, compartment FROM memory_entries WHERE id = ?",
            (result["id"],),
        ).fetchone()
        conn.close()
        assert row[0] == "CUI"
        assert row[1] == ""

    def test_write_custom_classification(self, memory_db, monkeypatch):
        from tools.memory import memory_write

        monkeypatch.setattr(memory_write, "DB_PATH", memory_db)
        result = memory_write.write_to_db(
            "secret stuff", "fact", 5, classification="SECRET", compartment="B"
        )
        conn = sqlite3.connect(str(memory_db))
        row = conn.execute(
            "SELECT classification, compartment FROM memory_entries WHERE id = ?",
            (result["id"],),
        ).fetchone()
        conn.close()
        assert row[0] == "SECRET"
        assert row[1] == "B"

    def test_dedup_ignores_classification(self, memory_db, monkeypatch):
        from tools.memory import memory_write

        monkeypatch.setattr(memory_write, "DB_PATH", memory_db)
        r1 = memory_write.write_to_db("dup test", "fact", 5, classification="CUI")
        r2 = memory_write.write_to_db("dup test", "fact", 5, classification="SECRET")
        assert r2["status"] == "duplicate_merged"
        assert r1["id"] == r2["id"]


# ---------------------------------------------------------------------------
# Read filtering tests
# ---------------------------------------------------------------------------


class TestReadFiltering:
    def test_read_filters_by_clearance(self, memory_db, monkeypatch):
        from tools.memory import memory_read

        monkeypatch.setattr(memory_read, "DB_PATH", memory_db)
        _seed_entries(memory_db)

        entries = memory_read.read_db_recent(limit=20, clearance="CUI")
        contents = [e[0] for e in entries]
        assert "public fact" in contents
        assert "cui fact" in contents
        assert "secret fact" not in contents
        assert "top secret fact" not in contents

    def test_read_filters_by_compartment(self, memory_db, monkeypatch):
        from tools.memory import memory_read

        monkeypatch.setattr(memory_read, "DB_PATH", memory_db)
        _seed_entries(memory_db)

        entries = memory_read.read_db_recent(
            limit=20, clearance="CUI", compartments=["A"]
        )
        contents = [e[0] for e in entries]
        assert "cui fact compartment A" in contents
        # "cui multi compartment" requires both A and B; user only has A
        assert "cui multi compartment" not in contents
        assert "cui fact" in contents  # no compartment required
        assert "secret fact compartment B" not in contents  # wrong clearance + compartment

    def test_read_no_clearance_returns_all(self, memory_db, monkeypatch):
        from tools.memory import memory_read

        monkeypatch.setattr(memory_read, "DB_PATH", memory_db)
        _seed_entries(memory_db)

        entries = memory_read.read_db_recent(limit=20)
        assert len(entries) == 8

    def test_read_clearance_case_insensitive(self, memory_db, monkeypatch):
        from tools.memory import memory_read

        monkeypatch.setattr(memory_read, "DB_PATH", memory_db)
        _seed_entries(memory_db)

        entries = memory_read.read_db_recent(limit=20, clearance="secret")
        contents = [e[0] for e in entries]
        assert "secret fact" in contents
        assert "top secret fact" not in contents


# ---------------------------------------------------------------------------
# Hybrid search filtering + scoring tests
# ---------------------------------------------------------------------------


class TestHybridSearchFiltering:
    def test_hybrid_search_filters_by_clearance(self, memory_db, monkeypatch):
        from tools.memory import hybrid_search

        monkeypatch.setattr(hybrid_search, "DB_PATH", memory_db)
        _seed_entries(memory_db)

        entries = hybrid_search.get_all_entries(clearance="CUI")
        contents = [e[1] for e in entries]
        assert "public fact" in contents
        assert "cui fact" in contents
        assert "secret fact" not in contents

    def test_hybrid_search_filters_by_compartment(self, memory_db, monkeypatch):
        from tools.memory import hybrid_search

        monkeypatch.setattr(hybrid_search, "DB_PATH", memory_db)
        _seed_entries(memory_db)

        entries = hybrid_search.get_all_entries(
            clearance="CUI", compartments=["A"]
        )
        contents = [e[1] for e in entries]
        assert "cui fact compartment A" in contents
        # multi-compartment entry requires both A and B; user only has A
        assert "cui multi compartment" not in contents
        assert "cui fact" in contents
        assert "cui fact compartment B" not in contents

    def test_score_penalty_downgrades_higher_classification(self, memory_db, monkeypatch):
        from tools.memory import hybrid_search

        monkeypatch.setattr(hybrid_search, "DB_PATH", memory_db)
        conn = sqlite3.connect(str(memory_db))
        # Use a clean DB with only these two entries so BM25 scores are positive and identical
        conn.execute("DELETE FROM memory_entries")
        conn.execute(
            "INSERT INTO memory_entries (content, type, classification, compartment) "
            "VALUES (?, 'fact', ?, ?)",
            ("target keyword here", "TOP SECRET", ""),
        )
        conn.execute(
            "INSERT INTO memory_entries (content, type, classification, compartment) "
            "VALUES (?, 'fact', ?, ?)",
            ("target keyword here", "CUI", ""),
        )
        conn.commit()
        ids = {
            row[1]: row[0]
            for row in conn.execute("SELECT id, classification FROM memory_entries WHERE content = 'target keyword here'")
        }
        conn.close()

        entries = hybrid_search.get_all_entries(clearance="TOP SECRET")
        bm25_scores = hybrid_search.bm25_search("target keyword here", entries)
        # Both entries have identical raw BM25 scores
        ts_idx = next(i for i, e in enumerate(entries) if e[1] == "target keyword here" and e[6] == "TOP SECRET")
        cui_idx = next(i for i, e in enumerate(entries) if e[1] == "target keyword here" and e[6] == "CUI")
        assert abs(bm25_scores[ts_idx] - bm25_scores[cui_idx]) < 0.01  # same raw BM25

        # hybrid_rank applies classification penalty
        results = hybrid_search.hybrid_rank(
            entries, bm25_scores, None, 1.0, 0.0, classification_penalty=True
        )
        ts_result = next(r for r in results if r[1] == ids.get("TOP SECRET"))
        cui_result = next(r for r in results if r[1] == ids.get("CUI"))
        # Ratio of penalized scores equals the penalty ratio (independent of BM25 sign)
        expected_ratio = 0.55 / 0.85  # TOP SECRET penalty / CUI penalty
        actual_ratio = ts_result[0] / cui_result[0]
        assert abs(actual_ratio - expected_ratio) < 0.01


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


class TestClassificationHelpers:
    def test_classification_order_public_lowest(self):
        from tools.memory.memory_read import _classification_level

        assert _classification_level("PUBLIC") < _classification_level("CUI")
        assert _classification_level("CUI") < _classification_level("SECRET")
        assert _classification_level("SECRET") < _classification_level("TOP SECRET")
        assert _classification_level("TOP SECRET") < _classification_level("TOP SECRET//SCI")

    def test_compartment_subset(self):
        from tools.memory.memory_read import _compartments_allowed

        assert _compartments_allowed("", ["A"]) is True
        assert _compartments_allowed("A", ["A"]) is True
        assert _compartments_allowed("A,B", ["A", "B"]) is True
        assert _compartments_allowed("A,B", ["A"]) is False
        assert _compartments_allowed("C", ["A", "B"]) is False
