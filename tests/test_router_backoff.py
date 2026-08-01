# CUI // SP-CTI
"""Tests for LLMRouter graceful degradation with exponential backoff (D-GD-1)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _is_transient classification tests
# ---------------------------------------------------------------------------

class TestIsTransient:
    def test_timeout_in_message_is_transient(self):
        from tools.llm.router import _is_transient
        assert _is_transient(ConnectionError("request timeout"))

    def test_rate_limit_in_message_is_transient(self):
        from tools.llm.router import _is_transient
        assert _is_transient(RuntimeError("429 rate limit exceeded"))

    def test_503_in_message_is_transient(self):
        from tools.llm.router import _is_transient
        assert _is_transient(OSError("503 Service Unavailable"))

    def test_502_in_message_is_transient(self):
        from tools.llm.router import _is_transient
        assert _is_transient(OSError("502 Bad Gateway"))

    def test_overloaded_in_message_is_transient(self):
        from tools.llm.router import _is_transient
        assert _is_transient(Exception("Model is overloaded, please try again"))

    def test_connection_in_message_is_transient(self):
        from tools.llm.router import _is_transient
        assert _is_transient(OSError("Connection refused"))

    def test_cross_grader_violation_is_not_transient(self):
        from tools.llm.router import _is_transient, CrossGraderViolation
        assert not _is_transient(CrossGraderViolation("same model graded itself"))

    def test_value_error_is_not_transient(self):
        from tools.llm.router import _is_transient
        assert not _is_transient(ValueError("invalid model id"))

    def test_permission_error_is_not_transient(self):
        from tools.llm.router import _is_transient
        assert not _is_transient(PermissionError("401 Unauthorized"))

    def test_generic_runtime_error_without_keywords_is_not_transient(self):
        from tools.llm.router import _is_transient
        assert not _is_transient(RuntimeError("unknown model"))

    def test_llm_unavailable_with_timeout_last_error_is_transient(self):
        """LLMUnavailableError wrapping a timeout should be transient."""
        from tools.llm.router import _is_transient, LLMUnavailableError
        exc = LLMUnavailableError(
            "All providers in chain [] failed. Last error: connection timeout",
            function="code_generation",
        )
        assert _is_transient(exc)

    def test_llm_unavailable_with_auth_error_is_not_transient(self):
        from tools.llm.router import _is_transient, LLMUnavailableError
        exc = LLMUnavailableError(
            "All providers in chain [] failed. Last error: None",
            function="code_generation",
        )
        assert not _is_transient(exc)


# ---------------------------------------------------------------------------
# Retry behavior tests (patch _invoke_chain to control outcomes)
# ---------------------------------------------------------------------------

def _request(prompt: str = "write a hello world function"):
    """A REAL LLMRequest, not a MagicMock.

    `invoke()` runs `_pre_invoke_redaction` before dispatching, which regexes
    the message text. A MagicMock is not str/bytes, so the scan raised, and
    `redaction.fail_closed` correctly converted that into a blocked call --
    every test here then failed on RedactionUnavailableError before reaching
    the retry logic it was written to exercise.

    Failing closed is the right production behaviour, so the fixture is what
    changes: these tests assert backoff, and they should hand the router the
    request shape it actually contracts on.
    """
    from tools.llm.provider import LLMRequest

    return LLMRequest(messages=[{"role": "user", "content": prompt}])


class TestRouterBackoff:
    """Test invoke() retry logic with _invoke_chain patched out."""

    def _make_router(self):
        from tools.llm.router import LLMRouter
        r = LLMRouter.__new__(LLMRouter)
        r._config = {}
        r._providers = {}
        r._availability_cache = {}
        r._availability_cache_time = 0.0
        r._cache_ttl = 1800.0
        return r

    def test_transient_error_retries_full_count(self):
        """All providers failing with timeout — retries max_retries times."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()
        attempt_count = [0]

        def _fail(fn, req, chain, excl=None, red=None):
            attempt_count[0] += 1
            raise LLMUnavailableError(
                "All providers failed. Last error: connection timeout",
                function=fn,
            )

        with patch.object(router, "_invoke_chain", side_effect=_fail):
            with patch("time.sleep"):
                with pytest.raises(LLMUnavailableError):
                    router.invoke("code_generation", _request(), max_retries=2)

        assert attempt_count[0] == 3  # 1 initial + 2 retries

    def test_nontransient_error_raises_immediately_no_retry(self):
        """Non-transient LLMUnavailableError is not retried."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()
        attempt_count = [0]

        def _fail(fn, req, chain, excl=None, red=None):
            attempt_count[0] += 1
            raise LLMUnavailableError(
                "All providers failed. Last error: None",
                function=fn,
            )

        with patch.object(router, "_invoke_chain", side_effect=_fail):
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(LLMUnavailableError):
                    router.invoke("code_generation", _request(), max_retries=2)

        assert attempt_count[0] == 1
        mock_sleep.assert_not_called()

    def test_cross_grader_violation_never_retried(self):
        """CrossGraderViolation is re-raised immediately without retry."""
        from tools.llm.router import CrossGraderViolation
        router = self._make_router()
        attempt_count = [0]

        def _fail(fn, req, chain, excl=None, red=None):
            attempt_count[0] += 1
            raise CrossGraderViolation("same model graded itself")

        with patch.object(router, "_invoke_chain", side_effect=_fail):
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(CrossGraderViolation):
                    router.invoke("code_generation", _request(), max_retries=2)

        assert attempt_count[0] == 1
        mock_sleep.assert_not_called()

    def test_success_on_second_attempt(self):
        """First attempt fails transiently, second succeeds — returns result."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()
        calls = [0]
        mock_response = MagicMock()

        def _maybe_fail(fn, req, chain, excl=None, red=None):
            calls[0] += 1
            if calls[0] == 1:
                raise LLMUnavailableError(
                    "All providers failed. Last error: 503 Service Unavailable",
                    function=fn,
                )
            return mock_response

        with patch.object(router, "_invoke_chain", side_effect=_maybe_fail):
            with patch("time.sleep") as mock_sleep:
                result = router.invoke("code_generation", _request(), max_retries=2)

        assert result is mock_response
        assert calls[0] == 2
        mock_sleep.assert_called_once()  # slept exactly once before retry

    def test_success_on_third_attempt(self):
        """Two transient failures then success."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()
        calls = [0]
        mock_response = MagicMock()

        def _maybe_fail(fn, req, chain, excl=None, red=None):
            calls[0] += 1
            if calls[0] < 3:
                raise LLMUnavailableError(
                    "All providers failed. Last error: timeout", function=fn
                )
            return mock_response

        with patch.object(router, "_invoke_chain", side_effect=_maybe_fail):
            with patch("time.sleep") as mock_sleep:
                result = router.invoke("code_generation", _request(), max_retries=2)

        assert result is mock_response
        assert calls[0] == 3
        assert mock_sleep.call_count == 2

    def test_zero_retries_no_sleep(self):
        """max_retries=0 — fail immediately, no sleep."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()

        def _fail(fn, req, chain, excl=None, red=None):
            raise LLMUnavailableError(
                "All providers failed. Last error: connection timeout", function=fn
            )

        with patch.object(router, "_invoke_chain", side_effect=_fail):
            with patch("time.sleep") as mock_sleep:
                with pytest.raises(LLMUnavailableError):
                    router.invoke("code_generation", _request(), max_retries=0)

        mock_sleep.assert_not_called()

    def test_retry_delays_increase(self):
        """Sleep durations use the configured delay sequence (increasing)."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()
        sleep_calls = []

        def _fail(fn, req, chain, excl=None, red=None):
            raise LLMUnavailableError(
                "All providers failed. Last error: timeout", function=fn
            )

        with patch.object(router, "_invoke_chain", side_effect=_fail):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                with pytest.raises(LLMUnavailableError):
                    router.invoke("code_generation", _request(), max_retries=2)

        assert len(sleep_calls) == 2
        assert sleep_calls[1] >= sleep_calls[0]

    def test_config_retry_delays_respected(self):
        """router.retry_delays_seconds in config overrides _RETRY_DELAYS default."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()
        router._config = {"router": {"retry_delays_seconds": [1, 2]}}
        sleep_calls = []

        def _fail(fn, req, chain, excl=None, red=None):
            raise LLMUnavailableError(
                "All providers failed. Last error: timeout", function=fn
            )

        with patch.object(router, "_invoke_chain", side_effect=_fail):
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                with pytest.raises(LLMUnavailableError):
                    router.invoke("code_generation", _request(), max_retries=2)

        assert sleep_calls == [1, 2]

    def test_config_max_retries_caps_param(self):
        """router.max_retries in config caps the invoke() param (takes min)."""
        from tools.llm.router import LLMUnavailableError
        router = self._make_router()
        router._config = {"router": {"max_retries": 1, "retry_delays_seconds": [1]}}
        attempt_count = [0]

        def _fail(fn, req, chain, excl=None, red=None):
            attempt_count[0] += 1
            raise LLMUnavailableError(
                "All providers failed. Last error: timeout", function=fn
            )

        with patch.object(router, "_invoke_chain", side_effect=_fail):
            with patch("time.sleep"):
                with pytest.raises(LLMUnavailableError):
                    # Caller requests 3 but config caps at 1
                    router.invoke("code_generation", _request(), max_retries=3)

        assert attempt_count[0] == 2  # 1 initial + 1 retry (capped by config)
