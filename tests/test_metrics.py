# [TEMPLATE: CUI // SP-CTI]
"""Tests for tools.saas.metrics — Prometheus-compatible metrics collector.

Validates fallback metric types (_FallbackCounter, _FallbackGauge,
_FallbackHistogram), the MetricsCollector singleton, Flask middleware
registration, and Prometheus text exposition output.
"""

import sys
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.saas.metrics import (
    MetricsCollector,
    _FallbackCounter,
    _FallbackGauge,
    _FallbackHistogram,
    get_collector,
)


# ============================================================================
# _FallbackCounter tests
# ============================================================================

class TestFallbackCounter:
    """Tests for the stdlib-only counter implementation."""

    def test_inc_increases_count(self):
        """inc() must increase the counter value."""
        counter = _FallbackCounter("test_counter", "A test counter")
        counter.inc()
        counter.inc()
        counter.inc(3)
        rendered = counter.render()
        assert "test_counter 5" in rendered

    def test_labels_returns_counter_instance(self):
        """labels() must return a _FallbackCounter for chaining."""
        counter = _FallbackCounter("test_counter", "A test counter",
                                   labelnames=("method",))
        child = counter.labels(method="GET")
        assert isinstance(child, _FallbackCounter)

    def test_labeled_inc(self):
        """inc() on a labeled counter must track per-label values."""
        counter = _FallbackCounter("req_total", "Requests",
                                   labelnames=("method",))
        counter.labels(method="GET").inc(2)
        counter.labels(method="POST").inc(1)
        rendered = counter.render()
        assert 'method="GET"' in rendered
        assert 'method="POST"' in rendered


# ============================================================================
# _FallbackGauge tests
# ============================================================================

class TestFallbackGauge:
    """Tests for the stdlib-only gauge implementation."""

    def test_set_stores_value(self):
        """set() must store the exact value."""
        gauge = _FallbackGauge("test_gauge", "A test gauge")
        gauge.set(42.0)
        rendered = gauge.render()
        assert "test_gauge 42.0" in rendered

    def test_inc_increases_gauge(self):
        """inc() must increase the gauge value."""
        gauge = _FallbackGauge("test_gauge", "A test gauge")
        gauge.set(10)
        gauge.inc(5)
        rendered = gauge.render()
        assert "test_gauge 15" in rendered

    def test_dec_decreases_gauge(self):
        """dec() must decrease the gauge value."""
        gauge = _FallbackGauge("test_gauge", "A test gauge")
        gauge.set(10)
        gauge.dec(3)
        rendered = gauge.render()
        assert "test_gauge 7" in rendered

    def test_labels_returns_gauge_instance(self):
        """labels() must return a _FallbackGauge for chaining."""
        gauge = _FallbackGauge("test_gauge", "A test gauge",
                               labelnames=("status",))
        child = gauge.labels(status="active")
        assert isinstance(child, _FallbackGauge)


# ============================================================================
# _FallbackHistogram tests
# ============================================================================

class TestFallbackHistogram:
    """Tests for the stdlib-only histogram implementation."""

    def test_observe_stores_values(self):
        """observe() must record observation values."""
        hist = _FallbackHistogram("req_duration", "Request duration")
        hist.observe(0.1)
        hist.observe(0.5)
        hist.observe(1.2)
        rendered = hist.render()
        assert "req_duration_count" in rendered
        assert "req_duration_sum" in rendered


# ============================================================================
# MetricsCollector tests
# ============================================================================

class TestMetricsCollector:
    """Tests for the MetricsCollector dual-backend class."""

    def test_initializes_without_prometheus_client(self):
        """MetricsCollector must initialize even without prometheus_client."""
        with patch.dict("sys.modules", {"prometheus_client": None}):
            collector = MetricsCollector()
            assert collector._use_prometheus is False

    def test_has_http_requests_total(self):
        """Collector must expose an http_requests_total metric."""
        collector = MetricsCollector()
        assert hasattr(collector, "http_requests_total")

    def test_has_http_request_duration(self):
        """Collector must expose an http_request_duration metric."""
        collector = MetricsCollector()
        assert hasattr(collector, "http_request_duration")


# ============================================================================
# format_metrics tests
# ============================================================================

class TestFormatMetrics:
    """Tests for format_metrics() Prometheus text exposition output."""

    @pytest.fixture(autouse=True)
    def _collector(self):
        self.collector = MetricsCollector()
        # Force fallback mode for deterministic test output
        self.collector._use_prometheus = False
        self.collector._create_fallback_metrics()

    def test_format_returns_string(self):
        """format_metrics() must return a string."""
        result = self.collector.format_metrics()
        assert isinstance(result, str)

    def test_format_contains_help_lines(self):
        """Output must contain # HELP lines for metric documentation."""
        result = self.collector.format_metrics()
        assert "# HELP" in result

    def test_format_contains_type_lines(self):
        """Output must contain # TYPE lines for metric type declarations."""
        result = self.collector.format_metrics()
        assert "# TYPE" in result

    def test_format_contains_metric_names(self):
        """Output must reference known ICDEV metric names."""
        result = self.collector.format_metrics()
        assert "icdev_http_requests_total" in result
        assert "icdev_gateway_uptime_seconds" in result

    def test_format_matches_prometheus_text_format(self):
        """Output must follow Prometheus text exposition conventions.

        Each metric block has # HELP, # TYPE, then data lines.
        """
        result = self.collector.format_metrics()
        lines = result.strip().split("\n")
        # First non-empty line should be a HELP line
        non_empty = [ln for ln in lines if ln.strip()]
        assert non_empty[0].startswith("# HELP")


# ============================================================================
# get_collector singleton tests
# ============================================================================

class TestGetCollector:
    """Tests for the get_collector() global singleton."""

    def test_returns_metrics_collector(self):
        """get_collector() must return a MetricsCollector instance."""
        # Reset the global singleton for isolation
        import tools.saas.metrics as metrics_module
        original = metrics_module._collector
        metrics_module._collector = None
        try:
            collector = get_collector()
            assert isinstance(collector, MetricsCollector)
        finally:
            metrics_module._collector = original

    def test_returns_same_instance(self):
        """get_collector() must return the same instance on second call."""
        import tools.saas.metrics as metrics_module
        original = metrics_module._collector
        metrics_module._collector = None
        try:
            first = get_collector()
            second = get_collector()
            assert first is second
        finally:
            metrics_module._collector = original


# ============================================================================
# Flask middleware tests
# ============================================================================

class TestMetricsMiddleware:
    """Tests for register_metrics_middleware() Flask integration."""

    @pytest.fixture()
    def flask_app(self):
        """Create a minimal Flask test app for middleware testing."""
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.route("/ping")
        def ping():
            from flask import jsonify
            return jsonify({"pong": True})

        return app

    def test_register_metrics_middleware(self, flask_app):
        """Registering middleware must not raise."""
        collector = MetricsCollector()
        collector.register_metrics_middleware(flask_app)
        # Middleware hooks should be attached
        assert len(flask_app.before_request_funcs.get(None, [])) >= 1

    def test_middleware_increments_request_counter(self, flask_app):
        """After a request, the http_requests_total counter must increase."""
        collector = MetricsCollector()
        collector._use_prometheus = False
        collector._create_fallback_metrics()
        collector.register_metrics_middleware(flask_app)

        client = flask_app.test_client()
        client.get("/ping")

        rendered = collector.http_requests_total.render()
        assert "icdev_http_requests_total" in rendered
        # At least one data line with a non-zero value
        assert "1" in rendered

    def test_middleware_records_duration(self, flask_app):
        """After a request, the http_request_duration histogram must have data."""
        collector = MetricsCollector()
        collector._use_prometheus = False
        collector._create_fallback_metrics()
        collector.register_metrics_middleware(flask_app)

        client = flask_app.test_client()
        client.get("/ping")

        rendered = collector.http_request_duration.render()
        assert "icdev_http_request_duration_seconds_count" in rendered


# ============================================================================
# Best-effort update methods
# ============================================================================

class TestBestEffortUpdates:
    """Tests for update_circuit_breaker_metrics and update_tenant_metrics."""

    def test_update_circuit_breaker_does_not_crash(self):
        """update_circuit_breaker_metrics must not raise even if module missing."""
        collector = MetricsCollector()
        # Should silently pass (no circuit breaker module in test env)
        collector.update_circuit_breaker_metrics()

    def test_update_tenant_metrics_does_not_crash(self):
        """update_tenant_metrics must not raise even if platform.db missing."""
        collector = MetricsCollector()
        # Should silently pass (no platform.db in test env)
        collector.update_tenant_metrics()
