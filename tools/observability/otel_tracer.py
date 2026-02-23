#!/usr/bin/env python3
# CUI // SP-CTI
"""OTel Tracer — OpenTelemetry SDK wrapper (D280, D283).

Wraps the opentelemetry-api/sdk for production environments with OTLP export.
Gracefully falls back to NullTracer when opentelemetry is not installed (D73).

Usage:
    from tools.observability.otel_tracer import OTelTracer
    tracer = OTelTracer(service_name="icdev-builder", endpoint="http://localhost:4317")

Configuration:
    ICDEV_OTEL_ENDPOINT — OTel collector endpoint
    ICDEV_MLFLOW_TRACKING_URI — MLflow tracking server
    args/observability_tracing_config.yaml — full config
"""

import logging
import os
from typing import Any, Dict, Optional

from tools.observability.tracer import NullSpan, NullTracer, Span, Tracer

logger = logging.getLogger("icdev.observability.otel_tracer")

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        OTLPSpanExporter = None

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False
    otel_trace = None


class OTelSpan(Span):
    """Span wrapping an OpenTelemetry SDK span."""

    def __init__(self, otel_span):
        self._otel_span = otel_span
        self._status_code = "UNSET"

    @property
    def span_id(self) -> str:
        ctx = self._otel_span.get_span_context()
        return format(ctx.span_id, "016x")

    @property
    def trace_id(self) -> str:
        ctx = self._otel_span.get_span_context()
        return format(ctx.trace_id, "032x")

    @property
    def parent_span_id(self) -> Optional[str]:
        parent = getattr(self._otel_span, "parent", None)
        if parent and parent.span_id:
            return format(parent.span_id, "016x")
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        self._otel_span.set_attribute(key, value)

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self._otel_span.add_event(name, attributes=attributes or {})

    def set_status(self, code: str, message: str = "") -> None:
        self._status_code = code
        if code == "ERROR":
            self._otel_span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, message))
        elif code == "OK":
            self._otel_span.set_status(otel_trace.Status(otel_trace.StatusCode.OK))

    def end(self) -> None:
        self._otel_span.end()

    def _raw_status_code(self) -> str:
        return self._status_code


class OTelTracer(Tracer):
    """OpenTelemetry SDK tracer for production environments (D280, D283).

    Requires: opentelemetry-api, opentelemetry-sdk
    Optional: opentelemetry-exporter-otlp (for OTLP gRPC export)
    """

    def __init__(
        self,
        service_name: str = "icdev",
        endpoint: Optional[str] = None,
    ):
        if not HAS_OTEL:
            raise ImportError(
                "opentelemetry-sdk not installed. Install with: "
                "pip install opentelemetry-api opentelemetry-sdk"
            )

        self._endpoint = endpoint or os.environ.get(
            "ICDEV_OTEL_ENDPOINT", "http://localhost:4317"
        )

        resource = Resource.create({
            "service.name": service_name,
            "service.version": "phase-46",
            "deployment.environment": os.environ.get("ICDEV_ENVIRONMENT", "development"),
        })

        provider = TracerProvider(resource=resource)

        # Add OTLP exporter if available
        if OTLPSpanExporter:
            try:
                otlp_exporter = OTLPSpanExporter(endpoint=self._endpoint)
                provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            except Exception as e:
                logger.warning("OTLP exporter setup failed: %s — using console", e)
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            logger.info("OTLP exporter not available — using console exporter")
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        otel_trace.set_tracer_provider(provider)
        self._tracer = otel_trace.get_tracer("icdev.observability", "0.1.0")
        self._provider = provider

    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        kind: str = "INTERNAL",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> OTelSpan:
        # Map kind string to OTel SpanKind
        kind_map = {
            "INTERNAL": otel_trace.SpanKind.INTERNAL,
            "CLIENT": otel_trace.SpanKind.CLIENT,
            "SERVER": otel_trace.SpanKind.SERVER,
            "PRODUCER": otel_trace.SpanKind.PRODUCER,
            "CONSUMER": otel_trace.SpanKind.CONSUMER,
        }
        otel_kind = kind_map.get(kind, otel_trace.SpanKind.INTERNAL)

        otel_span = self._tracer.start_span(
            name=name,
            kind=otel_kind,
            attributes=attributes or {},
        )
        return OTelSpan(otel_span)

    def get_active_span(self) -> Optional[Span]:
        current = otel_trace.get_current_span()
        if current and current.is_recording():
            return OTelSpan(current)
        return None

    def flush(self) -> None:
        if hasattr(self._provider, "force_flush"):
            self._provider.force_flush()
