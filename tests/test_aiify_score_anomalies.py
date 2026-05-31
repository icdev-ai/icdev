# CUI // SP-CTI
"""Tests for detect_score_anomalies() in tools.aiify.engine.

Validates that score anomaly detection uses config-driven thresholds and
correctly flags value/feasibility imbalance and component outliers.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.aiify.engine import detect_score_anomalies

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
