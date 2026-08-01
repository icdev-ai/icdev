#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for shx-safe-02 / shx-safe-03 — zero-trust stubs fail closed.

Covers:
  1. stub_allowed() truthy-value matrix (ICDEV_ZT_ALLOW_STUB).
  2. device_trust: CrowdStrike stub -> status "unknown"; enforcement denies
     with the flag unset, permits under the flag; live posture unaffected.
  3. device_compliance_scanner: unknown posture -> non-compliant with the flag
     unset, scans normally under the flag.
  4. pdp_client: DISA ICAM / Zscaler ZPA stubs deny with the flag unset,
     honored under the flag; live (local/ABAC) adapter path unaffected.
"""
from __future__ import annotations

import sqlite3

import pytest

import tools.security.stub_gate as _gate
import tools.security.device_trust as _dt
import tools.security.pdp_client as _pdp


# ---------------------------------------------------------------------------
# 1. stub_allowed() truthy-value matrix
# ---------------------------------------------------------------------------

class TestStubAllowedMatrix:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES", "Yes"])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", value)
        assert _gate.stub_allowed() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  ", "enabled", "2"])
    def test_non_truthy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", value)
        assert _gate.stub_allowed() is False

    def test_unset_disables(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        assert _gate.stub_allowed() is False

    def test_whitespace_padded_truthy(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "  yes  ")
        assert _gate.stub_allowed() is True


# ---------------------------------------------------------------------------
# 2. device_trust — CrowdStrike stub reports "unknown" + fails closed
# ---------------------------------------------------------------------------

def _configure_crowdstrike(monkeypatch):
    """Set dummy credentials so _call_crowdstrike_api reaches the stub
    (rather than raising 'not configured')."""
    monkeypatch.setenv("ICDEV_CROWDSTRIKE_BASE_URL", "https://falcon.example.mil")
    monkeypatch.setenv("ICDEV_CROWDSTRIKE_CLIENT_ID", "dummy-id")
    monkeypatch.setenv("ICDEV_CROWDSTRIKE_CLIENT_SECRET", "dummy-secret")


class TestDeviceTrustStub:
    def test_stub_returns_status_unknown_never_healthy(self, monkeypatch):
        _configure_crowdstrike(monkeypatch)
        posture = _dt._call_crowdstrike_api("fp-stub")
        assert posture["status"] == "unknown"
        assert posture.get("_stub") is True
        # Never fabricate a healthy score.
        assert posture["health_score"] == 0.0

    def test_stub_denies_with_flag_unset(self, monkeypatch):
        _dt._cache.clear()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        _configure_crowdstrike(monkeypatch)

        result = _dt.verify_device_posture("fp-deny")
        assert result.trusted is False
        assert result.status == "unknown"
        assert "fail closed" in result.reason

    def test_stub_permits_under_flag(self, monkeypatch):
        _dt._cache.clear()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        _configure_crowdstrike(monkeypatch)

        result = _dt.verify_device_posture("fp-permit")
        assert result.trusted is True
        assert result.status == "unknown"
        assert "ICDEV_ZT_ALLOW_STUB" in result.reason

    def test_live_healthy_posture_unaffected_by_flag(self, monkeypatch):
        """A real (non-stub) healthy posture is trusted regardless of the flag."""
        _dt._cache.clear()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", lambda fp: {
            "device_id": "d-live",
            "health_score": 0.95,
            "last_seen_seconds_ago": 120,
            "sensor_version": "7.14.0",
        })

        result = _dt.verify_device_posture("fp-live")
        assert result.trusted is True
        assert result.status == "healthy"

    def test_status_included_in_to_dict(self):
        r = _dt.DeviceTrustResult(trusted=False, status="unknown")
        assert r.to_dict()["status"] == "unknown"


# ---------------------------------------------------------------------------
# 3. device_compliance_scanner — unknown posture fails closed
# ---------------------------------------------------------------------------

import tools.security_canvas.device_compliance_scanner as _dcs  # noqa: E402
from tools.db.storage import StorageConnection  # noqa: E402


def _fresh_mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return StorageConnection(conn, "sqlite")


class TestDeviceComplianceScanner:
    def test_unknown_posture_denies_with_flag_unset(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        monkeypatch.setattr(_dcs, "get_connection", _fresh_mem_conn)
        monkeypatch.setattr(
            _dcs, "verify_device_posture",
            lambda device_id: _dt.DeviceTrustResult(
                trusted=False, device_id=device_id, health_score=0.0, status="unknown",
                reason="stub",
            ),
        )

        result = _dcs.scan_device("host-unknown.example.mil")
        assert result["overall_pass"] is False
        assert result["compliance_score"] == 0.0
        assert result["health_score"] == 0.0
        assert any("UNKNOWN" in g for g in result["gaps"])

    def test_unknown_posture_scans_under_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")
        monkeypatch.setattr(_dcs, "get_connection", _fresh_mem_conn)
        monkeypatch.setattr(
            _dcs, "verify_device_posture",
            lambda device_id: _dt.DeviceTrustResult(
                trusted=True, device_id=device_id, health_score=0.0, status="unknown",
                last_seen_seconds_ago=300, reason="stub permit",
            ),
        )

        result = _dcs.scan_device("host-dev.example.mil")
        # Under the flag the scanner falls back to its dev score (0.75) and
        # the unknown-posture gap is not injected.
        assert result["health_score"] == 0.75
        assert not any("UNKNOWN" in g for g in result["gaps"])
        assert result["overall_pass"] is True

    def test_live_healthy_posture_scans_normally(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        monkeypatch.setattr(_dcs, "get_connection", _fresh_mem_conn)
        monkeypatch.setattr(
            _dcs, "verify_device_posture",
            lambda device_id: _dt.DeviceTrustResult(
                trusted=True, device_id=device_id, health_score=0.92, status="healthy",
                last_seen_seconds_ago=120, reason="posture checks passed",
            ),
        )

        result = _dcs.scan_device("host-live.example.mil")
        assert result["health_score"] == 0.92
        assert result["overall_pass"] is True
        assert not any("UNKNOWN" in g for g in result["gaps"])


# ---------------------------------------------------------------------------
# 4. pdp_client — stub adapters fail closed; live adapter unaffected
# ---------------------------------------------------------------------------

class TestPDPStubAdapters:
    def test_disa_icam_denies_with_flag_unset(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        decision = _pdp.DisaICAMAdapter().evaluate({}, "res", "read")
        assert decision.permit is False
        assert decision.adapter == "disa_icam"
        assert "fail closed" in decision.reason

    def test_disa_icam_permits_under_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "true")
        decision = _pdp.DisaICAMAdapter().evaluate({}, "res", "read")
        assert decision.permit is True
        assert "ICDEV_ZT_ALLOW_STUB" in decision.reason

    def test_zscaler_zpa_denies_with_flag_unset(self, monkeypatch):
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        decision = _pdp.ZscalerZPAAdapter().evaluate({}, "res", "read")
        assert decision.permit is False
        assert decision.adapter == "zscaler_zpa"
        assert "fail closed" in decision.reason

    def test_zscaler_zpa_permits_under_flag(self, monkeypatch):
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "yes")
        decision = _pdp.ZscalerZPAAdapter().evaluate({}, "res", "read")
        assert decision.permit is True
        assert "ICDEV_ZT_ALLOW_STUB" in decision.reason

    def test_evaluate_access_stub_denies_by_default(self, monkeypatch):
        _pdp.clear_cache()
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)
        monkeypatch.setenv("ICDEV_PDP_ADAPTER", "disa_icam")
        decision = _pdp.evaluate_access({"user_id": "u1"}, "proj-1", "read")
        assert decision.permit is False


class TestPDPLiveAdapterUnaffected:
    def test_live_adapter_permit_honored_regardless_of_flag(self, monkeypatch):
        """A live adapter that permits is honored even with the stub flag unset."""
        _pdp.clear_cache()
        monkeypatch.delenv("ICDEV_ZT_ALLOW_STUB", raising=False)

        class LivePermitAdapter(_pdp.PDPAdapter):
            def evaluate(self, user_ctx, resource, action):
                return _pdp.PDPDecision(permit=True, reason="live policy match", adapter="local")

        monkeypatch.setattr(_pdp, "_build_pdp_adapter", lambda: LivePermitAdapter())
        decision = _pdp.evaluate_access({"user_id": "u1"}, "res", "read")
        assert decision.permit is True
        assert decision.reason == "live policy match"

    def test_live_adapter_deny_honored(self, monkeypatch):
        _pdp.clear_cache()
        monkeypatch.setenv("ICDEV_ZT_ALLOW_STUB", "1")  # flag on must not force permit

        class LiveDenyAdapter(_pdp.PDPAdapter):
            def evaluate(self, user_ctx, resource, action):
                return _pdp.PDPDecision(permit=False, reason="live policy deny", adapter="local")

        monkeypatch.setattr(_pdp, "_build_pdp_adapter", lambda: LiveDenyAdapter())
        decision = _pdp.evaluate_access({"user_id": "u1"}, "res", "read")
        assert decision.permit is False
