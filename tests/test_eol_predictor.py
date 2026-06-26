# CUI // SP-CTI
"""Tests for PNA Device EOL/EOS Risk Predictor (pna-eol-03)."""
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
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def _dev(name="rtr-01", vendor="cisco", model="ISR4321", os_ver="17.6.1"):
    return {"name": name, "vendor": vendor, "model": model, "osVersion": os_ver}


# ---------------------------------------------------------------------------
# Unit tests: _lookup_eos (returns tuple)
# ---------------------------------------------------------------------------

def test_lookup_eos_cisco_isr_known():
    from tools.network.eol_predictor import _lookup_eos
    eos_date, source = _lookup_eos("Cisco", "ISR4321")
    assert eos_date is not None
    assert source == "static_registry"


def test_lookup_eos_unknown_model_returns_none():
    from tools.network.eol_predictor import _lookup_eos
    eos_date, source = _lookup_eos("cisco", "UnknownModel-999")
    assert eos_date is None
    assert source == "local_heuristic"


def test_lookup_eos_unknown_vendor_returns_none():
    from tools.network.eol_predictor import _lookup_eos
    eos_date, source = _lookup_eos(None, "ISR4321")
    assert eos_date is None


def test_lookup_eos_juniper_mx80_known():
    from tools.network.eol_predictor import _lookup_eos
    eos_date, source = _lookup_eos("juniper", "MX80")
    assert eos_date == "2025-12-31"
    assert source == "static_registry"


def test_lookup_eos_empty_vendor_and_model():
    from tools.network.eol_predictor import _lookup_eos
    eos_date, source = _lookup_eos("", "")
    assert eos_date is None


# ---------------------------------------------------------------------------
# Unit tests: _score_device
# ---------------------------------------------------------------------------

def test_score_device_past_eos_high_base():
    from tools.network.eol_predictor import _score_device
    score, tier, days = _score_device("2020-01-01", 0)
    assert score >= 0.70
    assert tier in ("critical", "high")
    assert days < 0


def test_score_device_near_eos_90_days():
    from tools.network.eol_predictor import _score_device
    from datetime import datetime, timezone, timedelta
    near_date = (datetime.now(timezone.utc).date() + timedelta(days=60)).isoformat()
    score, tier, days = _score_device(near_date, 0)
    assert score >= 0.55
    assert 0 < days <= 90


def test_score_device_cve_boost_increases_score():
    from tools.network.eol_predictor import _score_device
    from datetime import datetime, timezone, timedelta
    mid_date = (datetime.now(timezone.utc).date() + timedelta(days=200)).isoformat()
    score_no_cve, _, _ = _score_device(mid_date, 0)
    score_with_cve, _, _ = _score_device(mid_date, 5)
    assert score_with_cve > score_no_cve


def test_score_device_no_eos_date_base():
    from tools.network.eol_predictor import _score_device
    score, tier, days = _score_device(None, 0)
    assert score == pytest.approx(0.15, abs=0.01)
    assert days is None


def test_score_device_score_within_bounds():
    from tools.network.eol_predictor import _score_device
    from datetime import datetime, timezone, timedelta
    for days_to_eos in (-30, 60, 180, 365, 500):
        eos = (datetime.now(timezone.utc).date() + timedelta(days=days_to_eos)).isoformat()
        for cve in (0, 3, 10):
            score, tier, _ = _score_device(eos, cve)
            assert 0.0 <= score <= 1.0
            assert tier in ("critical", "high", "medium", "low")


# ---------------------------------------------------------------------------
# Integration tests: predict_eol_risk
# ---------------------------------------------------------------------------

def test_predict_eol_risk_empty_nqe_returns_empty_list():
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = []
        from tools.network.eol_predictor import predict_eol_risk
        result = predict_eol_risk()
        assert result == []


def test_predict_eol_risk_returns_list():
    devices = [_dev()]
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = devices
        from tools.network.eol_predictor import predict_eol_risk
        result = predict_eol_risk()
        assert isinstance(result, list)
        assert len(result) == 1


def test_predict_eol_risk_prediction_has_required_fields():
    devices = [_dev(vendor="cisco", model="ISR4321")]
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = devices
        from tools.network.eol_predictor import predict_eol_risk
        result = predict_eol_risk()
        pred = result[0]
        assert "device_name" in pred
        assert "risk_score" in pred
        assert "risk_tier" in pred
        assert "eos_date" in pred


def test_predict_eol_risk_multiple_devices():
    devices = [_dev("rtr-01", "cisco", "ISR4321"), _dev("rtr-02", "juniper", "MX80")]
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = devices
        from tools.network.eol_predictor import predict_eol_risk
        result = predict_eol_risk()
        assert len(result) == 2


def test_predict_eol_risk_device_name_filter():
    devices = [_dev("rtr-01"), _dev("rtr-02")]
    conn = _make_conn(fetchone_val=(0,))
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.db.init_db.get_connection", return_value=conn):
        MockClient.return_value.query.return_value = devices
        from tools.network.eol_predictor import predict_eol_risk
        result = predict_eol_risk(device_name="rtr-01")
        assert all(r["device_name"] == "rtr-01" for r in result)


# ---------------------------------------------------------------------------
# Integration tests: get_eol_predictions / get_eol_summary
# ---------------------------------------------------------------------------

def test_get_eol_predictions_risk_tier_filter():
    conn = _make_conn()
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.eol_predictor import get_eol_predictions
        get_eol_predictions(risk_tier="critical")
        sql = conn.execute.call_args[0][0]
        assert "risk_tier" in sql


def test_get_eol_summary_returns_correct_keys():
    conn = _make_conn(fetchall_rows=[], fetchone_val=(0,))
    with patch("tools.network.db.init_db.get_connection", return_value=conn):
        from tools.network.eol_predictor import get_eol_summary
        result = get_eol_summary()
        assert "total_devices" in result
        assert "by_tier" in result
        assert "already_eos" in result
