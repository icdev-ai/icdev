#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Tracer ABCs — Pluggable tracing abstraction (D280).

Follows the Haystack ProxyTracer pattern:
  - Span ABC: represents a single operation with attributes, events, status
  - Tracer ABC: creates spans, manages lifecycle
  - NullTracer/NullSpan: zero-overhead no-op implementations
  - ProxyTracer: delegates to actual tracer, swappable at runtime

Content tagging follows Haystack's set_content_tag() pattern (D282):
  - SHA-256 hash always recorded
  - Plaintext only when ICDEV_CONTENT_TRACING_ENABLED=true

Architecture decisions:
  D280 — Pluggable Tracer ABC (consistent with D66 LLMProvider, D116 BaseAssessor)
  D282 — Content tracing opt-in via env var (CUI environments)
"""

import hashlib
import os
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


# Content tracing gate (D282)
def _content_tracing_enabled() -> bool:
    """Check if plaintext content tracing is enabled."""
    return os.environ.get("ICDEV_CONTENT_TRACING_ENABLED", "").lower() in (
        "true", "1", "yes",
    )


def set_content_tag(span: "Span", key: str, value: str) -> None:
    """Set a content tag on a span with privacy gating (D282).

    Always records SHA-256 hash as {key}_hash.
    Only records plaintext value when ICDEV_CONTENT_TRACING_ENABLED=true.

    Args:
        span: The span to tag.
        key: Attribute key (e.g., "gen_ai.prompt").
        value: The content value.
    """
    content_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
    span.set_attribute(f"{key}_hash", content_hash)
    if _content_tracing_enabled():
        span.set_attribute(key, value)


class Span(ABC):
    """Abstract base for a trace span (D280)."""

    @property
    @abstractmethod
    def span_id(self) -> str:
        """Return the unique span identifier (hex string)."""

    @property
    @abstractmethod
    def trace_id(self) -> str:
        """Return the trace identifier (32-char hex string)."""

    @property
    @abstractmethod
    def parent_span_id(self) -> Optional[str]:
        """Return the parent span ID, or None for root spans."""

    @abstractmethod
    def set_attribute(self, key: str, value: Any) -> None:
        """Set a key-value attribute on this span."""

    @abstractmethod
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Record a timestamped event on this span."""

    @abstractmethod
    def set_status(self, code: str, message: str = "") -> None:
        """Set span status: UNSET, OK, or ERROR."""

    @abstractmethod
    def end(self) -> None:
        """End this span and record its duration."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.set_status("ERROR", str(exc_val) if exc_val else "")
        elif self._raw_status_code() == "UNSET":
            self.set_status("OK")
        self.end()
        return False

    def _raw_status_code(self) -> str:
        """Return current status code. Subclasses should override."""
        return "UNSET"


class NullSpan(Span):
    """No-op span that discards all data (D280 fallback)."""

    def __init__(self, trace_id: str = "", span_id: str = "", parent_span_id: Optional[str] = None):
        self._trace_id = trace_id or uuid.uuid4().hex
        self._span_id = span_id or uuid.uuid4().hex[:16]
        self._parent_span_id = parent_span_id
        self._status_code = "UNSET"

    @property
    def span_id(self) -> str:
        return self._span_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def parent_span_id(self) -> Optional[str]:
        return self._parent_span_id

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def set_status(self, code: str, message: str = "") -> None:
        self._status_code = code

    def end(self) -> None:
        pass

    def _raw_status_code(self) -> str:
        return self._status_code


class Tracer(ABC):
    """Abstract base for a tracer backend (D280)."""

    @abstractmethod
    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        kind: str = "INTERNAL",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        """Create and start a new span.

        Args:
            name: Operation name (e.g., "mcp.tool_call", "gen_ai.invoke").
            parent: Parent span for nesting, or None for root span.
            kind: Span kind — INTERNAL, CLIENT, SERVER, PRODUCER, CONSUMER.
            attributes: Initial attributes dict.

        Returns:
            A started Span instance (use as context manager).
        """

    @abstractmethod
    def get_active_span(self) -> Optional[Span]:
        """Return the currently active span, or None."""

    @abstractmethod
    def flush(self) -> None:
        """Flush any buffered spans to the backend."""


class NullTracer(Tracer):
    """No-op tracer that creates NullSpans (D280 fallback)."""

    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        kind: str = "INTERNAL",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> NullSpan:
        trace_id = parent.trace_id if parent else uuid.uuid4().hex
        parent_id = parent.span_id if parent else None
        return NullSpan(
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_id,
        )

    def get_active_span(self) -> Optional[Span]:
        return None

    def flush(self) -> None:
        pass


class ProxyTracer(Tracer):
    """Runtime-swappable tracer proxy (Haystack pattern, D280).

    Starts as NullTracer. Call set_tracer() to activate a real backend.
    All calls delegate to the underlying tracer.
    """

    def __init__(self):
        self._actual: Tracer = NullTracer()

    def set_tracer(self, tracer: Tracer) -> None:
        """Set the actual tracer backend."""
        self._actual = tracer

    @property
    def actual(self) -> Tracer:
        """Return the underlying tracer (for type checking/inspection)."""
        return self._actual

    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        kind: str = "INTERNAL",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        return self._actual.start_span(name, parent=parent, kind=kind, attributes=attributes)

    def get_active_span(self) -> Optional[Span]:
        return self._actual.get_active_span()

    def flush(self) -> None:
        self._actual.flush()
