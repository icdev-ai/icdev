# CUI // SP-CTI
"""Tests for detect_score_anomalies() in tools.ai_augmentation.engine.

Validates that score anomaly detection uses config-driven thresholds and
correctly flags value/feasibility imbalance and component outliers.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ai_augmentation.engine import calibrate_anomaly_thresholds, detect_score_anomalies

_THRESHOLDS = {
    "value_feasibility_max_delta": 0.50,
    "component_outlier_floor": 0.05,
    "component_outlier_ceiling": 0.95,
}


def _row(opp_id, value, feasibility, risk=0.5, composite=0.5):
    return {
        "opportunity_id": opp_id,
        "value_score": value,
        "feasibility_score": feasibility,
        "risk_score": risk,
        "composite_score": composite,
    }


# ---------------------------------------------------------------------------
# Empty / clean inputs
# ---------------------------------------------------------------------------

def test_no_rows_returns_empty():
    assert detect_score_anomalies([], _THRESHOLDS) == []


def test_balanced_scores_no_anomaly():
    rows = [_row(1, 0.6, 0.6, 0.5, 0.6)]
    assert detect_score_anomalies(rows, _THRESHOLDS) == []


# ---------------------------------------------------------------------------
# value_feasibility_imbalance
# ---------------------------------------------------------------------------

def test_vf_delta_at_threshold_not_flagged():
    rows = [_row(1, 0.9, 0.4)]  # delta == 0.50, boundary — NOT flagged (strict >)
    anomalies = detect_score_anomalies(rows, _THRESHOLDS)
    vf = [a for a in anomalies if a["anomaly_type"] == "value_feasibility_imbalance"]
    assert vf == []


def test_vf_delta_above_threshold_flagged():
    rows = [_row(1, 0.9, 0.3)]  # delta == 0.60 > 0.50
    anomalies = detect_score_anomalies(rows, _THRESHOLDS)
    vf = [a for a in anomalies if a["anomaly_type"] == "value_feasibility_imbalance"]
    assert len(vf) == 1
    assert vf[0]["opportunity_id"] == 1
    assert vf[0]["detail"]["delta"] == pytest.approx(0.6, abs=1e-4)


# ---------------------------------------------------------------------------
# component_outlier_low
# ---------------------------------------------------------------------------

def test_component_below_floor_flagged():
    rows = [_row(2, 0.04, 0.5)]  # value_score 0.04 < 0.05
    anomalies = detect_score_anomalies(rows, _THRESHOLDS)
    low = [a for a in anomalies if a["anomaly_type"] == "component_outlier_low"]
    assert any(a["detail"]["component"] == "value_score" for a in low)


def test_component_at_floor_not_flagged():
    rows = [_row(2, 0.05, 0.5)]  # value_score == floor — not flagged (strict <)
    anomalies = detect_score_anomalies(rows, _THRESHOLDS)
    low = [a for a in anomalies if a["anomaly_type"] == "component_outlier_low"
           and a["detail"].get("component") == "value_score"]
    assert low == []


# ---------------------------------------------------------------------------
# component_outlier_high
# ---------------------------------------------------------------------------

def test_composite_above_ceiling_flagged():
    rows = [_row(3, 0.5, 0.5, 0.5, 0.96)]  # composite 0.96 > 0.95
    anomalies = detect_score_anomalies(rows, _THRESHOLDS)
    high = [a for a in anomalies if a["anomaly_type"] == "component_outlier_high"]
    assert any(a["detail"]["component"] == "composite_score" for a in high)


# ---------------------------------------------------------------------------
# Threshold override via config
# ---------------------------------------------------------------------------

def test_custom_vf_threshold_respected():
    tight = {**_THRESHOLDS, "value_feasibility_max_delta": 0.10}
    rows = [_row(4, 0.7, 0.5)]  # delta 0.20 — fine at 0.50 but anomalous at 0.10
    anomalies = detect_score_anomalies(rows, tight)
    assert any(a["anomaly_type"] == "value_feasibility_imbalance" for a in anomalies)


def test_wide_threshold_suppresses_flag():
    wide = {**_THRESHOLDS, "value_feasibility_max_delta": 1.0}
    rows = [_row(5, 0.9, 0.1)]  # delta 0.80 — flagged at 0.50 but fine at 1.0
    anomalies = detect_score_anomalies(rows, wide)
    vf = [a for a in anomalies if a["anomaly_type"] == "value_feasibility_imbalance"]
    assert vf == []


# ---------------------------------------------------------------------------
# Multiple rows / multiple anomaly types
# ---------------------------------------------------------------------------

def test_multiple_rows_multiple_anomalies():
    rows = [
        _row(10, 0.9, 0.2),   # vf imbalance (delta 0.70)
        _row(11, 0.03, 0.5),  # component_outlier_low on value_score
        _row(12, 0.5, 0.5),   # clean
    ]
    anomalies = detect_score_anomalies(rows, _THRESHOLDS)
    ids = {a["opportunity_id"] for a in anomalies}
    assert 10 in ids
    assert 11 in ids
    assert 12 not in ids


# ---------------------------------------------------------------------------
# Fallback thresholds (no explicit thresholds arg)
# ---------------------------------------------------------------------------

def test_fallback_thresholds_used_when_none():
    # Should not raise; uses _FALLBACK_ANOMALY_THRESHOLDS internally
    rows = [_row(99, 0.5, 0.5, 0.5, 0.5)]
    result = detect_score_anomalies(rows)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# calibrate_anomaly_thresholds — AI-driven threshold calibration
# ---------------------------------------------------------------------------

def test_calibrate_returns_dict_when_disabled(monkeypatch):
    """With AI calibration disabled (default), returns config-based thresholds."""
    monkeypatch.delenv("ICDEV_AAC_AI_CALIBRATE", raising=False)
    result = calibrate_anomaly_thresholds([_row(1, 0.5, 0.5)])
    assert isinstance(result, dict)
    assert "value_feasibility_max_delta" in result
    assert "component_outlier_floor" in result
    assert "component_outlier_ceiling" in result


def test_calibrate_returns_dict_when_insufficient_rows(monkeypatch):
    """With fewer than 10 rows and AI enabled, falls back to config thresholds."""
    monkeypatch.setenv("ICDEV_AAC_AI_CALIBRATE", "true")
    rows = [_row(i, 0.5, 0.5) for i in range(5)]
    result = calibrate_anomaly_thresholds(rows)
    assert isinstance(result, dict)
    assert "value_feasibility_max_delta" in result


def test_calibrate_returns_dict_on_llm_error(monkeypatch):
    """When AI is enabled and LLM raises, falls back gracefully to config."""
    monkeypatch.setenv("ICDEV_AAC_AI_CALIBRATE", "true")
    rows = [_row(i, float(i) / 20, float(i) / 20) for i in range(10)]

    # Patch the extracted helper so no real LLM call is made.
    monkeypatch.setattr(
        "tools.ai_augmentation.engine._invoke_llm_for_calibration",
        lambda dist: None,
    )
    result = calibrate_anomaly_thresholds(rows)
    assert isinstance(result, dict)
    assert "value_feasibility_max_delta" in result


def test_calibrate_merges_valid_llm_response(monkeypatch):
    """When LLM returns valid JSON thresholds, they are merged into the result."""
    import json

    monkeypatch.setenv("ICDEV_AAC_AI_CALIBRATE", "true")
    rows = [_row(i, float(i) / 20, float(i) / 20) for i in range(10)]

    fake_raw = json.dumps({
        "value_feasibility_max_delta": 0.35,
        "component_outlier_floor": 0.03,
        "component_outlier_ceiling": 0.97,
    })
    monkeypatch.setattr(
        "tools.ai_augmentation.engine._invoke_llm_for_calibration",
        lambda dist: fake_raw,
    )
    result = calibrate_anomaly_thresholds(rows)
    assert result["value_feasibility_max_delta"] == pytest.approx(0.35)
    assert result["component_outlier_floor"] == pytest.approx(0.03)
    assert result["component_outlier_ceiling"] == pytest.approx(0.97)


def test_calibrate_rejects_out_of_range_llm_values(monkeypatch):
    """LLM values outside [0.0, 1.0] are rejected; base config values are kept."""
    import json

    monkeypatch.setenv("ICDEV_AAC_AI_CALIBRATE", "true")
    rows = [_row(i, float(i) / 20, float(i) / 20) for i in range(10)]

    fake_raw = json.dumps({
        "value_feasibility_max_delta": 5.0,   # invalid
        "component_outlier_floor": -0.1,       # invalid
        "component_outlier_ceiling": 0.90,     # valid
    })
    monkeypatch.setattr(
        "tools.ai_augmentation.engine._invoke_llm_for_calibration",
        lambda dist: fake_raw,
    )
    result = calibrate_anomaly_thresholds(rows)
    # Invalid values fall back to whatever config/fallback provides
    assert result["value_feasibility_max_delta"] != pytest.approx(5.0)
    assert result["component_outlier_floor"] != pytest.approx(-0.1)
    # Valid value accepted
    assert result["component_outlier_ceiling"] == pytest.approx(0.90)
