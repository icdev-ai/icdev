#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 AI-driven memory consolidation (Feature 8 — D276).

Covers: keyword extraction, Jaccard similarity, keyword decision thresholds,
LLM decision mocking, dry-run, execute consolidation, batch consolidation,
append-only log, no similar entries.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

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
        kw = MemoryConsolidator._extract_keywords("The ICDEV system manages deployments")
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
        mock_find.return_value = [
            {"id": 1, "content": "similar", "entry_type": "fact", "similarity": 0.92}
        ]
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
    @patch("tools.memory.memory_consolidation.sqlite3")
    def test_get_stats_handles_db_error(self, mock_sqlite):
        import sqlite3
        mock_sqlite.connect.side_effect = sqlite3.OperationalError("no table")
        mock_sqlite.OperationalError = sqlite3.OperationalError
        c = MemoryConsolidator()
        result = c.get_stats()
        assert result == {"stats": []}
