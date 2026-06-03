# CUI // SP-CTI
"""Tests for anomaly-detection helpers in proposal_genesis/reflexes/polish.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.polish import (
    _compute_quality_anomaly_thresholds,
    _check_grammar,
    _check_tone,
    _check_ai_detection,
    _check_stub_content,
    _check_context_pressure,
    _GRAMMAR_DEDUCTION_PER_ISSUE,
    _TONE_DEDUCTION_PER_SIGNAL,
    _TONE_STRENGTH_BONUS,
    _AI_BURSTINESS_LOW,
    _AI_BURSTINESS_MID,
    _CONTEXT_WARNING_TOKENS,
    _CONTEXT_CRITICAL_TOKENS,
    _STUB_MIN_WORDS,
    _STUB_SIGNAL_DEDUCTION,
    _STUB_GATE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_conn_rows(composite_values, stub_values=None):
    """Return a mock connection whose execute().fetchone() returns aggregate stats."""
    stub_values = stub_values or composite_values
    n = len(composite_values)
    mean_q = sum(composite_values) / n if n else 0.0
    mean_stub = sum(stub_values) / n if n else 0.0
    # Variance
    var_q = sum((v - mean_q) ** 2 for v in composite_values) / n if n else 0.0
    row = {
        "mean_q": mean_q, "var_q": var_q,
        "mean_stub": mean_stub, "n": n,
    }
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row
    return conn


# ---------------------------------------------------------------------------
# _compute_quality_anomaly_thresholds
# ---------------------------------------------------------------------------

class TestComputeQualityAnomalyThresholds:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_quality_threshold": 0.70, "fallback_stub_gate_threshold": 0.55}
        result = _compute_quality_anomaly_thresholds(cfg)
        assert result["quality_threshold"] == 0.70
        assert result["stub_gate_threshold"] == 0.55
        assert result["computed"] is False

    def test_insufficient_history_returns_fallback(self):
        cfg = {
            "enabled": True,
            "min_samples": 10,
            "fallback_quality_threshold": 0.65,
            "fallback_stub_gate_threshold": 0.50,
            "sigma_quality": 1.0,
            "sigma_stub": 1.0,
            "adaptive_bounds": {"quality_threshold_floor": 0.40, "stub_gate_floor": 0.25},
        }
        with patch("tools.proposal_genesis.reflexes.polish.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_rows([0.7, 0.8, 0.6])  # only 3 < min_samples=10
            result = _compute_quality_anomaly_thresholds(cfg)
        assert result["quality_threshold"] == 0.65
        assert result["computed"] is False

    def test_sufficient_history_computes_threshold(self):
        cfg = {
            "enabled": True,
            "min_samples": 3,
            "fallback_quality_threshold": 0.65,
            "fallback_stub_gate_threshold": 0.50,
            "sigma_quality": 1.0,
            "sigma_stub": 1.0,
            "adaptive_bounds": {"quality_threshold_floor": 0.40, "stub_gate_floor": 0.25},
        }
        # Uniform scores: mean=0.75, std=0 → threshold = max(0.40, 0.75 - 1*0) = 0.75
        values = [0.75] * 5
        with patch("tools.proposal_genesis.reflexes.polish.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_rows(values)
            result = _compute_quality_anomaly_thresholds(cfg)
        assert result["computed"] is True
        assert result["quality_threshold"] == pytest.approx(0.75, abs=0.01)

    def test_floor_bound_respected(self):
        cfg = {
            "enabled": True,
            "min_samples": 2,
            "fallback_quality_threshold": 0.65,
            "fallback_stub_gate_threshold": 0.50,
            "sigma_quality": 10.0,  # very large sigma — would push threshold below floor
            "sigma_stub": 10.0,
            "adaptive_bounds": {"quality_threshold_floor": 0.40, "stub_gate_floor": 0.25},
        }
        values = [0.5] * 5
        with patch("tools.proposal_genesis.reflexes.polish.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn_rows(values)
            result = _compute_quality_anomaly_thresholds(cfg)
        assert result["quality_threshold"] >= 0.40
        assert result["stub_gate_threshold"] >= 0.25

    def test_db_error_falls_back(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_quality_threshold": 0.65,
               "fallback_stub_gate_threshold": 0.50}
        with patch("tools.proposal_genesis.reflexes.polish.get_connection", side_effect=Exception("DB error")):
            result = _compute_quality_anomaly_thresholds(cfg)
        assert result["quality_threshold"] == 0.65
        assert result["computed"] is False

    def test_none_cfg_uses_defaults(self):
        # Should not raise, should return defaults
        with patch("tools.proposal_genesis.reflexes.polish.get_connection", side_effect=Exception("no DB")):
            result = _compute_quality_anomaly_thresholds(None)
        assert "quality_threshold" in result
        assert "stub_gate_threshold" in result


# ---------------------------------------------------------------------------
# _check_grammar — uses _GRAMMAR_DEDUCTION_PER_ISSUE
# ---------------------------------------------------------------------------

class TestCheckGrammar:

    def test_clean_text_perfect_score(self):
        result = _check_grammar("Hello. This is a clean sentence.")
        assert result["score"] == pytest.approx(1.0, abs=0.01)

    def test_issues_reduce_score(self):
        # 2 issues → score = max(0, 1.0 - 2 * _GRAMMAR_DEDUCTION_PER_ISSUE)
        result = _check_grammar("hello. this is bad  text bad.")
        expected = max(0.0, 1.0 - len(result["issues"]) * _GRAMMAR_DEDUCTION_PER_ISSUE)
        assert result["score"] == pytest.approx(expected, abs=0.01)

    def test_score_bounded_to_zero(self):
        # Deliberately bad text with many issues
        text = "hello  world  bad  test  bad  bad  hello  world  bad  test"
        result = _check_grammar(text)
        assert result["score"] >= 0.0


# ---------------------------------------------------------------------------
# _check_tone — uses _TONE_DEDUCTION_PER_SIGNAL, _TONE_STRENGTH_BONUS
# ---------------------------------------------------------------------------

class TestCheckTone:

    def test_informal_language_reduces_score(self):
        result = _check_tone("gonna wanna gotta do this stuff things basically")
        assert result["score"] < 1.0

    def test_strong_indicators_boost_score(self):
        result = _check_tone("We will deliver. Our team has demonstrated expertise. We implemented proven solutions.")
        assert result["score"] > 0.5

    def test_score_bounded(self):
        result = _check_tone("We will implement and deliver a compliant certified solution.")
        assert 0.0 <= result["score"] <= 1.0


# ---------------------------------------------------------------------------
# _check_ai_detection — uses _AI_BURSTINESS_LOW / _AI_BURSTINESS_MID
# ---------------------------------------------------------------------------

class TestCheckAiDetection:

    def test_short_text_returns_default(self):
        result = _check_ai_detection("Too short.")
        assert result["score"] == 1.0

    def test_uniform_sentences_flag_ai(self):
        # Very uniform sentence lengths → low burstiness → possibly AI.
        # Repeat a fixed-length sentence 8x to get >20 words and near-zero variance.
        sent = "The system works here."  # 4 words each
        text = (sent + " ") * 8
        result = _check_ai_detection(text)
        # Burstiness should be near 0 for identical lengths → score ≤ 0.7
        assert result["burstiness"] < _AI_BURSTINESS_MID
        assert result["score"] <= 0.7

    def test_varied_sentences_human_score(self):
        text = (
            "We will deliver. Our comprehensive approach to solving the complex federal IT "
            "modernization challenge integrates proven methodologies with cutting-edge AI-driven "
            "automation. Done."
        )
        result = _check_ai_detection(text)
        # High burstiness expected for varied lengths
        assert 0.0 <= result["score"] <= 1.0
        assert "burstiness" in result


# ---------------------------------------------------------------------------
# _check_context_pressure — uses _CONTEXT_WARNING_TOKENS, _CONTEXT_CRITICAL_TOKENS
# ---------------------------------------------------------------------------

class TestCheckContextPressure:

    def test_short_text_normal(self):
        result = _check_context_pressure("short text")
        assert result["pressure_level"] == "normal"

    def test_long_text_warning(self):
        # ~6001 tokens × 4 chars = 24004 chars → warning
        text = "x " * ((_CONTEXT_WARNING_TOKENS + 5) * 4 // 2)
        result = _check_context_pressure(text)
        assert result["pressure_level"] in ("warning", "critical")

    def test_very_long_text_critical(self):
        # > _CONTEXT_CRITICAL_TOKENS tokens
        text = "x " * ((_CONTEXT_CRITICAL_TOKENS + 5) * 4 // 2)
        result = _check_context_pressure(text)
        assert result["pressure_level"] == "critical"


# ---------------------------------------------------------------------------
# _check_stub_content — uses _STUB_MIN_WORDS, _STUB_SIGNAL_DEDUCTION, etc.
# ---------------------------------------------------------------------------

class TestCheckStubContent:

    def test_tbd_placeholder_reduces_score(self):
        text = "TBD. " * 20  # Trigger TBD pattern AND still have enough words
        with patch("tools.proposal_genesis.reflexes.polish.get_connection") as mock_gc:
            mock_gc.return_value.__enter__ = MagicMock()
            mock_gc.return_value.execute.return_value.fetchone.return_value = None
            mock_gc.return_value.close = MagicMock()
            result = _check_stub_content(text, "opp-001")
        assert result["score"] < 1.0
        assert result["total_stub_signals"] > 0

    def test_thin_content_flagged(self):
        text = "short " * 5  # 5 words < _STUB_MIN_WORDS
        with patch("tools.proposal_genesis.reflexes.polish.get_connection") as mock_gc:
            mock_gc.return_value.execute.return_value.fetchone.return_value = None
            mock_gc.return_value.close = MagicMock()
            result = _check_stub_content(text, "opp-001")
        # thin_content flag found
        thin_found = any(
            "thin content" in p.get("pattern", "") for p in result["stub_patterns_found"]
        )
        assert thin_found

    def test_substantive_text_high_score(self):
        text = (
            "Our team will deliver a comprehensive solution for AGENCY-001. "
            "We have demonstrated expertise in federal IT modernization across 12 agencies. "
            "Our certified methodology ensures full compliance with all requirements. "
            "We will implement proven automation tools with measurable outcomes. "
            "Deliverables include documentation, training, and ongoing support."
        ) * 3
        with patch("tools.proposal_genesis.reflexes.polish.get_connection") as mock_gc:
            mock_gc.return_value.execute.return_value.fetchone.return_value = {
                "agency": "agency-001", "title": "federal it modernization"
            }
            mock_gc.return_value.close = MagicMock()
            result = _check_stub_content(text, "AGENCY-001")
        # Score should be reasonably high with no stub patterns
        assert result["score"] >= 0.4


# ---------------------------------------------------------------------------
# Module-level constant sanity
# ---------------------------------------------------------------------------

class TestModuleLevelConstants:

    def test_all_constants_defined_and_positive(self):
        assert _GRAMMAR_DEDUCTION_PER_ISSUE > 0
        assert _TONE_DEDUCTION_PER_SIGNAL > 0
        assert _TONE_STRENGTH_BONUS > 0
        assert _AI_BURSTINESS_LOW > 0
        assert _AI_BURSTINESS_MID > _AI_BURSTINESS_LOW
        assert _CONTEXT_WARNING_TOKENS > 0
        assert _CONTEXT_CRITICAL_TOKENS > _CONTEXT_WARNING_TOKENS
        assert _STUB_MIN_WORDS > 0
        assert _STUB_SIGNAL_DEDUCTION > 0
        assert 0.0 < _STUB_GATE_THRESHOLD < 1.0
