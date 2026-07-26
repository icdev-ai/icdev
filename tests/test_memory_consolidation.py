#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 AI-driven memory consolidation (Feature 8 — D276).

Covers: keyword extraction, Jaccard similarity, keyword decision thresholds,
LLM decision mocking, dry-run, execute consolidation, batch consolidation,
append-only log, no similar entries.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.memory.memory_consolidation import (
    MemoryConsolidator,
    ACTIONS,
    JACCARD_SKIP_THRESHOLD,
    JACCARD_REPLACE_THRESHOLD,
    JACCARD_KEEP_THRESHOLD,
)


@pytest.fixture
def consolidator():
    return MemoryConsolidator(use_llm=False, dry_run=True)


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


class TestKeywordExtraction:
    def test_basic(self):
        kw = MemoryConsolidator._extract_keywords("The ICDEV™ system manages deployments")
        assert "icdev" in kw
        assert "system" in kw
        assert "manages" in kw
        assert "deployments" in kw
        assert "the" not in kw

    def test_empty_text(self):
        kw = MemoryConsolidator._extract_keywords("")
        assert len(kw) == 0

    def test_short_words_excluded(self):
        kw = MemoryConsolidator._extract_keywords("I am ok")
        assert len(kw) == 0


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    def test_identical_sets(self):
        sim = MemoryConsolidator._jaccard_similarity({"a", "b"}, {"a", "b"})
        assert sim == 1.0

    def test_disjoint_sets(self):
        sim = MemoryConsolidator._jaccard_similarity({"a", "b"}, {"c", "d"})
        assert sim == 0.0

    def test_partial_overlap(self):
        sim = MemoryConsolidator._jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
        assert sim == pytest.approx(0.5)  # 2/4

    def test_empty_sets(self):
        sim = MemoryConsolidator._jaccard_similarity(set(), set())
        assert sim == 1.0

    def test_one_empty(self):
        sim = MemoryConsolidator._jaccard_similarity({"a"}, set())
        assert sim == 0.0


# ---------------------------------------------------------------------------
# Keyword decision thresholds
# ---------------------------------------------------------------------------


class TestKeywordDecision:
    def test_skip_threshold(self, consolidator):
        entries = [{"id": 1, "content": "test", "entry_type": "fact", "similarity": 0.95}]
        result = consolidator._keyword_decide("test content", entries)
        assert result["recommended_action"] == "SKIP"
        assert result["should_write"] is False

    def test_replace_threshold(self, consolidator):
        entries = [{"id": 1, "content": "test", "entry_type": "fact", "similarity": 0.85}]
        result = consolidator._keyword_decide("test content", entries)
        assert result["recommended_action"] == "REPLACE"
        assert result["should_write"] is True

    def test_keep_separate_threshold(self, consolidator):
        entries = [{"id": 1, "content": "test", "entry_type": "fact", "similarity": 0.76}]
        result = consolidator._keyword_decide("test content", entries)
        assert result["recommended_action"] == "KEEP_SEPARATE"
        assert result["should_write"] is True

    def test_below_threshold(self, consolidator):
        entries = [{"id": 1, "content": "test", "entry_type": "fact", "similarity": 0.50}]
        result = consolidator._keyword_decide("test content", entries)
        assert result["recommended_action"] == "KEEP_SEPARATE"

    def test_empty_entries(self, consolidator):
        result = consolidator._keyword_decide("test content", [])
        assert result["recommended_action"] == "KEEP_SEPARATE"
        assert result["should_write"] is True


# ---------------------------------------------------------------------------
# check_for_consolidation
# ---------------------------------------------------------------------------


class TestCheckForConsolidation:
    @patch.object(MemoryConsolidator, "_find_similar", return_value=[])
    def test_no_similar_entries(self, mock_find, consolidator):
        result = consolidator.check_for_consolidation("new content", "fact")
        assert result["recommended_action"] == "KEEP_SEPARATE"
        assert result["method"] == "no_similar"
        assert result["should_write"] is True

    @patch.object(MemoryConsolidator, "_find_similar")
    @patch.object(MemoryConsolidator, "_log_consolidation")
    def test_with_similar_uses_keyword(self, mock_log, mock_find, consolidator):
        mock_find.return_value = [{"id": 1, "content": "similar", "entry_type": "fact", "similarity": 0.92}]
        result = consolidator.check_for_consolidation("similar text", "fact")
        assert result["method"] == "keyword"
        assert result["recommended_action"] == "SKIP"


# ---------------------------------------------------------------------------
# Execute consolidation
# ---------------------------------------------------------------------------


class TestExecuteConsolidation:
    def test_dry_run(self, consolidator):
        result = consolidator.execute_consolidation("REPLACE", "content", target_id=1)
        assert result["status"] == "dry_run"

    def test_skip_action(self):
        c = MemoryConsolidator(use_llm=False, dry_run=False)
        result = c.execute_consolidation("SKIP", "content")
        assert result["status"] == "skipped"

    def test_no_action_without_target(self):
        c = MemoryConsolidator(use_llm=False, dry_run=False)
        result = c.execute_consolidation("REPLACE", "content", target_id=None)
        assert result["status"] == "no_action"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_actions_defined(self):
        assert "MERGE" in ACTIONS
        assert "REPLACE" in ACTIONS
        assert "KEEP_SEPARATE" in ACTIONS
        assert "UPDATE" in ACTIONS
        assert "SKIP" in ACTIONS
        assert len(ACTIONS) == 5

    def test_thresholds_ordering(self):
        assert JACCARD_SKIP_THRESHOLD > JACCARD_REPLACE_THRESHOLD
        assert JACCARD_REPLACE_THRESHOLD > JACCARD_KEEP_THRESHOLD

    def test_threshold_values(self):
        assert JACCARD_SKIP_THRESHOLD == 0.90
        assert JACCARD_REPLACE_THRESHOLD == 0.80
        assert JACCARD_KEEP_THRESHOLD == 0.75


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    @patch("tools.memory.memory_consolidation.get_connection")
    def test_get_stats_handles_db_error(self, mock_get_conn):
        import sqlite3

        mock_get_conn.side_effect = sqlite3.OperationalError("no table")
        c = MemoryConsolidator()
        result = c.get_stats()
        assert result == {"stats": []}


# ---------------------------------------------------------------------------
# oss2-fix-04 (D5) — the REAL _find_similar paths, unmocked.
#
# The pre-existing tests above mock _find_similar, which is exactly why the D5
# defects survived: the dead import (`hybrid_search` instead of `search`) and the
# wrong SQL column (`entry_type` instead of `type`) were never executed. These
# tests exercise both real paths so a regression re-breaks them loudly.
# ---------------------------------------------------------------------------


def _memdb_with(rows):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE memory_entries ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, type TEXT DEFAULT 'event',"
        " created_at TEXT DEFAULT (datetime('now')))"
    )
    for content, mtype in rows:
        conn.execute("INSERT INTO memory_entries (content, type) VALUES (?, ?)", (content, mtype))
    conn.commit()
    return conn


class TestFindSimilarRealPaths:
    def test_hybrid_search_module_exports_search_not_hybrid_search(self):
        """Regression guard for the dead import: the symbol is `search`."""
        import tools.memory.hybrid_search as hs

        assert hasattr(hs, "search")
        assert not hasattr(hs, "hybrid_search"), "the D5 dead-import name must not exist"

    @patch("tools.memory.hybrid_search.search")
    def test_hybrid_path_reads_type_key(self, mock_search):
        """The hybrid result key is `type` (search returns {id, content, type, score})."""
        mock_search.return_value = [
            {"id": 7, "content": "the sky is blue", "type": "fact", "score": 0.95}
        ]
        c = MemoryConsolidator(use_llm=False, dry_run=True, similarity_threshold=0.3)
        out = c._find_similar("the sky is blue today", max_candidates=5)
        assert out and out[0]["entry_type"] == "fact"  # sourced from `type`, not `entry_type`

    @patch("tools.memory.memory_consolidation.get_connection")
    @patch("tools.memory.hybrid_search.search", side_effect=RuntimeError("no fts index"))
    def test_jaccard_fallback_executes_type_column(self, mock_search, mock_conn):
        """When hybrid raises, the Jaccard fallback runs `SELECT ... type AS entry_type`.
        Before D5 this selected a non-existent `entry_type` column, raised
        OperationalError, was swallowed, and returned [] — so nothing consolidated."""
        mock_conn.return_value = _memdb_with([
            ("deploy the service using kubernetes helm charts", "procedure"),
            ("something entirely unrelated about weather", "fact"),
        ])
        c = MemoryConsolidator(use_llm=False, dry_run=True, similarity_threshold=0.3)
        out = c._find_similar("deploy the service using kubernetes helm charts now", max_candidates=5)
        assert out, "Jaccard fallback must find the near-duplicate (type column must resolve)"
        assert out[0]["entry_type"] == "procedure"
        assert out[0]["similarity"] >= 0.3
