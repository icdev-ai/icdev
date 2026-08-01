# CUI // SP-CTI
"""Shared Flask route helpers extracted from tools/dashboard/app.py (nav-misc-03).

These are module-level route decorators/helpers used by multiple route groups.
Housed here (rather than in app.py) so blueprint modules can import them without
a circular dependency on the app factory. Pure move — no logic changes.
"""
from __future__ import annotations

from functools import wraps

from flask import jsonify


def _module_not_installed(slug: str):
    """Return a friendly error when a marketplace module is not installed."""
    return jsonify({"error": f"Module '{slug}' is not installed. Install via marketplace."}), 501


def require_installed(slug):
    """Route decorator - catches ImportError when module code is missing."""

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except ImportError as exc:
                if slug in str(exc) or f"tools.{slug}" in str(exc) or f"tools/{slug}" in str(exc):
                    return _module_not_installed(slug)
                raise

        return wrapper

    return decorator
