# CUI // SP-CTI
"""Tests for SaaS OAuth auth — constants extraction + JWKS latency anomaly detection."""

import time
from unittest.mock import MagicMock, patch

from tools.saas.auth import constants
from tools.saas.auth.oauth_auth import (
    _fetch_jwks,
    _is_jwks_latency_anomalous,
    _jwks_cache,
    _jwks_latency_history,
    _record_jwks_latency,
)


class TestConstants:
    """Verify extracted constants have sensible defaults."""

    def test_jwks_cache_ttl(self):
        assert constants.JWKS_CACHE_TTL_SECONDS == 3600

    def test_jwks_fetch_timeout(self):
        assert constants.JWKS_FETCH_TIMEOUT_SECONDS == 10

    def test_anomaly_stdev_k(self):
        assert constants.JWKS_LATENCY_ANOMALY_STDEV_K == 2.0

    def test_anomaly_min_samples(self):
        assert constants.JWKS_LATENCY_MIN_SAMPLES == 4

    def test_anomaly_abs_ceiling(self):
        assert constants.JWKS_LATENCY_ABS_CEILING_MS == 5000

    def test_latency_max_history(self):
        assert constants.JWKS_LATENCY_MAX_HISTORY == 20


class TestLatencyHistory:
    """Ring buffer and anomaly detection logic."""

    def setup_method(self):
        _jwks_latency_history.clear()

    def test_record_latency_populates_history(self):
        _record_jwks_latency("https://idp.example/jwks", 120.0)
        assert _jwks_latency_history["https://idp.example/jwks"] == [120.0]

    def test_record_latency_ring_buffer(self):
        uri = "https://idp.example/jwks"
        for i in range(constants.JWKS_LATENCY_MAX_HISTORY + 5):
            _record_jwks_latency(uri, float(i))
        hist = _jwks_latency_history[uri]
        assert len(hist) == constants.JWKS_LATENCY_MAX_HISTORY
        assert hist[0] == 5.0  # first 5 entries evicted

    def test_anomalous_not_enough_samples_uses_ceiling(self):
        _record_jwks_latency("https://idp.example/jwks", 100.0)
        assert _is_jwks_latency_anomalous("https://idp.example/jwks", 6000.0) is True
        assert _is_jwks_latency_anomalous("https://idp.example/jwks", 1000.0) is False

    def test_anomalous_statistical_outlier(self):
        uri = "https://idp.example/jwks"
        # Baseline: fetches with real variance so stdev > 0
        latencies = [90.0, 110.0, 95.0, 105.0, 100.0, 100.0]
        for lat in latencies:
            _record_jwks_latency(uri, lat)
        # mean≈100, stdev≈6.45, mean+2*stdev≈112.9;
        # 1000 ms is well above, 105 ms is below.
        assert _is_jwks_latency_anomalous(uri, 1000.0) is True
        assert _is_jwks_latency_anomalous(uri, 105.0) is False

    def test_anomalous_abs_ceiling_always_triggers(self):
        uri = "https://idp.example/jwks"
        for _ in range(6):
            _record_jwks_latency(uri, 100.0)
        assert _is_jwks_latency_anomalous(uri, 6000.0) is True


class TestFetchJwks:
    """JWKS fetch integrates latency tracking and anomaly warnings."""

    def setup_method(self):
        _jwks_cache.clear()
        _jwks_latency_history.clear()

    @patch("tools.http.client.request")
    def test_fetch_records_latency(self, mock_request):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"keys": [{"kid": "key-1"}]}
        mock_request.return_value = mock_response

        result = _fetch_jwks("https://idp.example/jwks")
        assert result == {"keys": [{"kid": "key-1"}]}
        assert "https://idp.example/jwks" in _jwks_latency_history
        assert len(_jwks_latency_history["https://idp.example/jwks"]) == 1
        mock_request.assert_called_once_with(
            "GET", "https://idp.example/jwks", timeout=constants.JWKS_FETCH_TIMEOUT_SECONDS
        )

    @patch("tools.http.client.request")
    def test_fetch_uses_cache(self, mock_request):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"keys": [{"kid": "key-1"}]}
        mock_request.return_value = mock_response

        _fetch_jwks("https://idp.example/jwks")
        _fetch_jwks("https://idp.example/jwks")
        # Second call should hit cache, not issue another HTTP request
        assert mock_request.call_count == 1

    @patch("tools.http.client.request")
    def test_fetch_cache_expires(self, mock_request):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"keys": [{"kid": "key-1"}]}
        mock_request.return_value = mock_response

        _fetch_jwks("https://idp.example/jwks")
        # Force cache expiry by back-dating the cached entry
        _jwks_cache["https://idp.example/jwks"]["fetched_at"] = time.time() - (
            constants.JWKS_CACHE_TTL_SECONDS + 1
        )
        _fetch_jwks("https://idp.example/jwks")
        assert mock_request.call_count == 2

    @patch("tools.saas.auth.oauth_auth.logger")
    @patch("tools.http.client.request")
    def test_fetch_logs_anomaly_on_slow_latency(self, mock_request, mock_logger):
        # Simulate a very slow response to trigger absolute ceiling
        def slow_request(*args, **kwargs):
            time.sleep(0.01)  # 10 ms — not enough for ceiling, so we patch time instead
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"keys": [{"kid": "key-1"}]}
            return mock_response

        mock_request.side_effect = slow_request

        # Seed baseline so we have enough samples
        for i in range(constants.JWKS_LATENCY_MIN_SAMPLES):
            _record_jwks_latency("https://idp.example/jwks", 50.0)

        # Force an anomalous latency by mocking time.time to return a huge delta
        with patch("tools.saas.auth.oauth_auth.time") as mock_time:
            # Calls: now=0.0, start=0.0, end=5.1  → latency = 5100 ms > ceiling
            mock_time.time.side_effect = [0.0, 0.0, 5.1]
            result = _fetch_jwks("https://idp.example/jwks")
            assert result is not None
            mock_logger.warning.assert_called_once()
            assert "Anomalous JWKS fetch latency" in mock_logger.warning.call_args[0][0]

    @patch("tools.http.client.request")
    def test_fetch_returns_none_on_error(self, mock_request):
        mock_request.side_effect = Exception("connection refused")
        result = _fetch_jwks("https://idp.example/jwks")
        assert result is None
