"""
ICDEV™ Prometheus metrics registry.

Provides a single shared CollectorRegistry and the 6 platform metrics.
Import `registry` and `wire_flask_metrics` from this module only.
"""

import time

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Global registry (not the default prometheus_client registry so tests can
# import multiple times without duplicate-metric errors)
# ---------------------------------------------------------------------------
if _PROMETHEUS_AVAILABLE:
    registry = CollectorRegistry()

    http_requests_total = Counter(
        "icdev_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
        registry=registry,
    )

    http_request_duration_seconds = Histogram(
        "icdev_http_request_duration_seconds",
        "HTTP request latency",
        ["method", "path"],
        registry=registry,
    )

    llm_calls_total = Counter(
        "icdev_llm_calls_total",
        "Total LLM invocations",
        ["provider", "model"],
        registry=registry,
    )

    llm_tokens_total = Counter(
        "icdev_llm_tokens_total",
        "Total LLM tokens consumed",
        ["provider", "model", "type"],
        registry=registry,
    )

    kanban_tasks_total = Counter(
        "icdev_kanban_tasks_total",
        "Total kanban tasks by terminal status",
        ["status"],
        registry=registry,
    )

    active_tenants = Gauge(
        "icdev_active_tenants",
        "Number of active tenants",
        registry=registry,
    )

    health_check_status = Gauge(
        "icdev_health_check_status",
        "Platform health status (1=healthy, 0.5=degraded, 0=unhealthy)",
        registry=registry,
    )
else:  # pragma: no cover
    registry = None
    http_requests_total = None
    http_request_duration_seconds = None
    llm_calls_total = None
    llm_tokens_total = None
    kanban_tasks_total = None
    active_tenants = None
    health_check_status = None
    generate_latest = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"


def _normalize_path(path: str) -> str:
    """Collapse numeric path segments so cardinality stays bounded."""
    import re
    return re.sub(r"/\d+", "/{id}", path)


def wire_flask_metrics(app) -> None:
    """Attach before/after_request hooks that record HTTP metrics.

    Idempotent: registering the hooks more than once on the same app would
    double-count requests (each request would inc the counter and observe the
    latency histogram twice), corrupting SLO math. A sentinel flag stored on
    ``app.extensions`` guards against repeat wiring.
    """
    if not _PROMETHEUS_AVAILABLE:
        return

    # Idempotence guard — never register the hooks twice on one app.
    extensions = getattr(app, "extensions", None)
    if extensions is None:
        extensions = {}
        app.extensions = extensions
    if extensions.get("icdev_metrics_wired"):
        return
    extensions["icdev_metrics_wired"] = True

    @app.before_request
    def _start_timer():
        from flask import g as _g, request as _req  # noqa: F401
        _g._metrics_start = time.monotonic()

    @app.after_request
    def _record_http_metrics(response):
        try:
            from flask import g as _g, request as _req
            elapsed = time.monotonic() - getattr(_g, "_metrics_start", time.monotonic())
            path = _normalize_path(_req.path)
            http_requests_total.labels(
                method=_req.method,
                path=path,
                status=str(response.status_code),
            ).inc()
            http_request_duration_seconds.labels(
                method=_req.method,
                path=path,
            ).observe(elapsed)
        except Exception:
            pass
        return response
