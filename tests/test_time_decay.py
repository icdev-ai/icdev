# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from tools.memory.time_decay import (
    load_decay_config,
    compute_decay_factor,
    compute_time_aware_score,
    score_entry,
    rank_with_decay,
    DEFAULT_CONFIG,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_memory_db(db_path):
    """Create memory_entries + memory_access_log tables for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT DEFAULT 'event',
            importance INTEGER DEFAULT 5,
            embedding BLOB,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS memory_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            results_count INTEGER,
            search_type TEXT,
            accessed_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


def _insert_entry(db_path, content, memory_type="event", importance=5,
                   created_at=None):
    """Insert a test memory entry with optional timestamp."""
    conn = sqlite3.connect(str(db_path))
    ts = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO memory_entries (content, type, importance, created_at) "
        "VALUES (?, ?, ?, ?)",
        (content, memory_type, importance, ts),
    )
    conn.commit()
    last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return last_id


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadDecayConfig:
    def test_returns_dict(self):
        config = load_decay_config()
        assert isinstance(config, dict)

    def test_has_half_lives(self):
        config = load_decay_config()
        assert "half_lives" in config
        hl = config["half_lives"]
        for t in ("fact", "preference", "event", "insight", "task",
                   "relationship"):
            assert t in hl, f"Missing half-life for {t}"

    def test_weights_sum_to_one(self):
        config = load_decay_config()
        w = config["weights"]
        total = w["relevance"] + w["recency"] + w["importance"]
        assert abs(total - 1.0) < 0.01

    def test_custom_config_path(self, tmp_path):
        # Non-existent path → falls back to defaults
        config = load_decay_config(tmp_path / "nonexistent.yaml")
        assert "half_lives" in config

    def test_min_decay_factor_positive(self):
        config = load_decay_config()
        assert config["min_decay_factor"] > 0.0


# ---------------------------------------------------------------------------
# Decay factor computation
# ---------------------------------------------------------------------------

class TestComputeDecayFactor:
    def test_brand_new_entry(self):
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        decay = compute_decay_factor(ts, "event", reference_time=now)
        assert decay > 0.99

    def test_entry_at_half_life(self):
        now = datetime.now(timezone.utc)
        half_life = DEFAULT_CONFIG["half_lives"]["event"]  # 7 days
        ts = (now - timedelta(days=half_life)).strftime("%Y-%m-%d %H:%M:%S")
        decay = compute_decay_factor(ts, "event", reference_time=now)
        assert abs(decay - 0.5) < 0.05

    def test_very_old_entry(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=3650)).strftime("%Y-%m-%d %H:%M:%S")
        decay = compute_decay_factor(ts, "event", reference_time=now)
        assert decay == DEFAULT_CONFIG["min_decay_factor"]

    def test_event_decays_faster_than_fact(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        event_decay = compute_decay_factor(ts, "event", reference_time=now)
        fact_decay = compute_decay_factor(ts, "fact", reference_time=now)
        assert event_decay < fact_decay

    def test_preference_decays_slowest(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        pref_decay = compute_decay_factor(ts, "preference", reference_time=now)
        event_decay = compute_decay_factor(ts, "event", reference_time=now)
        assert pref_decay > event_decay

    def test_high_importance_resists_decay(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        normal = compute_decay_factor(ts, "event", importance=5,
                                      reference_time=now)
        important = compute_decay_factor(ts, "event", importance=9,
                                         reference_time=now)
        assert important > normal

    def test_unknown_type_uses_default(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        decay = compute_decay_factor(ts, "unknown_type", reference_time=now)
        # Should use default_half_life of 30
        assert abs(decay - 0.5) < 0.05

    def test_min_decay_enforced(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=10000)).strftime("%Y-%m-%d %H:%M:%S")
        decay = compute_decay_factor(ts, "event", reference_time=now)
        assert decay >= DEFAULT_CONFIG["min_decay_factor"]

    def test_reference_time_override(self):
        ref = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ts = "2025-12-25 00:00:00"  # 7 days before ref
        decay = compute_decay_factor(ts, "event", reference_time=ref)
        # event half-life is 7 days, so decay ≈ 0.5
        assert abs(decay - 0.5) < 0.05


# ---------------------------------------------------------------------------
# Time-aware scoring
# ---------------------------------------------------------------------------

class TestComputeTimeAwareScore:
    def test_recent_high_importance_highest(self):
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        score = compute_time_aware_score(
            base_score=1.0, created_at=ts, memory_type="fact",
            importance=10, reference_time=now,
        )
        assert score > 0.9

    def test_old_low_importance_lowest(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
        score = compute_time_aware_score(
            base_score=0.0, created_at=ts, memory_type="event",
            importance=1, reference_time=now,
        )
        assert score < 0.1

    def test_perfect_relevance_dominates(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        score = compute_time_aware_score(
            base_score=1.0, created_at=ts, memory_type="event",
            importance=1, reference_time=now,
        )
        # relevance weight = 0.6, so score >= 0.6
        assert score >= 0.55

    def test_zero_base_still_has_value(self):
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        score = compute_time_aware_score(
            base_score=0.0, created_at=ts, memory_type="fact",
            importance=10, reference_time=now,
        )
        # recency (0.25 * ~1.0) + importance (0.15 * 1.0) ≈ 0.4
        assert score > 0.3

    def test_weights_applied(self):
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        config = dict(DEFAULT_CONFIG)
        config["weights"] = {"relevance": 1.0, "recency": 0.0,
                              "importance": 0.0}
        score = compute_time_aware_score(
            base_score=0.7, created_at=ts, memory_type="event",
            importance=10, config=config, reference_time=now,
        )
        assert abs(score - 0.7) < 0.01


# ---------------------------------------------------------------------------
# Score entry
# ---------------------------------------------------------------------------

class TestScoreEntry:
    def test_scores_existing_entry(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        entry_id = _insert_entry(db, "test fact content", "fact", 7)
        result = score_entry(entry_id, db_path=db)
        assert result["entry_id"] == entry_id
        assert result["type"] == "fact"
        assert result["importance"] == 7
        assert "decay_factor" in result
        assert "classification" in result

    def test_missing_entry_raises(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        with pytest.raises(ValueError, match="not found"):
            score_entry(9999, db_path=db)

    def test_returns_expected_keys(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        entry_id = _insert_entry(db, "test", "event", 5)
        result = score_entry(entry_id, db_path=db)
        expected = ["entry_id", "content", "type", "importance",
                     "created_at", "age_days", "half_life",
                     "decay_factor", "importance_normalized"]
        for key in expected:
            assert key in result, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Rank with decay
# ---------------------------------------------------------------------------

class TestRankWithDecay:
    def test_recent_ranks_above_old(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        now = datetime.now(timezone.utc)
        # Old entry
        _insert_entry(db, "python coding tips for developers", "fact", 5,
                      (now - timedelta(days=180)).strftime("%Y-%m-%d %H:%M:%S"))
        # Recent entry
        _insert_entry(db, "python coding patterns for developers", "fact", 5,
                      now.strftime("%Y-%m-%d %H:%M:%S"))

        results = rank_with_decay("python coding", top_k=10, db_path=db)
        assert len(results) == 2
        # Recent entry should rank higher
        assert results[0]["age_days"] < results[1]["age_days"]

    def test_high_importance_competitive(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        now = datetime.now(timezone.utc)
        # Old but important
        _insert_entry(db, "critical security note about auth", "fact", 10,
                      (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S"))
        # Recent but unimportant
        _insert_entry(db, "security update notes", "event", 1,
                      now.strftime("%Y-%m-%d %H:%M:%S"))

        results = rank_with_decay("security", top_k=10, db_path=db)
        assert len(results) == 2
        # Important old entry should still be competitive
        old_entry = [r for r in results if r["importance"] == 10][0]
        assert old_entry["time_aware_score"] > 0.1

    def test_top_k_limits(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        for i in range(20):
            _insert_entry(db, f"test entry number {i} content", "event", 5)
        results = rank_with_decay("test entry", top_k=5, db_path=db)
        assert len(results) <= 5

    def test_empty_db(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        results = rank_with_decay("anything", top_k=10, db_path=db)
        assert results == []

    def test_returns_expected_keys(self, tmp_path):
        db = tmp_path / "mem.db"
        _init_memory_db(db)
        _insert_entry(db, "test content for search", "fact", 5)
        results = rank_with_decay("test", top_k=10, db_path=db)
        assert len(results) == 1
        r = results[0]
        expected = ["entry_id", "content", "type", "importance",
                     "base_score", "decay_factor", "time_aware_score",
                     "age_days"]
        for key in expected:
            assert key in r, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Hybrid search integration
# ---------------------------------------------------------------------------

class TestHybridSearchIntegration:
    def test_time_decay_flag_accepted(self):
        """Verify hybrid_search module accepts --time-decay flag."""
        from tools.memory.hybrid_search import hybrid_rank
        import inspect
        sig = inspect.signature(hybrid_rank)
        assert "time_decay_enabled" in sig.parameters

    def test_backward_compatible(self):
        """Verify hybrid_rank works without time-decay flag."""
        from tools.memory.hybrid_search import hybrid_rank
        entries = [(1, "test content", "event", 5, None, "2026-01-01 00:00:00")]
        bm25 = [0.8]
        results = hybrid_rank(entries, bm25, None, 0.7, 0.3)
        assert len(results) == 1
        assert results[0][0] == 0.8  # BM25-only score

    def test_time_decay_changes_score(self):
        """Verify time-decay changes the combined score."""
        from tools.memory.hybrid_search import hybrid_rank
        entries = [(1, "test content", "event", 5, None, "2020-01-01 00:00:00")]
        bm25 = [0.8]
        # Without decay
        results_no_decay = hybrid_rank(entries, bm25, None, 0.7, 0.3)
        # With decay
        results_with_decay = hybrid_rank(
            entries, bm25, None, 0.7, 0.3,
            time_decay_enabled=True, decay_config=DEFAULT_CONFIG,
        )
        # Scores should differ (old entry penalized by decay)
        assert results_no_decay[0][0] != results_with_decay[0][0]
