#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Resilience — Correlation ID Middleware.

D149: Request-scoped correlation IDs propagated through Flask middleware,
A2A JSON-RPC metadata, and audit trail session_id.

Usage:
    from tools.resilience.correlation import register_correlation_middleware
    register_correlation_middleware(app)

    # Get current correlation ID anywhere in request context:
    from tools.resilience.correlation import get_correlation_id
    cid = get_correlation_id()
"""

import logging
import threading
import uuid
from typing import Optional

logger = logging.getLogger("icdev.resilience.correlation")

# Header name for propagation
CORRELATION_HEADER = "X-Correlation-ID"

# Thread-local storage for non-Flask contexts (background threads, CLI tools)
_thread_local = threading.local()


def generate_correlation_id() -> str:
    """Generate a 12-character correlation ID (UUID prefix)."""
    return uuid.uuid4().hex[:12]


def get_correlation_id() -> Optional[str]:
    """Get the current correlation ID.

    Checks (in order):
    1. Flask request context (g.correlation_id)
    2. Thread-local storage
    3. Returns None if no correlation context exists
    """
    # Try Flask g context first
    try:
        from flask import g
        cid = getattr(g, "correlation_id", None)
        if cid:
            return cid
    except (ImportError, RuntimeError):
        pass  # Not in Flask context

    # Fall back to thread-local
    return getattr(_thread_local, "correlation_id", None)


def set_correlation_id(correlation_id: str):
    """Set the correlation ID in thread-local storage.

    Use this for non-Flask contexts (background threads, CLI tools).
    """
    _thread_local.correlation_id = correlation_id


def clear_correlation_id():
    """Clear the thread-local correlation ID."""
    _thread_local.correlation_id = None


def register_correlation_middleware(app):
    """Register correlation ID middleware on a Flask app.

    Generates or extracts a correlation ID for each request and adds it
    to the response headers. Must be registered BEFORE auth middleware
    so that all downstream middleware and route handlers have access.
    """
    from flask import g, request

    @app.before_request
    def _inject_correlation_id():
        # Check for incoming correlation ID (from A2A agent calls)
        cid = request.headers.get(CORRELATION_HEADER)
        if not cid:
            cid = generate_correlation_id()
        g.correlation_id = cid
        # Also set thread-local for libraries that don't have Flask context
        _thread_local.correlation_id = cid

    @app.after_request
    def _add_correlation_header(response):
        cid = getattr(g, "correlation_id", None)
        if cid:
            response.headers[CORRELATION_HEADER] = cid
        return response

    @app.teardown_request
    def _clear_correlation(exc=None):
        _thread_local.correlation_id = None


class CorrelationLogFilter(logging.Filter):
    """Logging filter that injects correlation_id into log records.

    Usage:
        handler = logging.StreamHandler()
        handler.addFilter(CorrelationLogFilter())
        formatter = logging.Formatter(
            "%(asctime)s [%(correlation_id)s] %(name)s: %(message)s"
        )
        handler.setFormatter(formatter)
    """

    def filter(self, record):
        record.correlation_id = get_correlation_id() or "-"
        return True
