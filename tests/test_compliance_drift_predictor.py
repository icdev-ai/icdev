# CUI // SP-CTI
"""Tests for PNA Compliance Drift Predictor (pna-cmp-03)."""
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
# All compliance_drift functions use `conn = get_connection()` (not context mgr)
# ---------------------------------------------------------------------------

def _make_conn(fetchall_rows=None, fetchone_val=None):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 1
    cursor.fetchall.return_value = fetchall_rows or []
    cursor.fetchone.return_value = fetchone_val  # None → _get_previous_score returns None
    cursor.description = [("col",)]
    conn.execute.return_value = cursor
    conn.commit = MagicMock()
    return conn


def _nqe_result(rows):
    return {"rows": rows, "source": "nqe_api", "error": None}


# ---------------------------------------------------------------------------
# Unit tests: _run_stig_checks
# 10 checks; score = passing_count / 10.
# Empty config → 0 pass (all 10 require positive evidence now)
# V-220519: "ip ssh version 2" in c AND "service telnet" not in c
# V-220521: "ip http secure-server" in c OR "no ip http server" in c
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


def test_http_server_check_fails_when_absent():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    config = "ip http server\nip ssh version 2"  # no secure-server, no "no ip http server"
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
    # Empty config: all checks require positive evidence, so score = 0.0
    score, failing, critical_failing, details = _run_stig_checks("")
    assert score == 0.0
    assert critical_failing >= 1


def test_risk_score_within_bounds():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    config = "logging buffered"
    score, failing, critical_failing, details = _run_stig_checks(config)
    assert 0.0 <= score <= 1.0


def test_critical_controls_failing_counted():
    from tools.network.compliance_drift_predictor import _run_stig_checks
    # no SSH v2, has telnet, has http server (not secure) → ≥2 critical checks fail
    config = "service telnet\nip http server\nlogging buffered"
    _score, _failing, critical_failing, details = _run_stig_checks(config)
    assert critical_failing >= 2


# ---------------------------------------------------------------------------
# Integration tests: predict_compliance_drift
# Uses conn = get_connection() directly — patch return_value, not ctx manager
# ---------------------------------------------------------------------------

def test_predict_compliance_drift_empty_nqe():
    conn = _make_conn()
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.compliance_drift_predictor.get_connection", return_value=conn):
        MockClient.return_value.run_query.return_value = _nqe_result([])
        from tools.network.compliance_drift_predictor import predict_compliance_drift
        result = predict_compliance_drift()
        assert isinstance(result, dict)
        assert result["devices_assessed"] == 0
        assert result["predictions"] == []


def test_predict_compliance_drift_returns_keys():
    device = {"name": "rtr-01", "config": "ip ssh version 2\nlogging buffered"}
    conn = _make_conn(fetchone_val=None)  # None → no previous score
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.compliance_drift_predictor.get_connection", return_value=conn):
        MockClient.return_value.run_query.return_value = _nqe_result([device])
        from tools.network.compliance_drift_predictor import predict_compliance_drift
        result = predict_compliance_drift()
        assert "devices_assessed" in result
        assert "predictions" in result
        assert result["devices_assessed"] >= 1


def test_predict_compliance_drift_no_previous_drift_delta_zero():
    device = {"name": "rtr-01", "config": "ip ssh version 2\nlogging buffered"}
    conn = _make_conn(fetchone_val=None)  # None → last_score=None → drift_delta=0.0
    with patch("tools.network.nqe_client.FallbackNQEClient") as MockClient, \
         patch("tools.network.compliance_drift_predictor.get_connection", return_value=conn):
        MockClient.return_value.run_query.return_value = _nqe_result([device])
        from tools.network.compliance_drift_predictor import predict_compliance_drift
        result = predict_compliance_drift()
        if result["predictions"]:
            pred = result["predictions"][0]
            assert pred["drift_delta"] == 0.0


# ---------------------------------------------------------------------------
# Integration tests: get_compliance_drift / get_compliance_summary
# Both use conn = get_connection() directly
# ---------------------------------------------------------------------------

def test_get_compliance_drift_device_filter():
    conn = _make_conn()
    with patch("tools.network.compliance_drift_predictor.get_connection", return_value=conn):
        from tools.network.compliance_drift_predictor import get_compliance_drift
        get_compliance_drift(device_name="rtr-01")
        sql = conn.execute.call_args[0][0]
        assert "device_name" in sql


def test_get_compliance_summary_returns_keys():
    conn = _make_conn(fetchone_val=(0, None))
    with patch("tools.network.compliance_drift_predictor.get_connection", return_value=conn):
        from tools.network.compliance_drift_predictor import get_compliance_summary
        result = get_compliance_summary()
        assert "total_devices" in result
        assert "avg_compliance_score" in result
        assert "critical_drift_count" in result
