"""Tests for anomaly-detection in genesis/reflexes/market.py.

Replaces hardcoded thresholds (3.5 rating, 2 reviews, 3 installs) with
adaptive z-score / distribution-based detection + optional LLM triage.
"""
import json
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.market import (
    _adaptive_thresholds,
    _distribution_stats,
    _generate_suggestions,
    _llm_triage,
)


# ---------------------------------------------------------------------------
# _distribution_stats
# ---------------------------------------------------------------------------


class TestDistributionStats:
    def test_empty_list(self):
        result = _distribution_stats([])
        assert result["n"] == 0
        assert result["mean"] == 0.0
        assert result["std"] == 0.0
        assert result["median"] == 0.0

    def test_single_value(self):
        result = _distribution_stats([4.5])
        assert result["n"] == 1
        assert result["mean"] == pytest.approx(4.5)
        assert result["std"] == pytest.approx(0.0)
        assert result["median"] == pytest.approx(4.5)

    def test_uniform_distribution(self):
        result = _distribution_stats([3.0, 3.0, 3.0, 3.0])
        assert result["mean"] == pytest.approx(3.0)
        assert result["std"] == pytest.approx(0.0)

    def test_known_variance(self):
        # values: 2, 4, 4, 4, 5, 5, 7, 9  → mean=5, variance=4, std=2
        result = _distribution_stats([2, 4, 4, 4, 5, 5, 7, 9])
        assert result["mean"] == pytest.approx(5.0)
        assert result["std"] == pytest.approx(2.0)

    def test_median_odd(self):
        result = _distribution_stats([1.0, 3.0, 5.0])
        assert result["median"] == pytest.approx(3.0)

    def test_median_even(self):
        result = _distribution_stats([1.0, 2.0, 3.0, 4.0])
        assert result["median"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# _adaptive_thresholds
# ---------------------------------------------------------------------------


def _make_feedback(modules):
    """Build a feedback dict from list of (slug, avg_rating, count) tuples."""
    return {
        "modules_with_feedback": len(modules),
        "details": [
            {"slug": slug, "avg_rating": rating, "count": count}
            for slug, rating, count in modules
        ],
    }


def _make_stats(modules):
    """Build a stats list from list of (slug, installs) tuples."""
    return [{"slug": slug, "installs": installs} for slug, installs in modules]


class TestAdaptiveThresholds:
    def test_z_score_method_with_enough_data(self):
        """≥3 rated modules triggers z_score method."""
        feedback = _make_feedback([
            ("a", 4.5, 10), ("b", 4.0, 8), ("c", 2.0, 5),
        ])
        stats = _make_stats([("a", 100), ("b", 80), ("c", 50)])
        result = _adaptive_thresholds(stats, feedback)
        assert result["method"] == "z_score"
        assert result["rating_threshold"] >= 1.0

    def test_relative_method_with_one_or_two_ratings(self):
        """1 or 2 rated modules triggers relative method."""
        feedback = _make_feedback([("a", 4.0, 3)])
        stats = _make_stats([("a", 20)])
        result = _adaptive_thresholds(stats, feedback)
        assert result["method"] == "relative"
        assert result["rating_threshold"] == pytest.approx(max(1.0, 4.0 * 0.8))

    def test_fallback_method_with_no_ratings(self):
        """No rated modules triggers fallback method with neutral threshold."""
        feedback = _make_feedback([])
        stats = _make_stats([("a", 10)])
        result = _adaptive_thresholds(stats, feedback)
        assert result["method"] == "fallback"
        assert result["rating_threshold"] == pytest.approx(1.0)

    def test_rating_threshold_never_below_floor(self):
        """rating_threshold is never below 1.0 even with a very low mean."""
        feedback = _make_feedback([
            ("a", 1.1, 5), ("b", 1.2, 5), ("c", 1.0, 5),
        ])
        stats = _make_stats([("a", 5)])
        result = _adaptive_thresholds(stats, feedback)
        assert result["rating_threshold"] >= 1.0

    def test_installs_threshold_uses_median(self):
        """installs_threshold equals the median of the install distribution."""
        stats = _make_stats([("a", 10), ("b", 20), ("c", 30)])
        feedback = _make_feedback([])
        result = _adaptive_thresholds(stats, feedback)
        # median of [10, 20, 30] = 20
        assert result["installs_threshold"] == pytest.approx(20.0)

    def test_installs_threshold_with_single_module(self):
        """Single module: installs_threshold = its install count."""
        stats = _make_stats([("a", 7)])
        feedback = _make_feedback([])
        result = _adaptive_thresholds(stats, feedback)
        assert result["installs_threshold"] == pytest.approx(7.0)

    def test_empty_data_returns_fallback_thresholds(self):
        """Completely empty data sets all thresholds to safe defaults."""
        result = _adaptive_thresholds([], _make_feedback([]))
        assert result["method"] == "fallback"
        assert result["rating_threshold"] == pytest.approx(1.0)
        assert result["installs_threshold"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _generate_suggestions — statistical path (no LLM)
# ---------------------------------------------------------------------------


class TestGenerateSuggestionsStatistical:
    def _patch_llm(self):
        return patch("tools.genesis.reflexes.market._llm_triage", return_value=None)

    def test_bootstrap_suggestion_when_no_stats(self):
        with self._patch_llm():
            suggestions = _generate_suggestions([], _make_feedback([]))
        assert any(s["type"] == "bootstrap" for s in suggestions)

    def test_low_rating_anomaly_detected(self):
        """A module well below cohort mean is flagged as quality_improvement."""
        feedback = _make_feedback([
            ("good-a", 4.8, 10),
            ("good-b", 4.9, 10),
            ("good-c", 4.7, 10),
            ("bad", 1.5, 5),   # severe outlier
        ])
        stats = _make_stats([("good-a", 50), ("good-b", 40), ("good-c", 30), ("bad", 20)])
        with self._patch_llm():
            suggestions = _generate_suggestions(stats, feedback)
        types = [s["type"] for s in suggestions]
        modules = [s["module"] for s in suggestions]
        assert "quality_improvement" in types
        assert "bad" in modules

    def test_high_rated_modules_not_flagged(self):
        """Identical ratings → std=0 → threshold=mean, so none are flagged."""
        feedback = _make_feedback([
            ("a", 4.8, 10), ("b", 4.8, 10), ("c", 4.8, 10),
        ])
        stats = _make_stats([("a", 50), ("b", 40), ("c", 30)])
        with self._patch_llm():
            suggestions = _generate_suggestions(stats, feedback)
        assert not any(s["type"] == "quality_improvement" for s in suggestions)

    def test_feedback_gap_detected_for_popular_module(self):
        """A high-install module without feedback triggers a feedback_gap suggestion."""
        stats = _make_stats([("popular", 1000), ("niche", 2)])
        feedback = _make_feedback([])  # no feedback at all
        with self._patch_llm():
            suggestions = _generate_suggestions(stats, feedback)
        gap_modules = [s["module"] for s in suggestions if s["type"] == "feedback_gap"]
        assert "popular" in gap_modules

    def test_low_install_module_not_flagged_for_feedback_gap(self):
        """A below-median install module without feedback is NOT flagged."""
        stats = _make_stats([("big", 1000), ("tiny", 1)])
        feedback = _make_feedback([("big", 4.5, 5)])
        with self._patch_llm():
            suggestions = _generate_suggestions(stats, feedback)
        gap_modules = [s["module"] for s in suggestions if s["type"] == "feedback_gap"]
        assert "tiny" not in gap_modules

    def test_detection_method_in_suggestion(self):
        """Each suggestion records its detection_method."""
        feedback = _make_feedback([
            ("a", 4.8, 10), ("b", 4.9, 10), ("c", 4.7, 10), ("bad", 1.5, 5),
        ])
        stats = _make_stats([("a", 50), ("b", 40), ("c", 30), ("bad", 20)])
        with self._patch_llm():
            suggestions = _generate_suggestions(stats, feedback)
        for s in suggestions:
            assert "detection_method" in s


# ---------------------------------------------------------------------------
# _generate_suggestions — LLM-enrichment path
# ---------------------------------------------------------------------------


class TestGenerateSuggestionsLLMEnriched:
    def test_llm_enriched_suggestions_used_when_available(self):
        """When _llm_triage returns results, they replace the raw statistical ones."""
        feedback = _make_feedback([
            ("a", 4.8, 10), ("b", 4.9, 10), ("c", 4.7, 10), ("bad", 1.5, 5),
        ])
        stats = _make_stats([("a", 50), ("b", 40), ("c", 30), ("bad", 20)])
        enriched = [
            {"module": "bad", "severity": "high",
             "root_cause": "Consistently low ratings indicate UX issues.",
             "recommended_action": "Audit user journey and fix top pain points."}
        ]
        with patch("tools.genesis.reflexes.market._llm_triage", return_value=enriched):
            suggestions = _generate_suggestions(stats, feedback)
        assert any("llm" in s.get("detection_method", "") for s in suggestions)
        assert any(s["module"] == "bad" for s in suggestions)

    def test_llm_enriched_type_matches_anomaly_type(self):
        """The suggestion type is correctly mapped from the raw anomaly type."""
        feedback = _make_feedback([
            ("a", 4.8, 10), ("b", 4.9, 10), ("c", 4.7, 10), ("bad", 1.5, 5),
        ])
        stats = _make_stats([("a", 50), ("b", 40), ("c", 30), ("bad", 20)])
        enriched = [
            {"module": "bad", "severity": "medium",
             "root_cause": "Low rating.", "recommended_action": "Fix it."}
        ]
        with patch("tools.genesis.reflexes.market._llm_triage", return_value=enriched):
            suggestions = _generate_suggestions(stats, feedback)
        bad_sugg = next((s for s in suggestions if s["module"] == "bad"), None)
        assert bad_sugg is not None
        assert bad_sugg["type"] == "quality_improvement"

    def test_statistical_fallback_when_llm_unavailable(self):
        """_llm_triage returning None falls back to statistical suggestions."""
        feedback = _make_feedback([
            ("a", 4.8, 10), ("b", 4.9, 10), ("c", 4.7, 10), ("bad", 1.5, 5),
        ])
        stats = _make_stats([("a", 50), ("b", 40), ("c", 30), ("bad", 20)])
        with patch("tools.genesis.reflexes.market._llm_triage", return_value=None):
            suggestions = _generate_suggestions(stats, feedback)
        assert any("llm" not in s.get("detection_method", "") for s in suggestions)


# ---------------------------------------------------------------------------
# _llm_triage — error handling
# ---------------------------------------------------------------------------


def _mock_llm_modules(router_content=None, router_side_effect=None):
    """Return (context_manager, ) that patches tools.llm.* in sys.modules."""
    mock_router_inst = MagicMock()
    if router_side_effect is not None:
        mock_router_inst.invoke.side_effect = router_side_effect
    else:
        mock_router_inst.invoke.return_value.content = router_content

    mock_router_mod = MagicMock()
    mock_router_mod.LLMRouter.return_value = mock_router_inst

    mock_provider_mod = MagicMock()
    mock_provider_mod.LLMRequest = MagicMock

    return patch.dict(
        "sys.modules",
        {"tools.llm.router": mock_router_mod, "tools.llm.provider": mock_provider_mod},
    )


class TestLLMTriage:
    def test_empty_anomalies_returns_none(self):
        result = _llm_triage([], {})
        assert result is None

    def test_returns_none_on_import_error(self):
        """When LLM packages raise on import, returns None gracefully."""
        broken_mod = MagicMock()
        broken_mod.LLMRouter.side_effect = ImportError("no llm")
        with patch.dict("sys.modules", {"tools.llm.router": broken_mod,
                                         "tools.llm.provider": MagicMock()}):
            result = _llm_triage([{"type": "low_rating", "module": "x"}], {})
        assert result is None

    def test_returns_none_on_invalid_json(self):
        """When LLM returns garbage JSON, returns None gracefully."""
        with _mock_llm_modules(router_content="not valid json at all"):
            result = _llm_triage([{"type": "low_rating", "module": "x"}], {})
        assert result is None

    def test_returns_none_on_router_exception(self):
        """When router.invoke raises, returns None gracefully."""
        with _mock_llm_modules(router_side_effect=RuntimeError("no provider")):
            result = _llm_triage([{"type": "low_rating", "module": "x"}], {})
        assert result is None

    def test_parses_valid_json_array(self):
        """When LLM returns valid JSON array, it is returned."""
        payload = [{"module": "x", "severity": "high",
                    "root_cause": "Low.", "recommended_action": "Fix."}]
        with _mock_llm_modules(router_content=json.dumps(payload)):
            result = _llm_triage([{"type": "low_rating", "module": "x"}], {})
        assert result == payload

    def test_strips_markdown_fences(self):
        """JSON wrapped in ```json ... ``` fences is extracted correctly."""
        payload = [{"module": "x", "severity": "low",
                    "root_cause": "Gap.", "recommended_action": "Prompt."}]
        fenced = f"```json\n{json.dumps(payload)}\n```"
        with _mock_llm_modules(router_content=fenced):
            result = _llm_triage([{"type": "feedback_gap", "module": "x"}], {})
        assert result == payload
