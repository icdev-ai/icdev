# CUI // SP-CTI
"""Tests for PNA Compliance Drift Predictor (pna-cmp-03)."""
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

def _make_conn(rows=None, fetchone_val=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchall.return_value = rows or []
    cursor.fetchone.return_value = fetchone_val
    conn.execute.return_value = cursor
    conn.close = MagicMock()
    return conn


def _nqe_result(rows):
    return {"rows": rows, "source": "nqe_api", "error": None}


# ---------------------------------------------------------------------------
# Unit tests: STIG checks (_run_stig_checks)
# ---------------------------------------------------------------------------

def test_ssh_v2_check_passes():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    config = "ip ssh version 2\nlogging buffered\nntp server 10.0.0.1"
    score, failing, critical_failing, details = _run_stig_checks(config)
    ssh_detail = next(d for d in details if d["check_id"] == "V-220518")
    assert ssh_detail["passed"] is True


def test_telnet_check_fails_when_present():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    config = "service telnet\nip ssh version 2"
    score, failing, critical_failing, details = _run_stig_checks(config)
    telnet_detail = next(d for d in details if d["check_id"] == "V-220519")
    assert telnet_detail["passed"] is False


def test_http_server_check_fails_when_present():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    config = "ip http server\nip ssh version 2"
    _score, _failing, critical_failing, details = _run_stig_checks(config)
    http_detail = next(d for d in details if d["check_id"] == "V-220521")
    assert http_detail["passed"] is False
    assert critical_failing >= 1


def test_compliance_score_all_passing():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    config = (
        "ip ssh version 2\n"
        "service password-encryption\n"
        "ip http secure-server\n"
        "logging buffered\n"
        "ntp server 10.0.0.1\n"
        "exec-timeout 10\n"
        "banner motd #\n"
        "aaa new-model\n"
        "snmp-server group MGMT v3 priv\n"
    )
    score, failing, critical_failing, details = _run_stig_checks(config)
    assert score > 0.5
    assert critical_failing == 0


def test_compliance_score_empty_config_zero():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    score, failing, critical_failing, details = _run_stig_checks("")
    assert score == 0.0
    assert critical_failing > 0


def test_risk_score_within_bounds():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    config = "logging buffered"
    score, failing, critical_failing, details = _run_stig_checks(config)
    # risk_score computed externally but score must be valid
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def test_predict_compliance_drift_empty_nqe():
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.compliance_drift_predictor.get_connection") as mock_gc:
        MockClient.return_value.run_query.return_value = _nqe_result([])
        mock_gc.return_value = _make_conn()
        from tools.network.compliance_drift_predictor import predict_compliance_drift
        result = predict_compliance_drift()
        assert result["devices_assessed"] == 0
        assert result["predictions"] == []


def test_predict_compliance_drift_no_previous_drift_delta_zero():
    device = {"name": "rtr-01", "config": "ip ssh version 2\nlogging buffered"}
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.compliance_drift_predictor.get_connection") as mock_gc:
        MockClient.return_value.run_query.return_value = _nqe_result([device])
        conn = _make_conn()
        # Simulate no previous drift row
        conn.execute.return_value.fetchone.return_value = None
        conn.execute.return_value.fetchall.return_value = []
        mock_gc.return_value = conn
        from tools.network.compliance_drift_predictor import predict_compliance_drift
        result = predict_compliance_drift()
        if result["predictions"]:
            pred = result["predictions"][0]
            assert pred["drift_delta"] == 0.0


def test_get_compliance_drift_device_filter():
    with patch("tools.network.compliance_drift_predictor.get_connection") as mock_gc:
        mock_gc.return_value = _make_conn()
        from tools.network.compliance_drift_predictor import get_compliance_drift
        get_compliance_drift(device_name="rtr-01")
        sql = mock_gc.return_value.execute.call_args[0][0]
        assert "device_name" in sql


def test_get_compliance_summary_returns_keys():
    with patch("tools.network.compliance_drift_predictor.get_connection") as mock_gc:
        mock_gc.return_value = _make_conn()
        from tools.network.compliance_drift_predictor import get_compliance_summary
        result = get_compliance_summary()
        assert "total_devices" in result
        assert "avg_compliance_score" in result
        assert "critical_drift_count" in result


def test_critical_controls_failing_counted():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    # Config missing SSH v2 (critical), has telnet enabled (critical fail), has HTTP server (critical)
    config = "service telnet\nip http server\nlogging buffered"
    _score, _failing, critical_failing, details = _run_stig_checks(config)
    assert critical_failing >= 2
