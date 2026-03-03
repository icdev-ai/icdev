#!/usr/bin/env python3
# CUI // SP-CTI
"""W3C Trace Context propagation (D281).

Extends D149 correlation ID to W3C traceparent format for distributed tracing.
Backward compatible — existing correlation IDs continue to work alongside traceparent.

W3C traceparent format:
    {version}-{trace_id}-{parent_id}-{flags}
    00-4bf92f3577b58400000000000000000a-00f067aa0ba902b7-01

Usage:
    from tools.observability.trace_context import (
        generate_traceparent,
        parse_traceparent,
        get_current_context,
        set_current_context,
    )

    # Generate new trace context
    ctx = generate_traceparent()  # -> TraceContext(trace_id, span_id, sampled)

    # Parse incoming traceparent header
    ctx = parse_traceparent("00-abc123...-def456...-01")

    # Get/set in contextvars (propagates through async/thread boundaries)
    set_current_context(ctx)
    ctx = get_current_context()
"""

import contextvars
import uuid
from dataclasses import dataclass
from typing import Optional


# Version constant
TRACEPARENT_VERSION = "00"

# Context variable for propagation (works with threading and asyncio)
_trace_context_var: contextvars.ContextVar[Optional["TraceContext"]] = contextvars.ContextVar(
    "icdev_trace_context", default=None
)


@dataclass(frozen=True)
class TraceContext:
    """W3C Trace Context data (D281).

    Attributes:
        trace_id: 32-char hex string (128-bit trace identifier).
        span_id: 16-char hex string (64-bit span identifier).
        sampled: Whether this trace is sampled (included in export).
        tracestate: Optional vendor-specific tracestate header value.
    """
    trace_id: str
    span_id: str
    sampled: bool = True
    tracestate: str = ""

    def to_traceparent(self) -> str:
        """Format as W3C traceparent header value."""
        flags = "01" if self.sampled else "00"
        return f"{TRACEPARENT_VERSION}-{self.trace_id}-{self.span_id}-{flags}"

    def to_correlation_id(self) -> str:
        """Extract D149-compatible 12-char correlation ID from trace_id."""
        return self.trace_id[:12]

    def child(self, new_span_id: Optional[str] = None) -> "TraceContext":
        """Create a child context with same trace_id but new span_id."""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=new_span_id or generate_span_id(),
            sampled=self.sampled,
            tracestate=self.tracestate,
        )


def generate_trace_id() -> str:
    """Generate a 32-char hex trace ID (128-bit)."""
    return uuid.uuid4().hex


def generate_span_id() -> str:
    """Generate a 16-char hex span ID (64-bit)."""
    return uuid.uuid4().hex[:16]


def generate_traceparent(
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    sampled: bool = True,
) -> TraceContext:
    """Generate a new W3C trace context.

    Args:
        trace_id: Optional 32-char hex string. Generated if None.
        span_id: Optional 16-char hex string. Generated if None.
        sampled: Whether the trace is sampled. Default True.

    Returns:
        A new TraceContext.
    """
    return TraceContext(
        trace_id=trace_id or generate_trace_id(),
        span_id=span_id or generate_span_id(),
        sampled=sampled,
    )


def parse_traceparent(header: str) -> Optional[TraceContext]:
    """Parse a W3C traceparent header value.

    Args:
        header: traceparent string, e.g. "00-{trace_id}-{span_id}-{flags}"

    Returns:
        TraceContext if valid, None if malformed.
    """
    if not header:
        return None

    parts = header.strip().split("-")
    if len(parts) != 4:
        return None

    version, trace_id, span_id, flags = parts

    # Validate lengths
    if len(trace_id) != 32 or len(span_id) != 16 or len(flags) != 2:
        return None

    # Validate hex
    try:
        int(trace_id, 16)
        int(span_id, 16)
        int(flags, 16)
    except ValueError:
        return None

    # All-zero trace_id or span_id is invalid per spec
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None

    sampled = (int(flags, 16) & 0x01) == 1
    return TraceContext(trace_id=trace_id, span_id=span_id, sampled=sampled)


def get_current_context() -> Optional[TraceContext]:
    """Get the current trace context from contextvars.

    Falls back to Flask g context if available (D149 integration).
    """
    ctx = _trace_context_var.get()
    if ctx:
        return ctx

    # Try Flask g context for backward compat with D149
    try:
        from flask import g
        traceparent = getattr(g, "traceparent", None)
        if traceparent:
            return parse_traceparent(traceparent)
        # Fall back to correlation_id -> synthetic trace context
        cid = getattr(g, "correlation_id", None)
        if cid:
            # Pad correlation_id to 32 chars for trace_id
            padded_trace_id = (cid + "0" * 32)[:32]
            return TraceContext(
                trace_id=padded_trace_id,
                span_id=generate_span_id(),
                sampled=True,
            )
    except (ImportError, RuntimeError):
        pass

    return None


def set_current_context(ctx: TraceContext) -> contextvars.Token:
    """Set the current trace context in contextvars.

    Returns a token that can be used to reset the context.
    """
    return _trace_context_var.set(ctx)


def clear_current_context() -> None:
    """Clear the current trace context."""
    _trace_context_var.set(None)


def context_from_correlation_id(correlation_id: str) -> TraceContext:
    """Create a TraceContext from an existing D149 correlation ID.

    Pads the 12-char correlation ID to a 32-char trace_id.
    Used for backward compatibility during D149 → D281 transition.
    """
    padded = (correlation_id + "0" * 32)[:32]
    return TraceContext(
        trace_id=padded,
        span_id=generate_span_id(),
        sampled=True,
    )
