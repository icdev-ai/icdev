#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for ICDEV Trace Context — W3C traceparent (D281).

Covers:
  - TraceContext creation and serialization
  - traceparent parsing (valid + invalid)
  - traceparent generation
  - Context propagation via contextvars
  - Backward compat with D149 correlation ID
  - Child context creation
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.observability.trace_context import (
    TraceContext,
    clear_current_context,
    context_from_correlation_id,
    generate_span_id,
    generate_trace_id,
    generate_traceparent,
    get_current_context,
    parse_traceparent,
    set_current_context,
)


class TestTraceContext:
    def test_to_traceparent_sampled(self):
        ctx = TraceContext(
            trace_id="a" * 32,
            span_id="b" * 16,
            sampled=True,
        )
        assert ctx.to_traceparent() == f"00-{'a' * 32}-{'b' * 16}-01"

    def test_to_traceparent_not_sampled(self):
        ctx = TraceContext(
            trace_id="a" * 32,
            span_id="b" * 16,
            sampled=False,
        )
        assert ctx.to_traceparent() == f"00-{'a' * 32}-{'b' * 16}-00"

    def test_to_correlation_id(self):
        ctx = TraceContext(trace_id="abcdef123456" + "0" * 20, span_id="x" * 16)
        assert ctx.to_correlation_id() == "abcdef123456"

    def test_child_context(self):
        parent = TraceContext(trace_id="a" * 32, span_id="b" * 16)
        child = parent.child()
        assert child.trace_id == parent.trace_id
        assert child.span_id != parent.span_id
        assert len(child.span_id) == 16

    def test_child_context_custom_span_id(self):
        parent = TraceContext(trace_id="a" * 32, span_id="b" * 16)
        child = parent.child(new_span_id="c" * 16)
        assert child.span_id == "c" * 16

    def test_frozen_dataclass(self):
        ctx = TraceContext(trace_id="a" * 32, span_id="b" * 16)
        with pytest.raises(AttributeError):
            ctx.trace_id = "new_value"


class TestGenerateTraceId:
    def test_length(self):
        tid = generate_trace_id()
        assert len(tid) == 32

    def test_is_hex(self):
        tid = generate_trace_id()
        int(tid, 16)  # Should not raise

    def test_unique(self):
        ids = {generate_trace_id() for _ in range(100)}
        assert len(ids) == 100


class TestGenerateSpanId:
    def test_length(self):
        sid = generate_span_id()
        assert len(sid) == 16

    def test_is_hex(self):
        sid = generate_span_id()
        int(sid, 16)  # Should not raise


class TestGenerateTraceparent:
    def test_defaults(self):
        ctx = generate_traceparent()
        assert len(ctx.trace_id) == 32
        assert len(ctx.span_id) == 16
        assert ctx.sampled is True

    def test_custom_values(self):
        ctx = generate_traceparent(
            trace_id="a" * 32,
            span_id="b" * 16,
            sampled=False,
        )
        assert ctx.trace_id == "a" * 32
        assert ctx.span_id == "b" * 16
        assert ctx.sampled is False


class TestParseTraceparent:
    def test_valid_sampled(self):
        header = f"00-{'a' * 32}-{'b' * 16}-01"
        ctx = parse_traceparent(header)
        assert ctx is not None
        assert ctx.trace_id == "a" * 32
        assert ctx.span_id == "b" * 16
        assert ctx.sampled is True

    def test_valid_not_sampled(self):
        header = f"00-{'a' * 32}-{'b' * 16}-00"
        ctx = parse_traceparent(header)
        assert ctx is not None
        assert ctx.sampled is False

    def test_empty_string(self):
        assert parse_traceparent("") is None

    def test_none(self):
        assert parse_traceparent(None) is None

    def test_malformed_too_few_parts(self):
        assert parse_traceparent("00-abc-def") is None

    def test_malformed_wrong_trace_id_length(self):
        assert parse_traceparent(f"00-{'a' * 31}-{'b' * 16}-01") is None

    def test_malformed_wrong_span_id_length(self):
        assert parse_traceparent(f"00-{'a' * 32}-{'b' * 15}-01") is None

    def test_all_zero_trace_id_invalid(self):
        assert parse_traceparent(f"00-{'0' * 32}-{'b' * 16}-01") is None

    def test_all_zero_span_id_invalid(self):
        assert parse_traceparent(f"00-{'a' * 32}-{'0' * 16}-01") is None

    def test_non_hex_characters(self):
        assert parse_traceparent(f"00-{'g' * 32}-{'b' * 16}-01") is None

    def test_roundtrip(self):
        original = generate_traceparent()
        header = original.to_traceparent()
        parsed = parse_traceparent(header)
        assert parsed.trace_id == original.trace_id
        assert parsed.span_id == original.span_id
        assert parsed.sampled == original.sampled


class TestContextVarPropagation:
    def test_set_and_get_context(self):
        clear_current_context()
        ctx = generate_traceparent()
        set_current_context(ctx)
        retrieved = get_current_context()
        assert retrieved is not None
        assert retrieved.trace_id == ctx.trace_id
        clear_current_context()

    def test_clear_context(self):
        ctx = generate_traceparent()
        set_current_context(ctx)
        clear_current_context()
        assert get_current_context() is None

    def test_get_context_when_empty(self):
        clear_current_context()
        # Outside Flask, should return None
        assert get_current_context() is None


class TestContextFromCorrelationId:
    def test_pads_to_32_chars(self):
        ctx = context_from_correlation_id("abcdef123456")
        assert len(ctx.trace_id) == 32
        assert ctx.trace_id.startswith("abcdef123456")

    def test_preserves_correlation_id(self):
        cid = "abcdef123456"
        ctx = context_from_correlation_id(cid)
        assert ctx.to_correlation_id() == cid

    def test_sampled_by_default(self):
        ctx = context_from_correlation_id("abc123")
        assert ctx.sampled is True
