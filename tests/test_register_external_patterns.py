#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 innovation signal registration (Feature 10 — D279).

Covers: single/all registration, duplicate detection, scoring,
implementation status reporting, content hash, pattern data.
"""

import sys
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.innovation.register_external_patterns import (
    EXTERNAL_PATTERNS,
    SCORING_WEIGHTS,
    register_pattern,
    register_all,
    score_patterns,
    get_implementation_status,
    _content_hash,
)


@pytest.fixture
def test_db():
    """Create a temporary SQLite DB with innovation_signals table."""
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = Path(db_file.name)
    db_file.close()

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS innovation_signals (
            id TEXT PRIMARY KEY,
            source TEXT,
            source_type TEXT,
            title TEXT,
            description TEXT,
            url TEXT,
            category TEXT,
            innovation_score REAL,
            score_breakdown TEXT,
            content_hash TEXT,
            status TEXT DEFAULT 'new',
            gotcha_layer TEXT,
            implementation_status TEXT,
            classification TEXT DEFAULT 'CUI',
            discovered_at TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    yield db_path
    try:
        db_path.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pattern data
# ---------------------------------------------------------------------------

class TestPatternData:
    def test_has_10_patterns(self):
        assert len(EXTERNAL_PATTERNS) == 10

    def test_all_have_required_fields(self):
        for p in EXTERNAL_PATTERNS:
            assert "source" in p
            assert "title" in p
            assert "description" in p
            assert "scoring_hints" in p

    def test_sources_are_valid(self):
        sources = {p["source"] for p in EXTERNAL_PATTERNS}
        assert sources <= {"agent-zero", "insforge", "both"}

    def test_all_have_categories(self):
        for p in EXTERNAL_PATTERNS:
            assert p.get("category") in ("architecture", "memory", "security", "innovation")


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

class TestScoringWeights:
    def test_weights_sum_to_one(self):
        total = sum(SCORING_WEIGHTS.values())
        assert total == pytest.approx(1.0)

    def test_five_dimensions(self):
        assert len(SCORING_WEIGHTS) == 5
        for dim in ("novelty", "feasibility", "compliance_alignment", "user_impact", "effort"):
            assert dim in SCORING_WEIGHTS


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_consistent(self):
        h1 = _content_hash("test content")
        h2 = _content_hash("test content")
        assert h1 == h2

    def test_different_content(self):
        h1 = _content_hash("content A")
        h2 = _content_hash("content B")
        assert h1 != h2

    def test_hash_length(self):
        h = _content_hash("test")
        assert len(h) == 16


# ---------------------------------------------------------------------------
# Register single pattern
# ---------------------------------------------------------------------------

class TestRegisterPattern:
    def test_register_single(self, test_db):
        result = register_pattern(EXTERNAL_PATTERNS[0], test_db)
        assert result["status"] == "registered"
        assert result["is_duplicate"] is False
        assert "signal_id" in result
        assert "innovation_score" in result

    def test_duplicate_detection(self, test_db):
        register_pattern(EXTERNAL_PATTERNS[0], test_db)
        result = register_pattern(EXTERNAL_PATTERNS[0], test_db)
        assert result["is_duplicate"] is True
        assert result["status"] == "duplicate"


# ---------------------------------------------------------------------------
# Register all
# ---------------------------------------------------------------------------

class TestRegisterAll:
    def test_register_all(self, test_db):
        result = register_all(test_db)
        assert result["registered"] == 10
        assert result["skipped_duplicates"] == 0
        assert result["total_patterns"] == 10
        assert len(result["signals"]) == 10

    def test_register_all_idempotent(self, test_db):
        register_all(test_db)
        result = register_all(test_db)
        assert result["registered"] == 0
        assert result["skipped_duplicates"] == 10


# ---------------------------------------------------------------------------
# Score patterns
# ---------------------------------------------------------------------------

class TestScorePatterns:
    def test_score_all(self):
        result = score_patterns()
        assert "patterns" in result
        assert len(result["patterns"]) == 10
        assert "scoring_weights" in result

    def test_scores_in_range(self):
        result = score_patterns()
        for p in result["patterns"]:
            assert 0.0 <= p["score"] <= 1.0

    def test_sorted_by_score_desc(self):
        result = score_patterns()
        scores = [p["score"] for p in result["patterns"]]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Implementation status
# ---------------------------------------------------------------------------

class TestImplementationStatus:
    def test_all_implemented(self):
        result = get_implementation_status()
        assert result["total"] == 10
        assert result["implemented"] == 10
        assert result["pending"] == 0

    def test_patterns_have_status(self):
        result = get_implementation_status()
        for p in result["patterns"]:
            assert p["implementation_status"] in ("implemented", "pending", "in_progress")
