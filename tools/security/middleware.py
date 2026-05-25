#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
"""Security middleware for ICDEV™ — Flask before/after_request hooks.

Wires:
1. ``before_request``: extract SecurityContext from request, attach to ``g``.
2. ``before_request``: run MAC check on route-level classification ceiling.
3. ``after_request``: run field-level filtering on JSON responses.
4. ``after_request``: add ``X-Classification-Ceiling`` header.

Usage:
    from tools.security.middleware import init_security
    init_security(app, classification="CUI")
"""

import logging
from typing import Any, Optional, Set

logger = get_logger("security.middleware")

# Public endpoints that skip MAC/field checks (auth still runs separately)
DEFAULT_PUBLIC_ENDPOINTS = {
    "/health",
    "/api/v1/health",
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/metrics",
    "/portal",
    "/static/",
    "/favicon",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_public_endpoint(path: str, extra: Optional[Set[str]] = None) -> bool:
    """Check if path is public."""
    public = set(DEFAULT_PUBLIC_ENDPOINTS)
    if extra:
        public |= extra
    for p in public:
        if path == p or path.startswith(p + "/"):
            return True
    return False


def _wants_json(request) -> bool:
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.is_json


# ---------------------------------------------------------------------------
# init_security
# ---------------------------------------------------------------------------

def init_security(
    app: Any,
    classification: str = "CUI",
    public_endpoints: Optional[Set[str]] = None,
) -> None:
    """Register security middleware on a Flask app.

    Args:
        app: Flask application instance.
        classification: Classification ceiling for this app.
        public_endpoints: Additional public endpoint prefixes.
    """
    try:
        from flask import g, request, jsonify, make_response, abort
    except ImportError:
        logger.warning("Flask not available — skipping security middleware registration")
        return

    from tools.security.security_context import SecurityContext, attach_to_flask_g
    from tools.security.classification_enforcer import can_read
    from tools.security.field_security import field_security_after_request

    app.config["ICDEV_CLASSIFICATION"] = classification.upper()
    app.config["ICDEV_SECURITY_INITIALIZED"] = True

    @app.before_request
    def _security_before_request() -> Optional[Any]:
        path = request.path

        # Extract and attach security context
        ctx: Optional[SecurityContext] = None

        # Derive from existing auth middleware fields on g
        user = getattr(g, "current_user", None) or {}
        tenant_id = getattr(g, "tenant_id", None)
        user_role = getattr(g, "user_role", None) or user.get("role", "")
        user_id = getattr(g, "user_id", None) or user.get("id", "") or user.get("user_id", "")
        compartments = set(user.get("compartments", []))
        extra = getattr(g, "compartments", None)
        if isinstance(extra, (list, tuple, set)):
            compartments |= set(extra)

        user_classification = user.get("classification", classification)
        try:
            from tools.compliance.classification_manager import get_clearance_order
            clearance = get_clearance_order(user_classification)
        except Exception:
            clearance = {"PUBLIC": 0, "CUI": 1, "SECRET": 2, "TOP SECRET": 3, "TOP SECRET//SCI": 4}.get(
                user_classification.upper(), 1
            )

        mtls_cn = request.headers.get("X-Client-Cert-CN", None)
        auth_method = user.get("auth_method", "")

        ctx = SecurityContext(
            user_id=str(user_id) if user_id else "",
            role=str(user_role) if user_role else "",
            clearance_level=clearance,
            compartments=frozenset(compartments),
            impact_level=user.get("impact_level", "IL4"),
            tenant_id=str(tenant_id) if tenant_id else None,
            classification=user_classification.upper(),
            mtls_cn=mtls_cn,
            auth_method=auth_method,
        )
        attach_to_flask_g(ctx)

        # Skip MAC checks for public endpoints
        if _is_public_endpoint(path, public_endpoints):
            return None

        # MAC check — classification ceiling (no read-up)
        if not can_read(classification, ctx):
            logger.warning(
                "MAC ceiling denial: user %s clearance %s < app classification %s",
                ctx.user_id,
                ctx.clearance_level,
                classification,
            )
            if _wants_json(request):
                return make_response(
                    jsonify({"error": "Insufficient clearance for this endpoint", "code": "MAC_CEILING_DENIED"}),
                    403,
                )
            abort(403, description="Insufficient clearance")

        return None

    @app.after_request
    def _security_after_request(response) -> Any:
        # Classification ceiling header
        response.headers["X-Classification-Ceiling"] = app.config.get("ICDEV_CLASSIFICATION", "CUI")

        # Field-level filtering for JSON responses
        response = field_security_after_request(response)

        # Security headers (hardening)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate")
        return response


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------
# Some child apps import init_security from tools.security.middleware.
# The signature above is a drop-in replacement.
