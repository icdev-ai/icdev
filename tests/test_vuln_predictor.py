# CUI // SP-CTI
"""Tests for PVM Vulnerability Risk Predictor (pvm-pred-03)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _adv(id=1, cvss=7.5, exploited="0", published="2025-01-01"):
    return {
        "id": id,
        "cve_id": f"CVE-2025-{id:04d}",
        "cvss_score": cvss,
        "exploited_in_wild": exploited,
        "published_date": published,
        "status": "open",
        "vendor": "cisco",
    }


def _assessment(id=1, advisory_id=1, impacted_count=5):
    return {"id": id, "advisory_id": advisory_id, "impacted_count": impacted_count, "created_at": "2025-06-01"}


def _mock_conn(adv_row=None, assessments=None, pred_row=None):
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)

    adv = adv_row or _adv()
    mock_adv = MagicMock()
    mock_adv.__iter__ = lambda s: iter(adv.items())
    mock_adv.keys = lambda: adv.keys()
    mock_adv.__getitem__ = lambda s, k: adv[k]
    mock_adv.get = lambda k, d=None: adv.get(k, d)

    def _fetchone_side(sql, params=None):
        result = MagicMock()
        if "nc_advisories" in sql and "WHERE id" in sql:
            result.__iter__ = lambda s: iter(adv.items())
            result.keys = lambda: adv.keys()
            result.__getitem__ = lambda s, k: adv[k]
            result.get = lambda k, d=None: adv.get(k, d)
            return result
        if "nc_vuln_predictions" in sql and "WHERE id" in sql:
            pred = pred_row or {}
            result.__iter__ = lambda s: iter(pred.items())
            result.keys = lambda: pred.keys()
            result.__getitem__ = lambda s, k: pred[k]
            return result
        return None

    def _fetchall_side(sql, params=None):
        if "nc_advisory_assessments" in sql:
            rows = assessments or []
            mocks = []
            for a in rows:
                m = MagicMock()
                m.__iter__ = lambda s, _a=a: iter(_a.items())
                m.keys = lambda _a=a: _a.keys()
                m.__getitem__ = lambda s, k, _a=a: _a[k]
                m.get = lambda k, d=None, _a=a: _a.get(k, d)
                mocks.append(m)
            return mocks
        if "nc_advisories" in sql and "status IN" in sql:
            m = MagicMock()
            m.__getitem__ = lambda s, k: adv["id"] if k == 0 else None
            return [m]
        if "nc_vuln_predictions" in sql:
            return []
        return []

    execute_mock = MagicMock()
    execute_mock.fetchone = MagicMock(side_effect=lambda: None)
    execute_mock.lastrowid = 42

    conn.execute = MagicMock(return_value=execute_mock)

    def _execute(sql, params=None):
        result = MagicMock()
        result.fetchone = MagicMock(side_effect=lambda: _fetchone_side(sql, params))
        result.fetchall = MagicMock(side_effect=lambda: _fetchall_side(sql, params))
        result.lastrowid = 42
        return result

    conn.execute.side_effect = _execute
    return conn


# ---------------------------------------------------------------------------
# Tests for _compute_scores (unit, no DB)
# ---------------------------------------------------------------------------

def test_exploit_weight_tier_exploited_in_wild():
    """exploited_in_wild='1' → exploit_weight=1.0."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv(cvss=5.0, exploited="1")
    scores = _compute_scores(adv, [])
    assert scores["exploit_weight"] == 1.0


def test_exploit_weight_tier_high_cvss():
    """cvss>=7 and not exploited → exploit_weight=0.5."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv(cvss=8.0, exploited="0")
    scores = _compute_scores(adv, [])
    assert scores["exploit_weight"] == 0.5


def test_exploit_weight_tier_low_cvss():
    """cvss<7 and not exploited → exploit_weight=0.1."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv(cvss=4.0, exploited="0")
    scores = _compute_scores(adv, [])
    assert scores["exploit_weight"] == 0.1


def test_composite_score_clipped_to_unit_interval():
    """composite risk score must always be in [0, 1]."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv(cvss=10.0, exploited="1", published="2020-01-01")
    scores = _compute_scores(adv, [_assessment(impacted_count=100)])
    assert 0.0 <= scores["risk_score_composite"] <= 1.0


def test_patch_lag_norm_capped_at_1():
    """Advisory published many years ago → patch_lag_norm=1.0."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv(published="2000-01-01")  # 25+ years ago
    scores = _compute_scores(adv, [])
    assert scores["patch_lag_norm"] == 1.0


def test_confidence_zero_assessments():
    """No assessment history → confidence=0.30."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv()
    scores = _compute_scores(adv, [])
    assert scores["confidence"] == 0.30


def test_confidence_one_assessment():
    """1 assessment → confidence=0.40."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv()
    scores = _compute_scores(adv, [_assessment()])
    assert scores["confidence"] == 0.40


def test_confidence_two_assessments():
    """2 assessments → confidence=0.60."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv()
    scores = _compute_scores(adv, [_assessment(id=1), _assessment(id=2)])
    assert scores["confidence"] == 0.60


def test_confidence_three_or_more_assessments():
    """3+ assessments → confidence=0.85."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv()
    assessments = [_assessment(id=i, impacted_count=i * 2) for i in range(1, 4)]
    scores = _compute_scores(adv, assessments)
    assert scores["confidence"] == 0.85


def test_model_version_field_set():
    """model_version must equal MODEL_VERSION constant."""
    from tools.network.vuln_predictor import _compute_scores, MODEL_VERSION
    adv = _adv()
    scores = _compute_scores(adv, [])
    assert scores["model_version"] == MODEL_VERSION


def test_trend_rising_when_impacted_increases():
    """Impacted count increasing → trend='rising'."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv()
    assessments = [
        _assessment(id=1, impacted_count=2),
        _assessment(id=2, impacted_count=10),
    ]
    scores = _compute_scores(adv, assessments)
    assert scores["trend"] == "rising"


def test_trend_stable_when_impacted_unchanged():
    """Impacted count unchanged → trend='stable'."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv()
    assessments = [
        _assessment(id=1, impacted_count=5),
        _assessment(id=2, impacted_count=5),
    ]
    scores = _compute_scores(adv, assessments)
    assert scores["trend"] == "stable"


def test_30d_and_90d_ge_composite_when_rising():
    """When trend is rising, risk_score_30d and risk_score_90d >= composite."""
    from tools.network.vuln_predictor import _compute_scores
    adv = _adv(cvss=7.5, exploited="0")
    assessments = [
        _assessment(id=1, impacted_count=1),
        _assessment(id=2, impacted_count=20),
    ]
    scores = _compute_scores(adv, assessments)
    if scores["trend"] == "rising":
        assert scores["risk_score_30d"] >= scores["risk_score_composite"]
        assert scores["risk_score_90d"] >= scores["risk_score_30d"]


def test_predict_advisory_risk_missing_table_returns_error():
    """Missing nc_vuln_predictions table returns error dict, not exception."""
    conn = MagicMock()

    def _execute(sql, params=None):
        result = MagicMock()
        if "nc_advisories" in sql:
            row = MagicMock()
            adv = _adv()
            row.__iter__ = lambda s: iter(adv.items())
            row.keys = lambda: adv.keys()
            row.__getitem__ = lambda s, k: adv[k]
            row.get = lambda k, d=None: adv.get(k, d)
            result.fetchone = MagicMock(return_value=row)
        elif "nc_advisory_assessments" in sql:
            result.fetchall = MagicMock(return_value=[])
        elif "nc_vuln_predictions" in sql and "INSERT" in sql:
            result.execute = MagicMock(side_effect=Exception("no such table: nc_vuln_predictions"))
            result.lastrowid = None
            raise Exception("no such table: nc_vuln_predictions")
        else:
            result.fetchone = MagicMock(return_value=None)
            result.fetchall = MagicMock(return_value=[])
        return result

    conn.execute.side_effect = _execute

    with patch("tools.network.vuln_predictor.get_connection", return_value=conn):
        from tools.network.vuln_predictor import predict_advisory_risk
        result = predict_advisory_risk(1)

    assert "error" in result
    assert "nc_vuln_predictions" in result["error"] or result.get("advisory_id") == 1
