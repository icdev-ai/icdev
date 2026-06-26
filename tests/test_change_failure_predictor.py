# CUI // SP-CTI
"""Tests for PNA Change Failure Predictor (pna-chg-03)."""
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

def _make_conn(plan_rows=None, fetchone_val=None, fetchall_rows=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchall.return_value = fetchall_rows or []
    cursor.fetchone.return_value = fetchone_val
    conn.execute.return_value = cursor
    conn.close = MagicMock()
    return conn


def _plan_row(device="rtr-01", verdict="pass", blast_json="[]",
              scheduled="2026-07-01T02:00:00+00:00", batch="batch-1", plan="plan-1"):
    return {
        "id": 1, "plan_id": plan, "batch_id": batch, "advisory_id": 1,
        "device_name": device, "action": "patch",
        "scheduled_at": scheduled, "blast_radius_json": blast_json,
        "simulation_status": verdict,
    }


# ---------------------------------------------------------------------------
# Unit tests: _count_blast_radius
# ---------------------------------------------------------------------------

def test_count_blast_radius_empty():
    from tools.network.change_failure_predictor import _count_blast_radius
    assert _count_blast_radius("[]") == 0
    assert _count_blast_radius(None) == 0
    assert _count_blast_radius("invalid") == 0


def test_count_blast_radius_with_devices():
    from tools.network.change_failure_predictor import _count_blast_radius
    data = json.dumps([{"device": "a"}, {"device": "b"}, {"device": "c"}])
    assert _count_blast_radius(data) == 3


# ---------------------------------------------------------------------------
# Unit tests: _score_plan_row
# ---------------------------------------------------------------------------

def test_score_plan_row_fail_verdict_high_probability():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn(fetchone_val=MagicMock(__getitem__=lambda s, k: None))
    conn.execute.return_value.fetchone.return_value = None
    row = _plan_row(verdict="fail", blast_json="[]")
    result = _score_plan_row(conn, row)
    assert result["failure_probability"] >= 0.60
    assert result["risk_tier"] in ("critical", "high")


def test_score_plan_row_pass_verdict_low_probability():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn()
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    row = _plan_row(verdict="pass", blast_json="[]")
    result = _score_plan_row(conn, row)
    assert result["failure_probability"] < 0.80


def test_score_plan_row_large_blast_radius_increases_probability():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn()
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    big_blast = json.dumps([{"device": f"d{i}"} for i in range(15)])
    row_small = _plan_row(verdict="pass", blast_json="[]")
    row_big = _plan_row(verdict="pass", blast_json=big_blast)
    score_small = _score_plan_row(conn, row_small)["failure_probability"]
    score_big = _score_plan_row(conn, row_big)["failure_probability"]
    assert score_big > score_small


def test_score_plan_row_probability_capped_at_1():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn()
    fetchone = MagicMock()
    fetchone.__getitem__ = lambda s, k: 10
    fetchone.__bool__ = lambda s: True
    fetchone.__iter__ = lambda s: iter([10])
    conn.execute.return_value.fetchone.return_value = fetchone
    big_blast = json.dumps([{"device": f"d{i}"} for i in range(20)])
    row = _plan_row(verdict="fail", blast_json=big_blast)
    result = _score_plan_row(conn, row)
    assert result["failure_probability"] <= 1.0


def test_score_plan_row_risk_factors_json_valid():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn()
    conn.execute.return_value.fetchone.return_value = None
    conn.execute.return_value.fetchall.return_value = []
    row = _plan_row(verdict="warn")
    result = _score_plan_row(conn, row)
    factors = json.loads(result["risk_factors_json"])
    assert "simulation_verdict" in factors
    assert "blast_radius_devices" in factors
    assert "concurrent_changes" in factors


def test_score_plan_row_risk_tier_mapping():
    from tools.network.change_failure_predictor import _risk_tier
    assert _risk_tier(0.90) == "critical"
    assert _risk_tier(0.70) == "high"
    assert _risk_tier(0.50) == "medium"
    assert _risk_tier(0.20) == "low"


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def test_predict_change_failure_no_plans():
    with patch("tools.network.change_failure_predictor.get_connection") as mock_gc:
        conn = _make_conn()
        conn.execute.return_value.fetchall.return_value = []
        mock_gc.return_value = conn
        from tools.network.change_failure_predictor import predict_change_failure
        result = predict_change_failure()
        assert result["scored"] == 0
        assert "warning" in result


def test_get_change_risks_tier_filter():
    with patch("tools.network.change_failure_predictor.get_connection") as mock_gc:
        mock_gc.return_value = _make_conn()
        from tools.network.change_failure_predictor import get_change_risks
        get_change_risks(risk_tier="critical")
        sql = mock_gc.return_value.execute.call_args[0][0]
        assert "risk_tier" in sql


def test_get_change_risk_summary_keys():
    with patch("tools.network.change_failure_predictor.get_connection") as mock_gc:
        mock_gc.return_value = _make_conn()
        from tools.network.change_failure_predictor import get_change_risk_summary
        result = get_change_risk_summary()
        assert "total_changes" in result
        assert "by_tier" in result
        assert "avg_failure_probability" in result
