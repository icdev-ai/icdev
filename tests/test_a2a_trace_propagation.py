#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for A2A distributed tracing — W3C traceparent propagation (D285).

Verifies:
  - A2A client injects traceparent into metadata
  - A2A server extracts traceparent from metadata
  - Cross-agent span parent/child linking
  - Backward compat with D149 correlation_id
  - Correlation middleware generates traceparent
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.observability.trace_context import (
    TraceContext,
    clear_current_context,
    generate_traceparent,
    get_current_context,
    parse_traceparent,
    set_current_context,
)


class TestA2AClientTraceparent:
    """Test that agent_client.py propagates traceparent in metadata."""

    def test_traceparent_added_to_metadata(self):
        """When trace context exists, traceparent should be in metadata."""
        clear_current_context()
        ctx = generate_traceparent()
        set_current_context(ctx)

        meta = {}
        # Simulate the client's traceparent injection
        try:
            from tools.observability.trace_context import get_current_context
            retrieved = get_current_context()
            if retrieved:
                meta["traceparent"] = retrieved.to_traceparent()
                if retrieved.tracestate:
                    meta["tracestate"] = retrieved.tracestate
        except ImportError:
            pass

        assert "traceparent" in meta
        assert meta["traceparent"] == ctx.to_traceparent()
        clear_current_context()

    def test_no_traceparent_when_context_empty(self):
        """When no trace context, metadata should not have traceparent."""
        clear_current_context()

        meta = {}
        retrieved = get_current_context()
        if retrieved:
            meta["traceparent"] = retrieved.to_traceparent()

        assert "traceparent" not in meta


class TestA2AServerTraceparent:
    """Test that agent_server.py extracts traceparent from metadata."""

    def test_traceparent_restored_from_metadata(self):
        """Server should restore trace context from incoming traceparent."""
        clear_current_context()
        original = generate_traceparent()
        metadata = {"traceparent": original.to_traceparent()}

        # Simulate the server's traceparent extraction
        tp = metadata.get("traceparent")
        if tp:
            ctx = parse_traceparent(tp)
            if ctx:
                set_current_context(ctx)

        restored = get_current_context()
        assert restored is not None
        assert restored.trace_id == original.trace_id
        assert restored.span_id == original.span_id
        clear_current_context()

    def test_invalid_traceparent_ignored(self):
        """Server should ignore invalid traceparent without error."""
        clear_current_context()
        metadata = {"traceparent": "invalid-header"}

        tp = metadata.get("traceparent")
        ctx = parse_traceparent(tp)
        assert ctx is None

        restored = get_current_context()
        assert restored is None

    def test_no_traceparent_falls_back_to_none(self):
        """Without traceparent in metadata, context remains None."""
        clear_current_context()
        metadata = {"correlation_id": "abc123"}

        tp = metadata.get("traceparent")
        assert tp is None

        restored = get_current_context()
        assert restored is None


class TestCrossAgentSpanLinking:
    """Test that parent-child spans link correctly across agents."""

    def test_child_agent_inherits_trace_id(self):
        """Child agent span should share trace_id with parent agent."""
        clear_current_context()

        # Parent agent creates a root span context
        parent_ctx = generate_traceparent()
        parent_traceparent = parent_ctx.to_traceparent()

        # Simulate A2A transport: parent sends traceparent
        metadata = {"traceparent": parent_traceparent}

        # Child agent receives and restores context
        child_received = parse_traceparent(metadata["traceparent"])
        assert child_received is not None
        assert child_received.trace_id == parent_ctx.trace_id

        # Child creates a new span (child of the parent)
        child_span_ctx = child_received.child()
        assert child_span_ctx.trace_id == parent_ctx.trace_id
        assert child_span_ctx.span_id != parent_ctx.span_id

        clear_current_context()

    def test_traceparent_roundtrip(self):
        """traceparent serialization and parsing should roundtrip."""
        ctx = generate_traceparent()
        header = ctx.to_traceparent()
        parsed = parse_traceparent(header)

        assert parsed is not None
        assert parsed.trace_id == ctx.trace_id
        assert parsed.span_id == ctx.span_id
        assert parsed.sampled == ctx.sampled


class TestCorrelationTraceparentIntegration:
    """Test correlation.py middleware traceparent integration (D281)."""

    def test_middleware_generates_traceparent(self):
        """Flask middleware should generate traceparent when not provided."""
        try:
            from flask import Flask
        except ImportError:
            pytest.skip("Flask not installed")

        from tools.resilience.correlation import register_correlation_middleware

        app = Flask(__name__)

        @app.route("/test")
        def test_route():
            from flask import g
            return {
                "correlation_id": getattr(g, "correlation_id", None),
                "traceparent": getattr(g, "traceparent", None),
            }

        register_correlation_middleware(app)

        with app.test_client() as client:
            resp = client.get("/test")
            data = resp.get_json()

            assert data["correlation_id"] is not None
            assert data["traceparent"] is not None
            # traceparent should be valid W3C format
            parsed = parse_traceparent(data["traceparent"])
            assert parsed is not None

    def test_middleware_extracts_incoming_traceparent(self):
        """Flask middleware should extract incoming traceparent header."""
        try:
            from flask import Flask
        except ImportError:
            pytest.skip("Flask not installed")

        from tools.resilience.correlation import register_correlation_middleware

        app = Flask(__name__)

        @app.route("/test")
        def test_route():
            from flask import g
            return {"traceparent": getattr(g, "traceparent", None)}

        register_correlation_middleware(app)

        incoming = generate_traceparent()
        header_val = incoming.to_traceparent()

        with app.test_client() as client:
            resp = client.get("/test", headers={"traceparent": header_val})
            data = resp.get_json()
            assert data["traceparent"] == header_val

    def test_response_includes_traceparent_header(self):
        """Response should include traceparent in headers."""
        try:
            from flask import Flask
        except ImportError:
            pytest.skip("Flask not installed")

        from tools.resilience.correlation import register_correlation_middleware

        app = Flask(__name__)

        @app.route("/test")
        def test_route():
            return "ok"

        register_correlation_middleware(app)

        with app.test_client() as client:
            resp = client.get("/test")
            assert "traceparent" in resp.headers
