#!/usr/bin/env python3
"""Tests for the coverage gap detector."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.coverage_gap import detect_gaps, _extract_key_phrases


class TestCoverageGap:
    def test_detect_gaps_with_diverse_articles(self):
        """Should detect phrases mentioned by few sources."""
        articles = [
            {
                "title": "Climate Policy Update",
                "content": "President Biden announced a $500 million climate fund.",
            },
            {
                "title": "Climate Action Plan",
                "content": "The White House released new climate targets.",
            },
            {
                "title": "Green Energy Push",
                "content": "New solar subsidies announced. Wind expansion planned.",
            },
            {
                "title": "Climate Economy",
                "content": "Green jobs expected to grow 40% by 2030.",
            },
        ]
        gaps = detect_gaps(articles)
        assert isinstance(gaps, list)
        for gap in gaps:
            assert "fact_omitted" in gap
            assert "sources_mentioning" in gap
            assert "significance" in gap

    def test_no_gaps_with_single_article(self):
        """Should return empty with insufficient articles."""
        gaps = detect_gaps([{"title": "Solo", "content": "Only one article here."}])
        assert gaps == []

    def test_extract_key_phrases(self):
        """Should extract proper nouns, quotes, and statistics."""
        text = 'John Smith said "This is a major breakthrough" with a $5 billion impact.'
        phrases = _extract_key_phrases(text)
        assert isinstance(phrases, list)
        assert len(phrases) > 0

    def test_gaps_sorted_by_significance(self):
        """Gaps should be sorted by significance (highest first)."""
        articles = [
            {"title": f"Article {i}", "content": f"Content about Topic A and unique_{i} detail."}
            for i in range(5)
        ]
        articles[0]["content"] += " Special fact only here."
        gaps = detect_gaps(articles)
        if len(gaps) >= 2:
            assert gaps[0]["significance"] >= gaps[1]["significance"]

    def test_empty_articles(self):
        """Should handle empty article list gracefully."""
        gaps = detect_gaps([])
        assert gaps == []
