#!/usr/bin/env python3
"""Tests for the deterministic bias scorer."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.bias_scorer import score_article


class TestBiasScorer:
    def test_neutral_article(self):
        """Neutral, well-sourced article should score high."""
        text = (
            'According to researchers at MIT, the study found a 15% improvement '
            'compared to the baseline. However, critics argue that the sample size '
            'of 500 participants may not be representative. On the other hand, '
            'supporters say the methodology was peer-reviewed.'
        )
        result = score_article(text)
        assert result["composite"] >= 3.5
        assert result["color"] in ("blue", "purple", "green")

    def test_biased_article(self):
        """Heavily loaded article should score low."""
        text = (
            'The radical proposal is clearly a dangerous disaster. Everyone knows '
            'this extreme approach will be catastrophic. The truth is, this shocking '
            'propaganda from the corrupt regime is terrifying.'
        )
        result = score_article(text)
        assert result["composite"] <= 2.5
        assert result["color"] in ("yellow", "red")
        assert len(result["findings"]) > 0

    def test_dimensions_present(self):
        """All 5 bias dimensions should be present."""
        result = score_article("Simple test article with no bias indicators.")
        dims = result["dimensions"]
        assert "loaded_language" in dims
        assert "framing_balance" in dims
        assert "statistical_honesty" in dims
        assert "source_transparency" in dims
        assert "omission_severity" in dims

    def test_scores_in_range(self):
        """All scores should be between 1.0 and 5.0."""
        result = score_article("Test article content here.")
        for dim, score in result["dimensions"].items():
            assert 1.0 <= score <= 5.0, f"{dim} score {score} out of range"
        assert 1.0 <= result["composite"] <= 5.0

    def test_color_rating(self):
        """Color rating should be one of the valid FAR 15.305 colors."""
        result = score_article("An average article with some content.")
        assert result["color"] in ("blue", "purple", "green", "yellow", "red")
        assert result["color_label"] in ("Exceptional", "Outstanding", "Acceptable", "Marginal", "Unacceptable")

    def test_balance_phrases_boost_score(self):
        """Articles with balancing language should score higher on framing."""
        balanced = (
            "However, critics argue otherwise. On the other hand, "
            "some believe differently. In contrast, data shows."
        )
        unbalanced = "This is the only way. No alternative exists. Period."
        r1 = score_article(balanced)
        r2 = score_article(unbalanced)
        assert r1["dimensions"]["framing_balance"] > r2["dimensions"]["framing_balance"]

    def test_empty_text(self):
        """Should handle empty text gracefully."""
        result = score_article("")
        assert result["composite"] >= 1.0
        assert result["color"] is not None

    def test_statistics_without_context(self):
        """Statistics without context should lower statistical_honesty."""
        text = "The rate increased by 500%. Revenue hit $2 billion. Costs rose 10x more than expected."
        result = score_article(text)
        assert result["dimensions"]["statistical_honesty"] <= 3.0

    def test_anonymous_sources(self):
        """Anonymous sources should lower source_transparency."""
        text = "Sources say the deal is done. Insiders claim it was reportedly agreed upon. Allegedly final."
        result = score_article(text)
        assert result["dimensions"]["source_transparency"] <= 3.0

    def test_findings_explain_bias(self):
        """Findings list should explain detected bias patterns."""
        text = "The radical extreme dangerous shocking proposal is obviously the truth."
        result = score_article(text)
        findings = result["findings"]
        assert len(findings) > 0
        assert any("loaded" in f.lower() or "language" in f.lower() for f in findings)
