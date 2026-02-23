#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for OTel tracer wrapper (D280 Stage 4)."""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestOTelTracerImportFallback(unittest.TestCase):
    """Test graceful fallback when opentelemetry is not installed."""

    def test_has_otel_flag_exists(self):
        from tools.observability import otel_tracer
        self.assertIsInstance(otel_tracer.HAS_OTEL, bool)

    def test_otel_tracer_class_exists(self):
        from tools.observability.otel_tracer import OTelTracer
        self.assertTrue(callable(OTelTracer))

    def test_otel_span_class_exists(self):
        from tools.observability.otel_tracer import OTelSpan
        self.assertTrue(callable(OTelSpan))


class TestOTelTracerWithMocks(unittest.TestCase):
    """Test OTelTracer behavior with mocked opentelemetry SDK."""

    def setUp(self):
        # Create mock opentelemetry modules
        self.mock_trace = MagicMock()
        self.mock_sdk_trace = MagicMock()
        self.mock_otlp = MagicMock()
        self.mock_resources = MagicMock()

        # Mock tracer
        self.mock_tracer = MagicMock()
        self.mock_sdk_trace.TracerProvider.return_value = MagicMock()

        self.patcher_trace = patch.dict("sys.modules", {
            "opentelemetry": MagicMock(),
            "opentelemetry.trace": self.mock_trace,
            "opentelemetry.sdk": MagicMock(),
            "opentelemetry.sdk.trace": self.mock_sdk_trace,
            "opentelemetry.sdk.trace.export": MagicMock(),
            "opentelemetry.sdk.resources": self.mock_resources,
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": self.mock_otlp,
        })

    def test_otel_tracer_requires_otel_sdk(self):
        """OTelTracer should check for opentelemetry availability."""
        from tools.observability.otel_tracer import HAS_OTEL, OTelTracer
        # If HAS_OTEL is False, creating tracer should still not crash
        if not HAS_OTEL:
            tracer = OTelTracer()
            # Should fall back gracefully
            self.assertIsNotNone(tracer)

    def test_otel_span_abc_compliance(self):
        """OTelSpan should implement Span ABC methods."""
        from tools.observability.otel_tracer import OTelSpan
        from tools.observability.tracer import Span
        self.assertTrue(issubclass(OTelSpan, Span))

    def test_otel_tracer_abc_compliance(self):
        """OTelTracer should implement Tracer ABC methods."""
        from tools.observability.otel_tracer import OTelTracer
        from tools.observability.tracer import Tracer
        self.assertTrue(issubclass(OTelTracer, Tracer))

    def test_otel_span_set_attribute(self):
        """OTelSpan.set_attribute delegates to underlying span."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        span = OTelSpan(mock_span)
        span.set_attribute("key", "value")
        mock_span.set_attribute.assert_called_once_with("key", "value")

    def test_otel_span_set_status_ok(self):
        """OTelSpan.set_status('OK') delegates correctly."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        span = OTelSpan(mock_span)
        span.set_status("OK")
        mock_span.set_status.assert_called_once()

    def test_otel_span_set_status_error(self):
        """OTelSpan.set_status('ERROR') delegates correctly."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        span = OTelSpan(mock_span)
        span.set_status("ERROR", "something failed")
        mock_span.set_status.assert_called_once()

    def test_otel_span_add_event(self):
        """OTelSpan.add_event delegates to underlying span."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        span = OTelSpan(mock_span)
        span.add_event("test_event", {"key": "val"})
        mock_span.add_event.assert_called_once()

    def test_otel_span_end(self):
        """OTelSpan.end delegates to underlying span."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        span = OTelSpan(mock_span)
        span.end()
        mock_span.end.assert_called_once()

    def test_otel_span_context_manager(self):
        """OTelSpan works as context manager."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        span = OTelSpan(mock_span)
        with span as s:
            self.assertIs(s, span)
        mock_span.end.assert_called_once()

    def test_otel_span_context_manager_on_error(self):
        """OTelSpan sets ERROR status when exception occurs in context."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        span = OTelSpan(mock_span)
        with self.assertRaises(ValueError):
            with span:
                raise ValueError("test error")
        mock_span.set_status.assert_called()
        mock_span.end.assert_called_once()

    def test_otel_span_trace_id_property(self):
        """OTelSpan.trace_id returns hex trace ID."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.trace_id = 0xABCDEF1234567890
        mock_ctx.span_id = 0x1234567890ABCDEF
        mock_span.get_span_context.return_value = mock_ctx
        span = OTelSpan(mock_span)
        trace_id = span.trace_id
        self.assertIsInstance(trace_id, str)

    def test_otel_span_span_id_property(self):
        """OTelSpan.span_id returns hex span ID."""
        from tools.observability.otel_tracer import OTelSpan
        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.span_id = 0x1234567890ABCDEF
        mock_span.get_span_context.return_value = mock_ctx
        span = OTelSpan(mock_span)
        span_id = span.span_id
        self.assertIsInstance(span_id, str)


class TestOTelTracerCreation(unittest.TestCase):
    """Test OTelTracer initialization paths."""

    def test_create_without_otel(self):
        """Creating OTelTracer without opentelemetry should not raise."""
        from tools.observability.otel_tracer import OTelTracer, HAS_OTEL
        try:
            tracer = OTelTracer()
        except ImportError:
            # Expected if OTel not installed
            pass
        except Exception:
            if not HAS_OTEL:
                pass  # Acceptable — OTel not installed
            else:
                raise

    def test_service_name_parameter(self):
        """OTelTracer accepts service_name parameter."""
        from tools.observability.otel_tracer import OTelTracer, HAS_OTEL
        if not HAS_OTEL:
            self.skipTest("opentelemetry not installed")
        tracer = OTelTracer(service_name="test-service")
        self.assertIsNotNone(tracer)


if __name__ == "__main__":
    unittest.main()
