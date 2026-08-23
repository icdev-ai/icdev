# CUI // SP-CTI
"""Tests for AAC aac-opp-469 — LLMConfiguredDigestAuth.

Verifies that the hardcoded `num_401_calls < 2` threshold in
requests/auth.py is replaced by a configurable parameter, and that
the LLM advisor falls back gracefully when no LLM is available.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

# `requests` is a declared dependency (requirements.txt), so the module
# imports it unconditionally — a skip guard here would let a gated file
# satisfy coverage while asserting nothing (skip census, rem-tst discipline).
import requests  # noqa: F401

import pytest

from tools.ai_augmentation.implementations.llm_http_auth import (
    LLMConfiguredDigestAuth,
    _DEFAULT_THRESHOLD,
    _MAX_ALLOWED_RETRIES,
    _parse_threshold,
    advise_retry_threshold,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_response(status_code: int = 401, www_auth: str = "Digest realm=\"test\""):
    """Return a minimal mock that looks like a requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"www-authenticate": www_auth}
    resp.is_redirect = False
    resp.request = MagicMock()
    resp.request.body = None
    resp.connection = MagicMock()
    # Simulate a follow-up response from the retry
    retry_resp = MagicMock()
    retry_resp.history = []
    resp.connection.send.return_value = retry_resp
    return resp


# ---------------------------------------------------------------------------
# LLMConfiguredDigestAuth — construction
# ---------------------------------------------------------------------------

class TestLLMConfiguredDigestAuthInit:
    def test_default_retries_is_one(self):
        auth = LLMConfiguredDigestAuth("user", "pass")
        assert auth.max_401_retries == 1

    def test_custom_retries(self):
        auth = LLMConfiguredDigestAuth("user", "pass", max_401_retries=3)
        assert auth.max_401_retries == 3

    def test_zero_retries_raises(self):
        with pytest.raises(ValueError, match="max_401_retries must be >= 1"):
            LLMConfiguredDigestAuth("user", "pass", max_401_retries=0)

    def test_negative_retries_raises(self):
        with pytest.raises(ValueError):
            LLMConfiguredDigestAuth("user", "pass", max_401_retries=-1)

    def test_username_password_forwarded(self):
        auth = LLMConfiguredDigestAuth("alice", "secret", max_401_retries=2)
        assert auth.username == "alice"
        assert auth.password == "secret"


# ---------------------------------------------------------------------------
# handle_401 — threshold enforcement
# ---------------------------------------------------------------------------

class TestHandle401Threshold:
    def _make_auth(self, max_retries: int) -> LLMConfiguredDigestAuth:
        auth = LLMConfiguredDigestAuth("u", "p", max_401_retries=max_retries)
        auth.init_per_thread_state()
        return auth

    def test_non_4xx_resets_counter(self):
        auth = self._make_auth(1)
        auth._thread_local.num_401_calls = 2
        resp = _make_fake_response(status_code=200, www_auth="")
        result = auth.handle_401(resp)
        assert result is resp
        assert auth._thread_local.num_401_calls == 1

    def test_first_401_retries_when_max_is_one(self):
        """With max_401_retries=1, first 401 should trigger a retry."""
        auth = self._make_auth(1)
        auth._thread_local.num_401_calls = 1  # initial value set by __call__

        resp = _make_fake_response(401)

        with patch("requests.utils.parse_dict_header", return_value={"realm": "test", "nonce": "abc"}), \
             patch("requests.cookies.extract_cookies_to_jar"), \
             patch.object(auth, "build_digest_header", return_value="Digest token=abc"):
            prep = MagicMock()
            prep._cookies = {}
            resp.request.copy.return_value = prep
            result = auth.handle_401(resp)

        # Should have returned the retry response
        assert result is resp.connection.send.return_value

    def test_second_401_does_not_retry_when_max_is_one(self):
        """With max_401_retries=1, second 401 must not retry."""
        auth = self._make_auth(1)
        auth._thread_local.num_401_calls = 2  # already retried once

        resp = _make_fake_response(401)
        result = auth.handle_401(resp)

        # send should NOT have been called — falls through to the reset path
        resp.connection.send.assert_not_called()
        assert result is resp

    def test_higher_max_allows_more_retries(self):
        """max_401_retries=3 allows counter values 1, 2, 3 to retry."""
        auth = self._make_auth(3)
        retried = []

        for start_count in [1, 2, 3]:
            auth.init_per_thread_state()
            auth._thread_local.num_401_calls = start_count
            resp = _make_fake_response(401)

            with patch("requests.utils.parse_dict_header", return_value={"realm": "test", "nonce": "abc"}), \
                 patch("requests.cookies.extract_cookies_to_jar"), \
                 patch.object(auth, "build_digest_header", return_value="Digest token=abc"):
                prep = MagicMock()
                prep._cookies = {}
                resp.request.copy.return_value = prep
                result = auth.handle_401(resp)

            retried.append(result is resp.connection.send.return_value)

        assert all(retried), "Expected retries for counts 1, 2, 3 with max=3"

    def test_counter_four_does_not_retry_when_max_is_three(self):
        auth = self._make_auth(3)
        auth._thread_local.num_401_calls = 4

        resp = _make_fake_response(401)
        result = auth.handle_401(resp)

        resp.connection.send.assert_not_called()
        assert result is resp

    def test_no_digest_header_does_not_retry(self):
        auth = self._make_auth(5)
        auth._thread_local.num_401_calls = 1
        resp = _make_fake_response(401, www_auth="Basic realm=\"test\"")

        result = auth.handle_401(resp)

        resp.connection.send.assert_not_called()
        assert result is resp


# ---------------------------------------------------------------------------
# _parse_threshold
# ---------------------------------------------------------------------------

class TestParseThreshold:
    def test_valid_integer_extracted(self):
        assert _parse_threshold("3") == 3
        assert _parse_threshold("  2  ") == 2
        assert _parse_threshold("I recommend 4 retries.") == 4

    def test_out_of_range_not_matched(self):
        # Values outside 1–5 don't match the regex `\b([1-5])\b`
        assert _parse_threshold("6") == _DEFAULT_THRESHOLD
        assert _parse_threshold("0") == _DEFAULT_THRESHOLD

    def test_no_integer_returns_default(self):
        assert _parse_threshold("yes please") == _DEFAULT_THRESHOLD
        assert _parse_threshold("") == _DEFAULT_THRESHOLD

    def test_first_valid_digit_wins(self):
        assert _parse_threshold("try 2 or maybe 4") == 2


# ---------------------------------------------------------------------------
# advise_retry_threshold — graceful degradation
# ---------------------------------------------------------------------------

class TestAdviseRetryThreshold:
    def test_returns_default_when_router_unavailable(self):
        with patch.dict(sys.modules, {"tools.llm.router": None}):
            result = advise_retry_threshold("https://api.example.com/data")
        assert result == _DEFAULT_THRESHOLD

    def test_returns_default_on_llm_exception(self):
        mock_router_mod = types.ModuleType("tools.llm.router")

        class _FakeRouter:
            def invoke(self, function_name, req):
                raise RuntimeError("LLM unavailable")

        mock_router_mod.LLMRouter = _FakeRouter
        mock_router_mod.LLMUnavailableError = RuntimeError

        mock_req_mod = types.ModuleType("tools.llm.provider")
        mock_req_mod.LLMRequest = MagicMock(side_effect=lambda **kw: kw)

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_req_mod,
        }):
            result = advise_retry_threshold("https://api.example.com/data")

        assert result == _DEFAULT_THRESHOLD

    def test_returns_llm_suggestion_when_available(self):
        mock_router_mod = types.ModuleType("tools.llm.router")

        class _FakeResponse:
            content = "2"

        class _FakeRouter:
            def invoke(self, function_name, req):
                assert function_name == "llm_generation"
                return _FakeResponse()

        mock_router_mod.LLMRouter = _FakeRouter
        mock_router_mod.LLMUnavailableError = RuntimeError

        mock_req_mod = types.ModuleType("tools.llm.provider")

        class _FakeRequest:
            def __init__(self, **kw):
                pass

        mock_req_mod.LLMRequest = _FakeRequest

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_req_mod,
        }):
            result = advise_retry_threshold("https://api.example.com/data")

        assert result == 2

    def test_context_accepted_without_error(self):
        ctx = {"il_level": "il4", "environment": "staging"}
        with patch.dict(sys.modules, {"tools.llm.router": None}):
            result = advise_retry_threshold("https://gov.example.mil/api", ctx)
        assert isinstance(result, int)
        assert 1 <= result <= _MAX_ALLOWED_RETRIES


# ---------------------------------------------------------------------------
# from_llm classmethod
# ---------------------------------------------------------------------------

class TestFromLlm:
    def test_from_llm_returns_instance(self):
        with patch(
            "tools.ai_augmentation.implementations.llm_http_auth.advise_retry_threshold",
            return_value=2,
        ):
            auth = LLMConfiguredDigestAuth.from_llm("user", "pass", "https://example.com")

        assert isinstance(auth, LLMConfiguredDigestAuth)
        assert auth.max_401_retries == 2
        assert auth.username == "user"

    def test_from_llm_fallback_when_no_llm(self):
        with patch.dict(sys.modules, {"tools.llm.router": None}):
            auth = LLMConfiguredDigestAuth.from_llm("user", "pass", "https://example.com")

        assert auth.max_401_retries == _DEFAULT_THRESHOLD
