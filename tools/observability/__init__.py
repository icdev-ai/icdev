#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Observability — Pluggable distributed tracing (D280).

Provides a Haystack-style ProxyTracer with three backends:
  - OTelTracer   (production, requires opentelemetry-sdk)
  - SQLiteTracer (air-gapped IL5/IL6, stdlib only)
  - NullTracer   (fallback, zero overhead)

Usage:
    from tools.observability import get_tracer
    tracer = get_tracer()

    with tracer.start_span("my_operation") as span:
        span.set_attribute("key", "value")
        # ... do work ...

Configuration:
    args/observability_tracing_config.yaml — tracer backend, sampling, retention
    ICDEV_CONTENT_TRACING_ENABLED env var — opt-in for plaintext content
    ICDEV_MLFLOW_TRACKING_URI env var — auto-selects OTel backend when set
"""

from tools.observability.tracer import (
    NullSpan,
    NullTracer,
    ProxyTracer,
    Span,
    Tracer,
    set_content_tag,
)

_proxy = ProxyTracer()


def get_tracer() -> ProxyTracer:
    """Return the global ProxyTracer (D280).

    The proxy delegates to the actual tracer backend (OTel, SQLite, or Null).
    Call configure_tracer() or enable_tracing() to set the backend.
    """
    return _proxy


def configure_tracer(tracer: Tracer) -> None:
    """Set the active tracer backend.

    Args:
        tracer: A Tracer implementation (OTelTracer, SQLiteTracer, NullTracer).
    """
    _proxy.set_tracer(tracer)


def enable_tracing(backend: str = "auto") -> Tracer:
    """Auto-configure tracing based on environment (D290).

    Args:
        backend: "otel", "sqlite", "null", or "auto".
            "auto" checks ICDEV_MLFLOW_TRACKING_URI → otel, else sqlite.

    Returns:
        The configured Tracer instance.
    """
    import os

    if backend == "auto":
        if os.environ.get("ICDEV_MLFLOW_TRACKING_URI"):
            backend = "otel"
        else:
            backend = "sqlite"

    if backend == "otel":
        try:
            from tools.observability.otel_tracer import OTelTracer

            tracer = OTelTracer()
        except ImportError:
            # D73: Graceful fallback when opentelemetry-sdk not installed
            from tools.observability.sqlite_tracer import SQLiteTracer

            tracer = SQLiteTracer()
    elif backend == "sqlite":
        from tools.observability.sqlite_tracer import SQLiteTracer

        tracer = SQLiteTracer()
    else:
        tracer = NullTracer()

    _proxy.set_tracer(tracer)
    return tracer


def enable_tracing_if_enabled(env_var: str = "ICDEV_TRACING_ENABLED"):
    """Activate tracing at startup when the env toggle is on (obx-trc-01, D290).

    Reads ``env_var`` (default ``ICDEV_TRACING_ENABLED``). Treats
    ``"false"``/``"0"``/``"no"`` (case-insensitive, whitespace-trimmed) as OFF
    and everything else — including an unset var — as ON (default). When ON,
    calls :func:`enable_tracing` so :func:`get_tracer` delegates to a real
    backend instead of staying a NullTracer.

    Args:
        env_var: Name of the environment toggle to consult.

    Returns:
        The configured Tracer when activated, or ``None`` when disabled.
    """
    import os

    if os.environ.get(env_var, "true").strip().lower() in ("false", "0", "no"):
        return None
    return enable_tracing()


__all__ = [
    "get_tracer",
    "configure_tracer",
    "enable_tracing",
    "enable_tracing_if_enabled",
    "Tracer",
    "Span",
    "NullTracer",
    "NullSpan",
    "ProxyTracer",
    "set_content_tag",
]
