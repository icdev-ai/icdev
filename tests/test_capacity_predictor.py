# CUI // SP-CTI
"""Tests for PNA Capacity/Bandwidth Predictor (pna-cap-03)."""
from __future__ import annotations

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
# ---------------------------------------------------------------------------

def _make_conn(fetchall_rows=None, fetchone_val=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchall.return_value = fetchall_rows or []
    cursor.fetchone.return_value = fetchone_val or (0,)
    cursor.description = [("col1",), ("col2",)]
    conn.execute.return_value = cursor
    conn.close = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def _iface_row(device="rtr-01", name="Gi0/0", iface_id="gi0",
               util=30.0, peak=45.0):
    return {"device": device, "name": name, "id": iface_id,
            "utilizationPct": util, "peakUtilizationPct": peak}


# ---------------------------------------------------------------------------
# Unit tests: _compute_capacity_score
# ---------------------------------------------------------------------------

def test_compute_capacity_score_low_util():
    from tools.network.capacity_predictor import _compute_capacity_score
    score, tier, conf = _compute_capacity_score(20.0, 0.0, None)
    assert score < 0.40
    assert tier in ("low", "medium")


def test_compute_capacity_score_90_pct_critical():
    from tools.network.capacity_predictor import _compute_capacity_score
    score, tier, conf = _compute_capacity_score(92.0, 0.0, None)
    assert score >= 0.80
    assert tier == "critical"


def test_compute_capacity_score_75_pct_high():
    from tools.network.capacity_predictor import _compute_capacity_score
    score, tier, conf = _compute_capacity_score(80.0, 0.0, None)
    assert score >= 0.60
    assert tier in ("critical", "high")


def test_compute_capacity_score_growing_trend_increases_score():
    from tools.network.capacity_predictor import _compute_capacity_score
    score_flat, _, _ = _compute_capacity_score(50.0, 0.0, None)
    score_grow, _, _ = _compute_capacity_score(50.0, 3.0, None)
    assert score_grow > score_flat


def test_compute_capacity_score_capped_at_1():
    from tools.network.capacity_predictor import _compute_capacity_score
    score, _, _ = _compute_capacity_score(100.0, 100.0, 0)
    assert score <= 1.0


def test_compute_capacity_score_zero_util():
    from tools.network.capacity_predictor import _compute_capacity_score
    score, tier, conf = _compute_capacity_score(0.0, 0.0, None)
    assert score < 0.20
    assert tier == "low"


def test_compute_capacity_score_returns_valid_confidence():
    from tools.network.capacity_predictor import _compute_capacity_score
    for util in (10.0, 50.0, 90.0):
        score, tier, conf = _compute_capacity_score(util, 0.0, None)
        assert 0.0 <= conf <= 1.0


def test_compute_capacity_score_tier_mapping():
    from tools.network.capacity_predictor import _compute_capacity_score
    _, tier, _ = _compute_capacity_score(95.0, 0.0, None)
    assert tier == "critical"
    _, tier, _ = _compute_capacity_score(78.0, 0.0, None)
    assert tier in ("critical", "high")
    _, tier, _ = _compute_capacity_score(0.0, 0.0, None)
    assert tier == "low"


# ---------------------------------------------------------------------------
# Integration tests: predict_capacity_exhaustion
# ---------------------------------------------------------------------------

def test_predict_capacity_exhaustion_empty_nqe():
    conn = _make_conn(fetchall_rows=[])
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = []
        from tools.network.capacity_predictor import predict_capacity_exhaustion
        result = predict_capacity_exhaustion()
        assert result == []


def test_predict_capacity_exhaustion_returns_list():
    ifaces = [_iface_row(util=85.0)]
    conn = _make_conn(fetchall_rows=[])
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = ifaces
        from tools.network.capacity_predictor import predict_capacity_exhaustion
        result = predict_capacity_exhaustion()
        assert isinstance(result, list)
        assert len(result) == 1


def test_predict_capacity_exhaustion_prediction_fields():
    ifaces = [_iface_row(util=90.0)]
    conn = _make_conn(fetchall_rows=[])
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = ifaces
        from tools.network.capacity_predictor import predict_capacity_exhaustion
        result = predict_capacity_exhaustion()
        pred = result[0]
        assert "risk_score" in pred
        assert "risk_tier" in pred
        assert "device_name" in pred
        assert "interface_name" in pred
        assert "current_util_pct" in pred


def test_predict_capacity_exhaustion_device_filter():
    ifaces = [_iface_row("rtr-01"), _iface_row("rtr-02")]
    conn = _make_conn(fetchall_rows=[])
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = ifaces
        from tools.network.capacity_predictor import predict_capacity_exhaustion
        result = predict_capacity_exhaustion(device_name="rtr-01")
        assert all(r["device_name"] == "rtr-01" for r in result)


# ---------------------------------------------------------------------------
# Integration tests: get_capacity_predictions / get_capacity_summary
# ---------------------------------------------------------------------------

def test_get_capacity_predictions_risk_tier_filter():
    conn = _make_conn()
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.capacity_predictor import get_capacity_predictions
        get_capacity_predictions(risk_tier="critical")
        sql = conn.execute.call_args[0][0]
        assert "risk_tier" in sql


def test_get_capacity_summary_keys():
    conn = _make_conn(fetchall_rows=[], fetchone_val=(0,))
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.capacity_predictor import get_capacity_summary
        result = get_capacity_summary()
        assert "total_interfaces" in result
        assert "saturating_within_30d" in result
        assert "by_tier" in result
