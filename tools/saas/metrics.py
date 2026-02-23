# [TEMPLATE: CUI // SP-CTI]
"""
ICDEV SaaS Prometheus Metrics Collector (ADR D154).

Dual-backend metrics: uses prometheus_client when available,
falls back to stdlib text formatter for air-gapped environments.

Follows D66 provider pattern (ABC + implementations).
"""

import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Provider pattern (D66): abstract metric types
# ---------------------------------------------------------------------------


class MetricBase(ABC):
    """Abstract base for a single metric."""

    @abstractmethod
    def labels(self, **kwargs: str) -> "MetricBase":
        ...


class CounterBase(MetricBase):
    @abstractmethod
    def inc(self, amount: float = 1) -> None:
        ...


class GaugeBase(MetricBase):
    @abstractmethod
    def set(self, value: float) -> None:
        ...

    @abstractmethod
    def inc(self, amount: float = 1) -> None:
        ...

    @abstractmethod
    def dec(self, amount: float = 1) -> None:
        ...


class HistogramBase(MetricBase):
    @abstractmethod
    def observe(self, value: float) -> None:
        ...


# ---------------------------------------------------------------------------
# Fallback implementations (no prometheus_client dependency)
# ---------------------------------------------------------------------------


class _FallbackCounter(CounterBase):
    """In-process counter that renders Prometheus text format."""

    def __init__(self, name: str, documentation: str, labelnames: tuple = ()):
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames
        self._lock = threading.Lock()
        # key: frozenset of label pairs -> float
        self._values: Dict[frozenset, float] = {}
        self._label_values: Dict[frozenset, Dict[str, str]] = {}
        self._current_labels: Optional[frozenset] = None

    def labels(self, **kwargs: str) -> "_FallbackCounter":
        key = frozenset(kwargs.items())
        with self._lock:
            if key not in self._values:
                self._values[key] = 0.0
                self._label_values[key] = kwargs
        child = _FallbackCounter(self._name, self._documentation, self._labelnames)
        child._values = self._values
        child._label_values = self._label_values
        child._lock = self._lock
        child._current_labels = key
        return child

    def inc(self, amount: float = 1) -> None:
        key = self._current_labels or frozenset()
        with self._lock:
            self._values.setdefault(key, 0.0)
            self._values[key] += amount

    def render(self) -> str:
        lines: List[str] = []
        lines.append(f"# HELP {self._name} {self._documentation}")
        lines.append(f"# TYPE {self._name} counter")
        with self._lock:
            for key, value in sorted(self._values.items(), key=lambda x: str(x[0])):
                label_str = self._format_labels(self._label_values.get(key, {}))
                lines.append(f"{self._name}{label_str} {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_labels(labels: Dict[str, str]) -> str:
        if not labels:
            return ""
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(parts) + "}"


class _FallbackGauge(GaugeBase):
    """In-process gauge that renders Prometheus text format."""

    def __init__(self, name: str, documentation: str, labelnames: tuple = ()):
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames
        self._lock = threading.Lock()
        self._values: Dict[frozenset, float] = {}
        self._label_values: Dict[frozenset, Dict[str, str]] = {}
        self._current_labels: Optional[frozenset] = None

    def labels(self, **kwargs: str) -> "_FallbackGauge":
        key = frozenset(kwargs.items())
        with self._lock:
            if key not in self._values:
                self._values[key] = 0.0
                self._label_values[key] = kwargs
        child = _FallbackGauge(self._name, self._documentation, self._labelnames)
        child._values = self._values
        child._label_values = self._label_values
        child._lock = self._lock
        child._current_labels = key
        return child

    def set(self, value: float) -> None:
        key = self._current_labels or frozenset()
        with self._lock:
            self._values[key] = value
            if key not in self._label_values:
                self._label_values[key] = {}

    def inc(self, amount: float = 1) -> None:
        key = self._current_labels or frozenset()
        with self._lock:
            self._values.setdefault(key, 0.0)
            self._values[key] += amount

    def dec(self, amount: float = 1) -> None:
        key = self._current_labels or frozenset()
        with self._lock:
            self._values.setdefault(key, 0.0)
            self._values[key] -= amount

    def render(self) -> str:
        lines: List[str] = []
        lines.append(f"# HELP {self._name} {self._documentation}")
        lines.append(f"# TYPE {self._name} gauge")
        with self._lock:
            for key, value in sorted(self._values.items(), key=lambda x: str(x[0])):
                label_str = self._format_labels(self._label_values.get(key, {}))
                lines.append(f"{self._name}{label_str} {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_labels(labels: Dict[str, str]) -> str:
        if not labels:
            return ""
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(parts) + "}"


class _FallbackHistogram(HistogramBase):
    """In-process histogram that renders Prometheus text format."""

    def __init__(self, name: str, documentation: str, labelnames: tuple = ()):
        self._name = name
        self._documentation = documentation
        self._labelnames = labelnames
        self._lock = threading.Lock()
        # key -> list of observations
        self._observations: Dict[frozenset, List[float]] = {}
        self._label_values: Dict[frozenset, Dict[str, str]] = {}
        self._current_labels: Optional[frozenset] = None
        self._buckets = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def labels(self, **kwargs: str) -> "_FallbackHistogram":
        key = frozenset(kwargs.items())
        with self._lock:
            if key not in self._observations:
                self._observations[key] = []
                self._label_values[key] = kwargs
        child = _FallbackHistogram(self._name, self._documentation, self._labelnames)
        child._observations = self._observations
        child._label_values = self._label_values
        child._lock = self._lock
        child._current_labels = key
        child._buckets = self._buckets
        return child

    def observe(self, value: float) -> None:
        key = self._current_labels or frozenset()
        with self._lock:
            self._observations.setdefault(key, []).append(value)

    def render(self) -> str:
        lines: List[str] = []
        lines.append(f"# HELP {self._name} {self._documentation}")
        lines.append(f"# TYPE {self._name} histogram")
        with self._lock:
            for key, obs in sorted(self._observations.items(), key=lambda x: str(x[0])):
                labels = self._label_values.get(key, {})
                label_str = self._format_labels(labels)
                label_str_with_comma = self._format_labels_prefix(labels)
                total = sum(obs)
                count = len(obs)
                for bucket in self._buckets:
                    bucket_count = sum(1 for v in obs if v <= bucket)
                    lines.append(
                        f"{self._name}_bucket{{{label_str_with_comma}"
                        f'le="{bucket}"}} {bucket_count}'
                    )
                lines.append(
                    f"{self._name}_bucket{{{label_str_with_comma}"
                    f'le="+Inf"}} {count}'
                )
                lines.append(f"{self._name}_sum{label_str} {total}")
                lines.append(f"{self._name}_count{label_str} {count}")
        return "\n".join(lines)

    @staticmethod
    def _format_labels(labels: Dict[str, str]) -> str:
        if not labels:
            return ""
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(parts) + "}"

    @staticmethod
    def _format_labels_prefix(labels: Dict[str, str]) -> str:
        if not labels:
            return ""
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return ",".join(parts) + ","


# ---------------------------------------------------------------------------
# MetricsCollector — singleton dual-backend collector
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Collects ICDEV SaaS platform metrics.

    Uses prometheus_client library when available; otherwise falls back
    to stdlib-only text formatting (air-gap safe).
    """

    def __init__(self) -> None:
        self._use_prometheus = False
        self._start_time = time.time()
        self._fallback_metrics: List[Any] = []

        try:
            import prometheus_client  # noqa: F401

            self._use_prometheus = True
        except ImportError:
            pass

        self._create_metrics()

    # -- metric creation ----------------------------------------------------

    def _create_metrics(self) -> None:
        if self._use_prometheus:
            self._create_prometheus_metrics()
        else:
            self._create_fallback_metrics()

    def _create_prometheus_metrics(self) -> None:
        from prometheus_client import Counter, Gauge, Histogram

        self.http_requests_total = Counter(
            "icdev_http_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status", "tenant_id"],
        )
        self.http_request_duration = Histogram(
            "icdev_http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "endpoint", "tenant_id"],
        )
        self.http_in_flight = Gauge(
            "icdev_http_requests_in_flight",
            "Number of HTTP requests currently in flight",
        )
        self.http_errors_total = Counter(
            "icdev_http_errors_total",
            "Total HTTP error responses",
            ["method", "endpoint", "status", "tenant_id"],
        )
        self.rate_limit_hits = Counter(
            "icdev_rate_limit_hits_total",
            "Total rate limit hits",
            ["tenant_id"],
        )
        self.circuit_breaker_state = Gauge(
            "icdev_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=open, 2=half-open)",
            ["service_name", "state"],
        )
        self.gateway_uptime = Gauge(
            "icdev_gateway_uptime_seconds",
            "Gateway uptime in seconds",
        )
        self.platform_tenants = Gauge(
            "icdev_platform_tenants_total",
            "Total tenants on the platform",
            ["status"],
        )

    def _create_fallback_metrics(self) -> None:
        self.http_requests_total = _FallbackCounter(
            "icdev_http_requests_total",
            "Total HTTP requests",
            ("method", "endpoint", "status", "tenant_id"),
        )
        self.http_request_duration = _FallbackHistogram(
            "icdev_http_request_duration_seconds",
            "HTTP request duration in seconds",
            ("method", "endpoint", "tenant_id"),
        )
        self.http_in_flight = _FallbackGauge(
            "icdev_http_requests_in_flight",
            "Number of HTTP requests currently in flight",
        )
        self.http_errors_total = _FallbackCounter(
            "icdev_http_errors_total",
            "Total HTTP error responses",
            ("method", "endpoint", "status", "tenant_id"),
        )
        self.rate_limit_hits = _FallbackCounter(
            "icdev_rate_limit_hits_total",
            "Total rate limit hits",
            ("tenant_id",),
        )
        self.circuit_breaker_state = _FallbackGauge(
            "icdev_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=open, 2=half-open)",
            ("service_name", "state"),
        )
        self.gateway_uptime = _FallbackGauge(
            "icdev_gateway_uptime_seconds",
            "Gateway uptime in seconds",
        )
        self.platform_tenants = _FallbackGauge(
            "icdev_platform_tenants_total",
            "Total tenants on the platform",
            ("status",),
        )
        self._fallback_metrics = [
            self.http_requests_total,
            self.http_request_duration,
            self.http_in_flight,
            self.http_errors_total,
            self.rate_limit_hits,
            self.circuit_breaker_state,
            self.gateway_uptime,
            self.platform_tenants,
        ]

    # -- Flask middleware ----------------------------------------------------

    def register_metrics_middleware(self, app: Any) -> None:
        """Register Flask before_request / after_request hooks."""

        collector = self

        @app.before_request
        def _metrics_before() -> None:
            from flask import g

            collector.http_in_flight.inc()
            g._metrics_start = time.time()

        @app.after_request
        def _metrics_after(response: Any) -> Any:
            from flask import g, request

            collector.http_in_flight.dec()

            try:
                tenant_id = getattr(g, "tenant_id", "anonymous") or "anonymous"
            except RuntimeError:
                tenant_id = "anonymous"

            method = request.method
            endpoint = request.path
            status = str(response.status_code)

            # Duration
            start = getattr(g, "_metrics_start", None)
            if start is not None:
                duration = time.time() - start
                collector.http_request_duration.labels(
                    method=method, endpoint=endpoint, tenant_id=tenant_id
                ).observe(duration)

            # Request count
            collector.http_requests_total.labels(
                method=method, endpoint=endpoint, status=status, tenant_id=tenant_id
            ).inc()

            # Error count (4xx / 5xx)
            if response.status_code >= 400:
                collector.http_errors_total.labels(
                    method=method, endpoint=endpoint, status=status, tenant_id=tenant_id
                ).inc()

            return response

    # -- Prometheus text exposition -------------------------------------------

    def format_metrics(self) -> str:
        """Return all metrics in Prometheus text exposition format."""

        # Always update uptime
        self.gateway_uptime.set(time.time() - self._start_time)

        if self._use_prometheus:
            import prometheus_client

            return prometheus_client.generate_latest().decode("utf-8")

        # Fallback: manually render each metric
        sections: List[str] = []
        for metric in self._fallback_metrics:
            rendered = metric.render()
            if rendered.strip():
                sections.append(rendered)
        return "\n\n".join(sections) + "\n"

    # -- Circuit breaker metrics ---------------------------------------------

    def update_circuit_breaker_metrics(self) -> None:
        """Read circuit breaker states and update gauge.

        Best-effort: silently ignores if resilience module is unavailable.
        """
        try:
            from tools.resilience.circuit_breaker import get_all_breakers

            breakers = get_all_breakers()
            for name, breaker in breakers.items():
                state = getattr(breaker, "state", "closed")
                state_value = {"closed": 0, "open": 1, "half_open": 2}.get(state, 0)
                self.circuit_breaker_state.labels(
                    service_name=name, state=state
                ).set(state_value)
        except (ImportError, AttributeError, Exception):
            pass

    # -- Tenant metrics ------------------------------------------------------

    def update_tenant_metrics(self) -> None:
        """Read tenant counts from platform DB and update gauge.

        Best-effort: silently ignores if platform DB is unavailable.
        """
        try:
            import sqlite3

            db_path = Path(__file__).resolve().parent.parent.parent / "data" / "platform.db"
            if not db_path.exists():
                return
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, COUNT(*) FROM tenants GROUP BY status"
            )
            rows = cursor.fetchall()
            conn.close()
            for status, count in rows:
                self.platform_tenants.labels(status=status).set(count)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_collector: Optional[MetricsCollector] = None
_collector_lock = threading.Lock()


def get_collector() -> MetricsCollector:
    """Return the global MetricsCollector singleton."""
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = MetricsCollector()
    return _collector
