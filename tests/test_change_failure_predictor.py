# CUI // SP-CTI
"""Tests for PNA Change Failure Predictor (pna-chg-03)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Helpers
# predict_change_failure / get_change_risks / get_change_risk_summary all
# use conn = get_connection() directly (no context manager).
# _VERDICT_BASE = {"fail": 0.65, "warn": 0.40, "pass": 0.15, "skipped": 0.20}
# blast_factor = min(0.20, blast_count * 0.01)
# _risk_tier: ≥0.80→critical, ≥0.60→high, ≥0.40→medium, else→low
# ---------------------------------------------------------------------------

def _make_conn(fetchall_rows=None, fetchone_val=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchall.return_value = fetchall_rows or []
    cursor.fetchone.return_value = fetchone_val or (0,)
    cursor.description = [("col",)]
    conn.execute.return_value = cursor
    conn.commit = MagicMock()
    return conn


def _plan_row(device="rtr-01", verdict="pass", blast_json="[]", plan="plan-1"):
    return {
        "id": 1, "plan_id": plan, "advisory_id": 1,
        "device_name": device, "action": "patch",
        "scheduled_at": "2026-07-01T02:00:00+00:00",
        "blast_radius_json": blast_json,
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
# Unit tests: _score_plan_row  (uses conn directly, not context manager)
# ---------------------------------------------------------------------------

def test_score_plan_row_fail_verdict_high_probability():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn(fetchone_val=(0,))
    row = _plan_row(verdict="fail", blast_json="[]")
    result = _score_plan_row(conn, row)
    assert result["failure_probability"] >= 0.60
    assert result["risk_tier"] in ("critical", "high")


def test_score_plan_row_pass_verdict_low_probability():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn(fetchone_val=(0,))
    row = _plan_row(verdict="pass", blast_json="[]")
    result = _score_plan_row(conn, row)
    assert result["failure_probability"] < 0.50


def test_score_plan_row_large_blast_radius_increases_probability():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn(fetchone_val=(0,))
    big_blast = json.dumps([{"device": f"d{i}"} for i in range(25)])
    score_small = _score_plan_row(conn, _plan_row(verdict="pass", blast_json="[]"))["failure_probability"]
    score_big = _score_plan_row(conn, _plan_row(verdict="pass", blast_json=big_blast))["failure_probability"]
    assert score_big > score_small


def test_score_plan_row_probability_capped_at_1():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn(fetchone_val=(100,))
    big_blast = json.dumps([{"device": f"d{i}"} for i in range(100)])
    result = _score_plan_row(conn, _plan_row(verdict="fail", blast_json=big_blast))
    assert result["failure_probability"] <= 1.0


def test_score_plan_row_risk_factors_json_valid():
    from tools.network.change_failure_predictor import _score_plan_row
    conn = _make_conn(fetchone_val=(0,))
    result = _score_plan_row(conn, _plan_row(verdict="warn"))
    factors = json.loads(result["risk_factors_json"])
    assert "simulation_verdict" in factors
    assert "blast_radius_devices" in factors
    assert "concurrent_changes" in factors


def test_risk_tier_mapping():
    from tools.network.change_failure_predictor import _risk_tier
    assert _risk_tier(0.90) == "critical"
    assert _risk_tier(0.80) == "critical"
    assert _risk_tier(0.70) == "high"
    assert _risk_tier(0.60) == "high"
    assert _risk_tier(0.50) == "medium"
    assert _risk_tier(0.40) == "medium"
    assert _risk_tier(0.20) == "low"


# ---------------------------------------------------------------------------
# Integration tests: predict_change_failure
# Uses conn = get_connection() directly
# ---------------------------------------------------------------------------

def test_predict_change_failure_no_plans():
    conn = _make_conn(fetchall_rows=[])
    with patch("tools.network.change_failure_predictor.get_connection", return_value=conn):
        from tools.network.change_failure_predictor import predict_change_failure
        result = predict_change_failure()
        assert result["scored"] == 0
        assert "warning" in result


# ---------------------------------------------------------------------------
# Integration tests: get_change_risks / get_change_risk_summary
# Both use conn = get_connection() directly
# ---------------------------------------------------------------------------

def test_get_change_risks_tier_filter():
    conn = _make_conn()
    with patch("tools.network.change_failure_predictor.get_connection", return_value=conn):
        from tools.network.change_failure_predictor import get_change_risks
        get_change_risks(risk_tier="critical")
        sql = conn.execute.call_args[0][0]
        assert "risk_tier" in sql


def test_get_change_risk_summary_keys():
    call_count = [0]
    def side_effect(*args, **kwargs):
        call_count[0] += 1
        cur = MagicMock()
        cur.description = [("col",)]
        if call_count[0] == 1:
            cur.fetchall.return_value = []
        else:
            cur.fetchone.return_value = (0.0,)
        return cur
    conn = MagicMock()
    conn.execute.side_effect = side_effect
    conn.commit = MagicMock()
    with patch("tools.network.change_failure_predictor.get_connection", return_value=conn):
        from tools.network.change_failure_predictor import get_change_risk_summary
        result = get_change_risk_summary()
        assert "total_changes" in result
        assert "by_tier" in result
        assert "avg_failure_probability" in result
