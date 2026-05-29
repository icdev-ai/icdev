#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for G-15 — Device Trust (tools/security/device_trust.py)."""

import time
import pytest

import tools.security.device_trust as _dt


def _clear_cache():
    _dt._cache.clear()


# ---------------------------------------------------------------------------
# DeviceTrustResult dataclass
# ---------------------------------------------------------------------------

class TestDeviceTrustResult:
    def test_to_dict_fields(self):
        r = _dt.DeviceTrustResult(
            trusted=True,
            device_id="abc123",
            health_score=0.95,
            last_seen_seconds_ago=300,
            sensor_version="7.14.0",
            reason="posture checks passed",
        )
        d = r.to_dict()
        assert d["trusted"] is True
        assert d["device_id"] == "abc123"
        assert d["health_score"] == 0.95
        assert d["last_seen_seconds_ago"] == 300
        assert d["sensor_version"] == "7.14.0"
        assert d["reason"] == "posture checks passed"

    def test_defaults(self):
        r = _dt.DeviceTrustResult(trusted=False)
        assert r.device_id == ""
        assert r.health_score == 0.0
        assert r.last_seen_seconds_ago == 0
        assert r.sensor_version == ""
        assert r.reason == ""


# ---------------------------------------------------------------------------
# verify_device_posture — device trust not required (default)
# ---------------------------------------------------------------------------

class TestDeviceTrustNotRequired:
    def test_not_required_always_trusts(self, monkeypatch):
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "false")
        result = _dt.verify_device_posture("fp-deadbeef")
        assert result.trusted is True
        assert "not required" in result.reason

    def test_not_required_skips_api(self, monkeypatch):
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "false")
        called = []

        def never_called(fp):
            called.append(fp)
            return {}

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", never_called)
        _dt.verify_device_posture("fp-test")
        assert called == []


# ---------------------------------------------------------------------------
# verify_device_posture — API not configured (RuntimeError)
# ---------------------------------------------------------------------------

class TestDeviceTrustAPINotConfigured:
    def test_not_configured_strict_returns_untrusted(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")
        monkeypatch.delenv("ICDEV_CROWDSTRIKE_BASE_URL", raising=False)
        monkeypatch.delenv("ICDEV_CROWDSTRIKE_CLIENT_ID", raising=False)
        monkeypatch.delenv("ICDEV_CROWDSTRIKE_CLIENT_SECRET", raising=False)

        result = _dt.verify_device_posture("fp-unconfigured")
        assert result.trusted is False
        assert result.reason == "API error"


# ---------------------------------------------------------------------------
# verify_device_posture — mocked posture data
# ---------------------------------------------------------------------------

class TestDeviceTrustPostureEvaluation:
    def test_healthy_device_is_trusted(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", lambda fp: {
            "device_id": "d-healthy",
            "health_score": 0.95,
            "last_seen_seconds_ago": 300,
            "sensor_version": "7.14.0",
        })

        result = _dt.verify_device_posture("fp-healthy")
        assert result.trusted is True
        assert result.health_score == 0.95
        assert result.last_seen_seconds_ago == 300
        assert "passed" in result.reason

    def test_low_health_score_is_untrusted(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", lambda fp: {
            "device_id": "d-sick",
            "health_score": 0.4,
            "last_seen_seconds_ago": 100,
            "sensor_version": "7.0.0",
        })

        result = _dt.verify_device_posture("fp-sick")
        assert result.trusted is False
        assert "health_score 0.40 < 0.7" in result.reason

    def test_stale_device_is_untrusted(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", lambda fp: {
            "device_id": "d-stale",
            "health_score": 0.9,
            "last_seen_seconds_ago": 90000,  # > 86400
            "sensor_version": "7.14.0",
        })

        result = _dt.verify_device_posture("fp-stale")
        assert result.trusted is False
        assert "last_seen" in result.reason

    def test_boundary_health_score_07_trusted(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", lambda fp: {
            "device_id": "d-boundary",
            "health_score": 0.7,
            "last_seen_seconds_ago": 86399,
            "sensor_version": "7.0.0",
        })

        result = _dt.verify_device_posture("fp-boundary")
        assert result.trusted is True


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class TestDeviceTrustCaching:
    def test_cache_hit_skips_api(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_CACHE_TTL_SECONDS", "300")

        call_count = [0]

        def counting_api(fp):
            call_count[0] += 1
            return {
                "device_id": "d-cached",
                "health_score": 0.9,
                "last_seen_seconds_ago": 100,
                "sensor_version": "7.0.0",
            }

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", counting_api)

        _dt.verify_device_posture("fp-for-cache")
        _dt.verify_device_posture("fp-for-cache")
        assert call_count[0] == 1  # second call from cache

    def test_different_fingerprints_separate_cache_entries(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")

        call_count = [0]

        def counting_api(fp):
            call_count[0] += 1
            return {
                "device_id": f"d-{fp[:8]}",
                "health_score": 0.9,
                "last_seen_seconds_ago": 50,
                "sensor_version": "7.0.0",
            }

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", counting_api)

        _dt.verify_device_posture("fp-alpha")
        _dt.verify_device_posture("fp-beta")
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# Unexpected exception handling
# ---------------------------------------------------------------------------

class TestDeviceTrustUnexpectedError:
    def test_unexpected_error_strict_untrusted(self, monkeypatch):
        _clear_cache()
        monkeypatch.setenv("ICDEV_DEVICE_TRUST_REQUIRED", "true")

        def bad_api(fp):
            raise ValueError("unexpected parse error")

        monkeypatch.setattr(_dt, "_call_crowdstrike_api", bad_api)

        result = _dt.verify_device_posture("fp-bad")
        assert result.trusted is False
        assert result.reason == "API error"
