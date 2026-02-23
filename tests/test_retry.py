# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.resilience.retry."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import patch, MagicMock

import pytest

from tools.resilience.retry import backoff_delay, retry


# ---------------------------------------------------------------------------
# backoff_delay
# ---------------------------------------------------------------------------
class TestBackoffDelay:
    """Tests for the backoff_delay helper."""

    def test_returns_value_in_expected_range(self):
        """delay = min(max_delay, base * 2^attempt) * uniform(0.5, 1.0)"""
        for _ in range(50):
            d = backoff_delay(attempt=2, base_delay=1.0, max_delay=30.0)
            # base * 2^2 = 4.0;  range is [4.0*0.5, 4.0*1.0] = [2.0, 4.0]
            assert 2.0 <= d <= 4.0

    def test_respects_max_delay_cap(self):
        for _ in range(50):
            d = backoff_delay(attempt=20, base_delay=1.0, max_delay=5.0)
            # Capped at 5.0 * uniform(0.5, 1.0) => [2.5, 5.0]
            assert d <= 5.0

    def test_exponential_growth(self):
        """Average delay at attempt N+1 should be ~2x attempt N."""
        import statistics
        samples_a = [backoff_delay(attempt=1, base_delay=1.0, max_delay=1000.0) for _ in range(200)]
        samples_b = [backoff_delay(attempt=2, base_delay=1.0, max_delay=1000.0) for _ in range(200)]
        mean_a = statistics.mean(samples_a)
        mean_b = statistics.mean(samples_b)
        # Ratio should be close to 2.0 (with some tolerance for randomness)
        ratio = mean_b / mean_a
        assert 1.5 < ratio < 2.5

    def test_attempt_zero_close_to_base_delay(self):
        for _ in range(50):
            d = backoff_delay(attempt=0, base_delay=1.0, max_delay=30.0)
            # base * 2^0 = 1.0;  range is [0.5, 1.0]
            assert 0.5 <= d <= 1.0


# ---------------------------------------------------------------------------
# @retry decorator
# ---------------------------------------------------------------------------
class TestRetryDecorator:
    """Tests for the @retry decorator."""

    @patch("tools.resilience.retry.time.sleep")
    def test_succeeds_on_first_try(self, mock_sleep):
        call_count = 0

        @retry(max_retries=3)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeed()
        assert result == "ok"
        assert call_count == 1
        mock_sleep.assert_not_called()

    @patch("tools.resilience.retry.time.sleep")
    def test_retries_on_failure_then_succeeds(self, mock_sleep):
        call_count = 0

        @retry(max_retries=3, retryable_exceptions=(ValueError,))
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "done"

        result = flaky()
        assert result == "done"
        assert call_count == 3
        assert mock_sleep.call_count == 2  # slept before retry 2 and 3

    @patch("tools.resilience.retry.time.sleep")
    def test_exhausts_all_retries_then_raises(self, mock_sleep):
        @retry(max_retries=2, retryable_exceptions=(RuntimeError,))
        def always_fail():
            raise RuntimeError("permanent")

        with pytest.raises(RuntimeError, match="permanent"):
            always_fail()
        # Total calls = max_retries + 1 = 3; sleeps = max_retries = 2
        assert mock_sleep.call_count == 2

    @patch("tools.resilience.retry.time.sleep")
    def test_only_catches_specified_exceptions(self, mock_sleep):
        @retry(max_retries=3, retryable_exceptions=(ValueError,))
        def wrong_error():
            raise TypeError("not retryable")

        with pytest.raises(TypeError, match="not retryable"):
            wrong_error()
        # Should NOT have retried — TypeError is not in the retryable list
        mock_sleep.assert_not_called()

    @patch("tools.resilience.retry.time.sleep")
    def test_calls_on_retry_callback(self, mock_sleep):
        callback = MagicMock()
        call_count = 0

        @retry(max_retries=2, retryable_exceptions=(ValueError,), on_retry=callback)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("oops")
            return "ok"

        result = flaky()
        assert result == "ok"
        assert callback.call_count == 2
        # Callback receives (attempt, exc, delay)
        first_call_args = callback.call_args_list[0][0]
        assert first_call_args[0] == 0  # attempt number
        assert isinstance(first_call_args[1], ValueError)
        assert isinstance(first_call_args[2], float)  # delay

    @patch("tools.resilience.retry.time.sleep")
    def test_max_retries_zero_no_retries(self, mock_sleep):
        @retry(max_retries=0, retryable_exceptions=(RuntimeError,))
        def fail_once():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError, match="fail"):
            fail_once()
        mock_sleep.assert_not_called()

    def test_preserves_function_name(self):
        @retry(max_retries=1)
        def my_special_function():
            """My docstring."""
            pass

        assert my_special_function.__name__ == "my_special_function"

    def test_preserves_function_docstring(self):
        @retry(max_retries=1)
        def documented():
            """This is the docstring."""
            pass

        assert documented.__doc__ == "This is the docstring."

    @patch("tools.resilience.retry.time.sleep")
    def test_multiple_exception_types(self, mock_sleep):
        call_count = 0

        @retry(max_retries=3, retryable_exceptions=(ValueError, OSError))
        def mixed_errors():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("val")
            if call_count == 2:
                raise OSError("os")
            return "ok"

        result = mixed_errors()
        assert result == "ok"
        assert call_count == 3
        assert mock_sleep.call_count == 2

    @patch("tools.resilience.retry.time.sleep")
    def test_return_value_preserved(self, mock_sleep):
        @retry(max_retries=1)
        def returns_dict():
            return {"key": "value", "count": 42}

        result = returns_dict()
        assert result == {"key": "value", "count": 42}

    @patch("tools.resilience.retry.time.sleep")
    def test_args_and_kwargs_forwarded(self, mock_sleep):
        @retry(max_retries=1)
        def add(a, b, extra=0):
            return a + b + extra

        assert add(1, 2, extra=10) == 13
