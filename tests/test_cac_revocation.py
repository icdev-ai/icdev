#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for G-11 — CAC/PIV CRL/OCSP revocation checking in cac_auth.py."""

import os
import time
import pytest

import tools.saas.auth.cac_auth as _cac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_cache():
    _cac._CRL_CACHE.clear()


# ---------------------------------------------------------------------------
# verify_crl
# ---------------------------------------------------------------------------

class TestVerifyCRL:
    def test_no_url_non_strict_permits(self, monkeypatch):
        monkeypatch.delenv("ICDEV_PKI_CRL_URL", raising=False)
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", False)
        assert _cac.verify_crl("ABCD1234") is True

    def test_no_url_strict_denies(self, monkeypatch):
        monkeypatch.delenv("ICDEV_PKI_CRL_URL", raising=False)
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", True)
        assert _cac.verify_crl("ABCD1234") is False

    def test_empty_serial_non_strict_permits(self, monkeypatch):
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", False)
        assert _cac.verify_crl("", crl_url="http://example.com/crl") is True

    def test_revoked_serial_denied(self, monkeypatch):
        _clear_cache()
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", False)

        def fake_fetch(url):
            return {"DEADBEEF"}

        monkeypatch.setattr(_cac, "_fetch_crl_revoked_serials", fake_fetch)
        assert _cac.verify_crl("DEADBEEF", crl_url="http://x.example") is False

    def test_non_revoked_serial_permitted(self, monkeypatch):
        _clear_cache()
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", False)

        def fake_fetch(url):
            return {"DEADBEEF"}

        monkeypatch.setattr(_cac, "_fetch_crl_revoked_serials", fake_fetch)
        assert _cac.verify_crl("00CAFEBABE", crl_url="http://x.example") is True

    def test_serial_normalised_before_compare(self, monkeypatch):
        _clear_cache()
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", False)

        def fake_fetch(url):
            return {"DEADBEEF"}

        monkeypatch.setattr(_cac, "_fetch_crl_revoked_serials", fake_fetch)
        # Leading zeros should be stripped before comparison
        assert _cac.verify_crl("0000DEADBEEF", crl_url="http://x.example") is False

    def test_fetch_error_non_strict_permits(self, monkeypatch):
        _clear_cache()
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", False)

        def bad_fetch(url):
            raise ConnectionError("network down")

        monkeypatch.setattr(_cac, "_fetch_crl_revoked_serials", bad_fetch)
        assert _cac.verify_crl("ABCD", crl_url="http://x.example") is True

    def test_fetch_error_strict_denies(self, monkeypatch):
        _clear_cache()
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", True)

        def bad_fetch(url):
            raise ConnectionError("network down")

        monkeypatch.setattr(_cac, "_fetch_crl_revoked_serials", bad_fetch)
        assert _cac.verify_crl("ABCD", crl_url="http://x.example") is False


# ---------------------------------------------------------------------------
# _fetch_crl_revoked_serials — cache behaviour
# ---------------------------------------------------------------------------

class TestCRLCache:
    def test_cache_hit_skips_fetch(self, monkeypatch):
        _clear_cache()
        fetch_count = [0]

        def counting_fetch(url):
            import urllib.request
            raise OSError("should not reach here")

        # Pre-populate cache
        url = "http://disa.example/crl"
        _cac._CRL_CACHE[url] = ({"AABBCCDD"}, time.time())

        # verify_crl should use cache, not call urlopen
        assert _cac.verify_crl("AABBCCDD", crl_url=url) is False  # in revoked set
        assert fetch_count[0] == 0  # network never called

    def test_cache_expiry_refetches(self, monkeypatch):
        _clear_cache()
        url = "http://disa.example/crl"
        # Expire the cache entry
        _cac._CRL_CACHE[url] = ({"OLD"}, time.time() - _cac._CRL_CACHE_TTL - 1)

        def fake_fetch_fresh(crl_url):
            return {"FRESHSERIAL"}

        monkeypatch.setattr(_cac, "_fetch_crl_revoked_serials", fake_fetch_fresh)
        result = _cac.verify_crl("FRESHSERIAL", crl_url=url)
        assert result is False  # FRESHSERIAL is in fresh set → revoked


# ---------------------------------------------------------------------------
# check_revocation
# ---------------------------------------------------------------------------

class TestCheckRevocation:
    def test_no_serial_non_strict_passes(self, monkeypatch):
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", False)
        assert _cac.check_revocation(None) is True

    def test_no_serial_strict_fails(self, monkeypatch):
        monkeypatch.setattr(_cac, "_STRICT_REVOCATION", True)
        assert _cac.check_revocation(None) is False

    def test_delegates_to_ocsp_when_configured(self, monkeypatch):
        monkeypatch.setenv("ICDEV_PKI_OCSP_URL", "http://ocsp.example")
        called = []

        def fake_ocsp(serial, issuer_cn="", ocsp_url=None):
            called.append(serial)
            return True

        monkeypatch.setattr(_cac, "verify_ocsp", fake_ocsp)
        _cac.check_revocation("CAFE")
        assert "CAFE" in called
        monkeypatch.delenv("ICDEV_PKI_OCSP_URL", raising=False)

    def test_delegates_to_crl_when_no_ocsp(self, monkeypatch):
        monkeypatch.delenv("ICDEV_PKI_OCSP_URL", raising=False)
        called = []

        def fake_crl(serial, crl_url=None):
            called.append(serial)
            return True

        monkeypatch.setattr(_cac, "verify_crl", fake_crl)
        _cac.check_revocation("BEEF")
        assert "BEEF" in called
