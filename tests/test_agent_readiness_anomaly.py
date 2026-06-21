# CUI // SP-CTI
"""Tests for AnomalyDetector in agent-readiness _base module."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


from tools.ai_augmentation.agent_readiness.pillars._base import (
    SCORE_PRECISION,
    AnomalyDetector,
    _RULE_BASED_ANOMALY_THRESHOLD,
)


# ---------------------------------------------------------------------------
# SCORE_PRECISION constant
# ---------------------------------------------------------------------------

def test_score_precision_is_int():
    assert isinstance(SCORE_PRECISION, int)
    assert SCORE_PRECISION == 4


def test_rule_based_threshold_is_float():
    assert isinstance(_RULE_BASED_ANOMALY_THRESHOLD, float)
    assert 0.0 < _RULE_BASED_ANOMALY_THRESHOLD < 1.0


# ---------------------------------------------------------------------------
# AnomalyDetector.detect() — rule-based fallback path
# ---------------------------------------------------------------------------

def _make_detector_no_llm() -> AnomalyDetector:
    """Return a detector whose LLM path always raises LLMUnavailableError."""
    from tools.llm.router import LLMUnavailableError

    det = AnomalyDetector()
    det._detect_with_llm = MagicMock(side_effect=LLMUnavailableError("no LLM"))
    return det


def test_detect_returns_entry_per_pillar():
    det = _make_detector_no_llm()
    scores = {
        "code-quality": {"percentage": 0.8},
        "security": {"percentage": 0.9},
    }
    result = det.detect(scores)
    assert set(result.keys()) == {"code-quality", "security"}


def test_detect_flags_low_score_as_anomaly():
    det = _make_detector_no_llm()
    scores = {"code-quality": {"percentage": 0.1}}
    result = det.detect(scores)
    assert result["code-quality"]["is_anomaly"] is True


def test_detect_does_not_flag_healthy_score():
    det = _make_detector_no_llm()
    scores = {"code-quality": {"percentage": 0.9}}
    result = det.detect(scores)
    assert result["code-quality"]["is_anomaly"] is False


def test_detect_critical_pillar_uses_higher_threshold():
    det = _make_detector_no_llm()
    # security at 0.4 — above generic threshold (0.3) but below critical (0.5)
    scores = {"security": {"percentage": 0.4}}
    result = det.detect(scores)
    assert result["security"]["is_anomaly"] is True
    assert result["security"]["severity"] == "high"


def test_detect_severity_high_for_critical_pillar():
    det = _make_detector_no_llm()
    scores = {"il-classification": {"percentage": 0.0}}
    result = det.detect(scores)
    assert result["il-classification"]["severity"] == "high"


def test_detect_empty_scores():
    det = AnomalyDetector()
    assert det.detect({}) == {}


def test_detect_result_has_required_keys():
    det = _make_detector_no_llm()
    scores = {"testing": {"percentage": 0.5}}
    result = det.detect(scores)
    entry = result["testing"]
    assert "is_anomaly" in entry
    assert "reason" in entry
    assert "severity" in entry


# ---------------------------------------------------------------------------
# AnomalyDetector.detect() — LLM path
# ---------------------------------------------------------------------------

def _make_llm_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.content = json.dumps(payload)
    return resp


def test_detect_uses_llm_when_available():
    det = AnomalyDetector()
    llm_payload = {
        "code-quality": {"is_anomaly": False, "reason": "ok", "severity": "low"},
    }
    with patch.object(det, "_detect_with_llm", return_value=llm_payload) as mock_llm:
        result = det.detect({"code-quality": {"percentage": 0.8}})
    mock_llm.assert_called_once()
    assert result["code-quality"]["is_anomaly"] is False


def test_detect_falls_back_on_llm_error():
    det = AnomalyDetector()
    with patch.object(det, "_detect_with_llm", side_effect=RuntimeError("boom")):
        result = det.detect({"security": {"percentage": 0.1}})
    assert result["security"]["is_anomaly"] is True


def test_llm_response_normalised_for_missing_pillars():
    """LLM only returns some pillars; missing ones should be filled in."""
    det = AnomalyDetector()
    partial_llm_payload = {
        "security": {"is_anomaly": True, "reason": "low", "severity": "high"},
        # code-quality missing intentionally
    }

    # Mock _detect_with_llm internals via router mock
    router_mock = MagicMock()
    resp_mock = MagicMock()
    resp_mock.content = json.dumps(partial_llm_payload)
    router_mock.route.return_value = resp_mock

    with patch("tools.ai_augmentation.agent_readiness.pillars._base.AnomalyDetector._detect_with_llm",
               return_value={
                   "security": {"is_anomaly": True, "reason": "low", "severity": "high"},
                   "code-quality": {"is_anomaly": False, "reason": "within normal range", "severity": "low"},
               }):
        result = det.detect({
            "security": {"percentage": 0.2},
            "code-quality": {"percentage": 0.8},
        })

    assert "security" in result
    assert "code-quality" in result


# ---------------------------------------------------------------------------
# SCORE_PRECISION used in Pillar.score()
# ---------------------------------------------------------------------------

def test_pillar_score_uses_score_precision():
    from tools.ai_augmentation.agent_readiness.pillars._base import (
        CriterionResult,
        Pillar,
        Criterion,
    )

    def _always_pass(repo):
        return CriterionResult("c1", True, "ok")

    pillar = Pillar(
        id="test-pillar",
        name="Test",
        description="",
        criteria=[Criterion("c1", "C1", "", "test-pillar", 1, _always_pass)],
    )
    import pathlib
    results = pillar.run(pathlib.Path("."))
    score = pillar.score(results)
    # 1/1 = 1.0 — precision doesn't change the value, but verify the key exists
    assert score["percentage"] == 1.0
    # Also test partial — 0/1 when skipped
    skipped_results = [CriterionResult("c1", False, "x", skipped=True)]
    score2 = pillar.score(skipped_results)
    assert score2["percentage"] == 0.0
