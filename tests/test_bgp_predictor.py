# CUI // SP-CTI
"""Tests for PNA BGP Stability Predictor (pna-bgp-03)."""
from __future__ import annotations

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

def _make_conn(fetchall_rows=None, fetchone_val=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchall.return_value = fetchall_rows or []
    cursor.fetchone.return_value = fetchone_val or (0,)
    cursor.description = [("col",)]
    conn.execute.return_value = cursor
    conn.close = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def _session_row(device="rtr-01", peer="10.0.0.2", asn=65001,
                 state="established", count=100):
    return {"device": device, "peerIp": peer, "peerAsn": asn,
            "state": state, "routeCount": count}


# ---------------------------------------------------------------------------
# Unit tests: _compute_bgp_score
# ---------------------------------------------------------------------------

def test_compute_bgp_score_stable_session():
    from tools.network.bgp_predictor import _compute_bgp_score
    stability, tier, outage_hrs, confidence = _compute_bgp_score(0, 0, "established")
    assert stability == pytest.approx(1.0, abs=0.01)
    assert tier == "low"
    assert outage_hrs is None


def test_compute_bgp_score_non_established_penalty():
    from tools.network.bgp_predictor import _compute_bgp_score
    stability_est, _, _, _ = _compute_bgp_score(0, 0, "established")
    stability_act, _, _, _ = _compute_bgp_score(0, 0, "active")
    assert stability_act < stability_est


def test_compute_bgp_score_critical_on_high_flaps():
    from tools.network.bgp_predictor import _compute_bgp_score
    stability, tier, outage_hrs, confidence = _compute_bgp_score(10, 30, "established")
    assert stability <= 0.30
    assert tier == "critical"
    assert outage_hrs is not None


def test_compute_bgp_score_outage_prediction():
    from tools.network.bgp_predictor import _compute_bgp_score
    _, _, outage_hrs, _ = _compute_bgp_score(flap_24h=8, flap_7d=30, session_state="established")
    assert outage_hrs is not None
    assert 0 < outage_hrs <= 24


def test_compute_bgp_score_confidence_grows_with_flaps():
    from tools.network.bgp_predictor import _compute_bgp_score
    _, _, _, conf_low = _compute_bgp_score(0, 0, "established")
    _, _, _, conf_high = _compute_bgp_score(5, 20, "established")
    assert conf_high > conf_low


def test_compute_bgp_score_stability_within_bounds():
    from tools.network.bgp_predictor import _compute_bgp_score
    for f24 in (0, 5, 15):
        for f7d in (0, 10, 40):
            stability, _, _, conf = _compute_bgp_score(f24, f7d, "established")
            assert 0.0 <= stability <= 1.0, f"stability={stability} out of bounds"
            assert 0.0 <= conf <= 1.0


def test_compute_bgp_score_7d_flap_drives_outage_estimate():
    from tools.network.bgp_predictor import _compute_bgp_score
    _, _, outage_hrs, _ = _compute_bgp_score(0, 10, "established")
    assert outage_hrs is not None


# ---------------------------------------------------------------------------
# Integration tests: predict_bgp_stability
# ---------------------------------------------------------------------------

def test_predict_bgp_stability_empty_nqe():
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = []
        from tools.network.bgp_predictor import predict_bgp_stability
        result = predict_bgp_stability()
        assert result == []


def test_predict_bgp_stability_returns_list():
    sessions = [_session_row()]
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = sessions
        from tools.network.bgp_predictor import predict_bgp_stability
        result = predict_bgp_stability()
        assert isinstance(result, list)
        assert len(result) == 1


def test_predict_bgp_stability_prediction_has_required_fields():
    sessions = [_session_row(state="active")]
    conn = _make_conn(fetchone_val=(2,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = sessions
        from tools.network.bgp_predictor import predict_bgp_stability
        result = predict_bgp_stability()
        assert len(result) == 1
        pred = result[0]
        assert "stability_score" in pred
        assert "flap_risk" in pred
        assert "confidence" in pred
        assert "device_name" in pred
        assert "session_key" in pred


def test_predict_bgp_stability_session_key_format():
    sessions = [_session_row(device="rtr-01", peer="10.0.0.2")]
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = sessions
        from tools.network.bgp_predictor import predict_bgp_stability
        result = predict_bgp_stability()
        key = result[0]["session_key"]
        assert "rtr-01" in key
        assert "10.0.0.2" in key


# ---------------------------------------------------------------------------
# Integration tests: get_bgp_predictions / get_bgp_summary
# ---------------------------------------------------------------------------

def test_get_bgp_predictions_flap_risk_filter():
    conn = _make_conn()
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.bgp_predictor import get_bgp_predictions
        get_bgp_predictions(flap_risk="critical")
        sql = conn.execute.call_args[0][0]
        assert "flap_risk" in sql


def test_get_bgp_predictions_device_filter():
    conn = _make_conn()
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.bgp_predictor import get_bgp_predictions
        get_bgp_predictions(device_name="rtr-01")
        sql = conn.execute.call_args[0][0]
        assert "device_name" in sql


def test_get_bgp_summary_returns_keys():
    conn = _make_conn(fetchall_rows=[], fetchone_val=(0,))
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.bgp_predictor import get_bgp_summary
        result = get_bgp_summary()
        assert "total_sessions" in result
        assert "at_risk_sessions" in result
        assert "by_tier" in result


# ---------------------------------------------------------------------------
# Unit tests: record_bgp_event
# ---------------------------------------------------------------------------

def test_record_bgp_event_returns_session_key():
    conn = _make_conn()
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.bgp_predictor import record_bgp_event
        key = record_bgp_event("rtr-01", "10.0.0.2", "flap")
        assert "rtr-01" in key
        assert "10.0.0.2" in key
