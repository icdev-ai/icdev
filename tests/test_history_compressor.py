#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 44 3-tier history compression (Feature 5 — D271-D274).

Covers: topic boundary detection, budget allocation, keyword extraction,
LLM-unavailable fallback, bulk merge, token estimation, truncation,
empty messages, single topic.
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.memory.history_compressor import (
    HistoryCompressor,
    TopicBoundary,
    DEFAULT_BUDGET,
    _STOPWORDS,
)


@pytest.fixture
def compressor():
    return HistoryCompressor()


def _msg(turn, content, role="user", minutes_offset=0):
    """Helper: create a message dict."""
    ts = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes_offset)
    return {
        "turn_number": turn,
        "role": role,
        "content": content,
        "created_at": ts.isoformat(),
    }


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

class TestTokenEstimation:
    def test_estimate_tokens(self):
        assert HistoryCompressor._estimate_tokens("") == 1  # min 1
        assert HistoryCompressor._estimate_tokens("abcd") == 1
        assert HistoryCompressor._estimate_tokens("a" * 100) == 25

    def test_estimate_tokens_long(self):
        assert HistoryCompressor._estimate_tokens("x" * 4000) == 1000


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

class TestKeywordExtraction:
    def test_basic_extraction(self):
        kw = HistoryCompressor._extract_keywords("The quick brown fox jumped over the lazy dog")
        assert "quick" in kw
        assert "brown" in kw
        assert "fox" in kw
        assert "the" not in kw  # stopword

    def test_stopwords_filtered(self):
        kw = HistoryCompressor._extract_keywords("this is a test of the system")
        assert "test" in kw
        assert "system" in kw
        assert "this" not in kw
        assert "the" not in kw

    def test_short_words_excluded(self):
        kw = HistoryCompressor._extract_keywords("I am ok so do it")
        # All words <= 2 chars should be excluded
        assert len(kw) == 0

    def test_empty_text(self):
        kw = HistoryCompressor._extract_keywords("")
        assert len(kw) == 0


# ---------------------------------------------------------------------------
# Time gap calculation
# ---------------------------------------------------------------------------

class TestTimeGap:
    def test_gap_calculation(self):
        m1 = _msg(1, "hello", minutes_offset=0)
        m2 = _msg(2, "world", minutes_offset=45)
        gap = HistoryCompressor._time_gap_minutes_between(m1, m2)
        assert gap == pytest.approx(45.0, abs=0.1)

    def test_no_timestamps(self):
        m1 = {"content": "a"}
        m2 = {"content": "b"}
        gap = HistoryCompressor._time_gap_minutes_between(m1, m2)
        assert gap == 0.0

    def test_invalid_timestamps(self):
        m1 = {"created_at": "invalid"}
        m2 = {"created_at": "also invalid"}
        gap = HistoryCompressor._time_gap_minutes_between(m1, m2)
        assert gap == 0.0


# ---------------------------------------------------------------------------
# Topic boundary detection
# ---------------------------------------------------------------------------

class TestTopicBoundaryDetection:
    def test_no_messages(self, compressor):
        boundaries = compressor.detect_topic_boundaries([])
        assert boundaries == []

    def test_single_message(self, compressor):
        boundaries = compressor.detect_topic_boundaries([_msg(1, "hello")])
        assert len(boundaries) == 1
        assert boundaries[0].start_turn == 1

    def test_time_gap_boundary(self, compressor):
        messages = [
            _msg(1, "topic one discussion about deployment", minutes_offset=0),
            _msg(2, "more about deployment strategies", minutes_offset=5),
            _msg(3, "testing integration testing framework setup", minutes_offset=60),  # 60 min gap > 30
            _msg(4, "testing integration testing results pass", minutes_offset=65),
        ]
        boundaries = compressor.detect_topic_boundaries(messages)
        # Time gap at 60 min creates at least one boundary splitting messages 1-2 from 3+
        assert len(boundaries) >= 2
        assert boundaries[0].end_turn == 2  # First topic ends at turn 2
        assert boundaries[1].start_turn == 3  # Second topic starts at turn 3

    def test_continuous_conversation_single_topic(self, compressor):
        messages = [_msg(i, f"same topic message {i}", minutes_offset=i) for i in range(1, 6)]
        boundaries = compressor.detect_topic_boundaries(messages)
        assert len(boundaries) == 1  # All within 30 minutes


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

class TestCompression:
    def test_empty_messages(self, compressor):
        result = compressor.compress([], budget_tokens=4000)
        assert result == []

    def test_within_budget_returns_as_is(self, compressor):
        messages = [_msg(1, "short message")]
        result = compressor.compress(messages, budget_tokens=4000)
        assert result == messages

    def test_compress_reduces_messages(self, compressor):
        # Create many long messages that exceed budget
        messages = [
            _msg(i, f"Long content about topic {'A' if i < 10 else 'B'} " * 50,
                 minutes_offset=i * (35 if i == 10 else 2))
            for i in range(1, 21)
        ]
        result = compressor.compress(messages, budget_tokens=200)
        total_original = sum(len(m["content"]) for m in messages)
        total_compressed = sum(len(m.get("content", "")) for m in result)
        assert total_compressed < total_original

    def test_truncation_keeps_recent(self, compressor):
        messages = [_msg(i, f"Message {i} content", minutes_offset=i) for i in range(1, 20)]
        result = compressor._truncate_to_budget(messages, budget_tokens=10)
        # Should keep most recent messages
        if result:
            assert result[-1]["turn_number"] == 19


# ---------------------------------------------------------------------------
# TopicBoundary dataclass
# ---------------------------------------------------------------------------

class TestTopicBoundaryDataclass:
    def test_create(self):
        tb = TopicBoundary(start_turn=1, end_turn=5, keywords=["deploy", "test"], message_count=5)
        assert tb.start_turn == 1
        assert tb.end_turn == 5
        assert len(tb.keywords) == 2

    def test_defaults(self):
        tb = TopicBoundary(start_turn=1, end_turn=1)
        assert tb.keywords == []
        assert tb.time_span_minutes == 0.0
        assert tb.summary == ""


# ---------------------------------------------------------------------------
# Summarization fallback
# ---------------------------------------------------------------------------

class TestSummarization:
    def test_summarize_empty(self, compressor):
        result = compressor._summarize_topic([], max_tokens=100)
        assert result == ""

    def test_summarize_short_content(self, compressor):
        messages = [_msg(1, "Hello"), _msg(2, "World")]
        result = compressor._summarize_topic(messages, max_tokens=100)
        assert len(result) > 0

    def test_merge_summaries(self, compressor):
        summaries = ["Topic 1 summary", "Topic 2 summary"]
        result = compressor._merge_summaries(summaries, max_tokens=200)
        assert "Topic 1" in result
        assert "Topic 2" in result

    def test_merge_truncates_long(self, compressor):
        summaries = ["Very long " * 200, "Also long " * 200]
        result = compressor._merge_summaries(summaries, max_tokens=50)
        assert len(result) <= 50 * 4 + 10  # rough chars budget + margin


# ---------------------------------------------------------------------------
# Budget allocation
# ---------------------------------------------------------------------------

class TestBudgetAllocation:
    def test_default_budget_sums_to_one(self):
        total = sum(DEFAULT_BUDGET.values())
        assert total == pytest.approx(1.0)

    def test_custom_budget(self):
        custom = {"current_topic": 0.70, "historical_topics": 0.20, "bulk": 0.10}
        compressor = HistoryCompressor(budget_allocation=custom)
        assert compressor._budget["current_topic"] == 0.70
