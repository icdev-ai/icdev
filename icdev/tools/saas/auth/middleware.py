#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
"""ICDEV™ SaaS — Authentication & Authorization Middleware.
CUI // SP-CTI

Flask before_request middleware that:
1. Extracts credentials (API key, OAuth JWT, CAC/PIV cert)
2. Validates via the appropriate auth module
3. Sets tenant context on Flask g object
4. Checks RBAC permissions
5. Returns 401/403 for auth failures

Usage:
    from tools.saas.auth.middleware import register_auth_middleware
    register_auth_middleware(app)
"""

import json
import os
import sys
import time
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = get_logger("saas.auth.middleware")

# Public endpoints that do not require authentication
PUBLIC_ENDPOINTS = {
    "/health",
    "/api/v1/health",
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/metrics",
    "/portal",  # Portal uses its own session-based auth (_portal_auth_required)
}


def _is_public_endpoint(path: str) -> bool:
    """Check if the endpoint is public (no auth required)."""
    for public in PUBLIC_ENDPOINTS:
        if path == public or path.startswith(public + "/"):
            return True
    # POST /api/v1/tenants is semi-public (tenant creation)
    if path == "/api/v1/tenants" and _get_request_method() == "POST":
        return True
    return False


def _get_request_method() -> str:
    """Get current request method safely."""
    try:
        from flask import request

        return request.method
    except Exception:
        return "GET"


def _extract_credentials(request) -> Optional[dict]:
    """Extract authentication credentials from the request.

    Checks in order:
    1. Authorization: Bearer icdev_... -> API key
    2. Authorization: Bearer eyJ... -> OAuth JWT
    3. X-Client-Cert-CN header -> CAC/PIV
    """
    # Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token.startswith("psess_"):
            return {"method": "portal_session", "token": token}
        elif token.startswith("icdev_"):
            return {"method": "api_key", "token": token}
        elif token.startswith("eyJ"):
            return {"method": "oauth", "token": token}
        else:
            return {"method": "api_key", "token": token}  # assume API key

    # Check CAC/PIV cert header
    cac_cn = request.headers.get("X-Client-Cert-CN", "").strip()
    if cac_cn:
        serial = request.headers.get("X-Client-Cert-Serial", "").strip()
        return {"method": "cac_piv", "cn": cac_cn, "serial": serial}

    # Check query param (for SSE connections that cannot set headers easily)
    api_key = request.args.get("api_key", "").strip()
    if api_key:
        return {"method": "api_key", "token": api_key}

    return None


def _validate_credentials(creds: dict) -> Optional[dict]:
    """Validate credentials using the appropriate auth module."""
    method = creds.get("method")

    if method == "portal_session":
        # Validate opaque portal session token (Enhancement #1A)
        try:
            from tools.saas.portal.app import validate_portal_session_token

            sess = validate_portal_session_token(creds["token"])
            if sess:
                return {
                    "tenant_id": sess["tenant_id"],
                    "user_id": sess["user_id"],
                    "role": sess["role"],
                    "email": "portal-session",
                    "tenant_slug": "portal",
                }
        except ImportError:
            logger.debug("Portal session validation not available")
        return None

    if method == "api_key":
        from tools.saas.auth.api_key_auth import validate_api_key

        return validate_api_key(creds["token"])

    elif method == "oauth":
        from tools.saas.auth.oauth_auth import validate_oauth_token

        return validate_oauth_token(creds["token"])

    elif method == "cac_piv":
        from tools.saas.auth.cac_auth import validate_cac_cert

        return validate_cac_cert(creds["cn"], creds.get("serial"))

    return None


def _log_auth_event(tenant_id: Optional[str], user_id: Optional[str], event_type: str, details: dict, ip_address: str):
    """Log authentication event to platform audit trail."""
    try:
        platform_db = Path(os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db")))
        if not platform_db.exists():
            return
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO audit_platform (tenant_id, user_id, event_type, action, details, ip_address, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
            (
                tenant_id,
                user_id,
                event_type,
                details.get("action", event_type),
                json.dumps(details),
                ip_address,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Could not log auth event: %s", e)


def _check_api_key_scopes(scopes: list, method: str, path: str) -> bool:
    """G-04: Verify the API key's scope list permits the requested action.

    Scope format: ``["canvas:level", ...]`` e.g. ``["projects:read", "compliance:write"]``.
    An empty / absent scope list grants full access (backwards-compatible with
    legacy keys that predate scope enforcement).

    Rules:
    - ``*`` or ``*:*`` in scopes → allow all
    - ``canvas:write`` implies ``canvas:read`` (write >= read)
    - ``canvas:admin`` implies read + write
    - HTTP method mapping: GET/HEAD → read; POST/PUT/PATCH/DELETE → write
    - Scope canvas is matched as a prefix of the URL path segment after ``/api/v1/``
    """
    if not scopes:
        return True  # legacy key — no scope restriction

    if "*" in scopes or "*:*" in scopes:
        return True

    _METHOD_LEVEL = {
        "GET": "read", "HEAD": "read", "OPTIONS": "read",
        "POST": "write", "PUT": "write", "PATCH": "write", "DELETE": "write",
    }
    _LEVEL_ORDER = {"read": 0, "write": 1, "admin": 2}

    required_level = _METHOD_LEVEL.get(method.upper(), "write")

    # Extract canvas from path: /api/v1/<canvas>/... → canvas
    parts = path.strip("/").split("/")
    # strip "api" and version prefix (v1, v2, ...)
    while parts and parts[0] in ("api", "v1", "v2", "v3"):
        parts.pop(0)
    canvas = parts[0].replace("-", "_") if parts else ""

    for scope in scopes:
        try:
            scope_canvas, scope_level = scope.rsplit(":", 1)
        except ValueError:
            continue
        scope_canvas = scope_canvas.replace("-", "_")
        if scope_canvas not in ("*", canvas):
            continue
        if _LEVEL_ORDER.get(scope_level, -1) >= _LEVEL_ORDER.get(required_level, 0):
            return True

    logger.warning(
        "G-04 API key scope denied: path=%s method=%s required=%s scopes=%s",
        path, method, required_level, scopes,
    )
    return False


def _enforce_tenant_param(request, auth_tenant_id: str, path: str) -> None:
    """G-20: Detect and neutralise cross-tenant parameter injection.

    If the caller passes a tenant_id in query string, form body, or JSON body
    that differs from the auth-validated tenant_id, log a security warning.
    The parameter is not removed here (Flask makes requests immutable) — the
    handler must always use g.tenant_id, never the raw request param.

    Raises nothing — this is an audit/detect layer, not a blocking gate, so
    that an accidental mismatch in well-intentioned clients does not cause a
    500.  A future hardening pass can return 400 for confirmed mismatches.
    """
    for param_tenant in (
        request.args.get("tenant_id"),
        request.form.get("tenant_id") if request.method in ("POST", "PUT", "PATCH") else None,
    ):
        if param_tenant and param_tenant != auth_tenant_id:
            logger.warning(
                "G-20 cross-tenant param detected: auth_tenant=%s param_tenant=%s path=%s",
                auth_tenant_id,
                param_tenant,
                path,
            )
            break

    # JSON body check (only parse when content-type is JSON to avoid spurious errors)
    if request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            body_tenant = body.get("tenant_id") if isinstance(body, dict) else None
            if body_tenant and body_tenant != auth_tenant_id:
                logger.warning(
                    "G-20 cross-tenant JSON body detected: auth_tenant=%s body_tenant=%s path=%s",
                    auth_tenant_id,
                    body_tenant,
                    path,
                )
        except Exception:
            pass


def register_auth_middleware(app):
    """Register authentication middleware on a Flask app.

    Usage:
        app = Flask(__name__)
        register_auth_middleware(app)
    """
    from flask import g, request, jsonify

    @app.before_request
    def authenticate_request():
        """Authenticate every request before it reaches the handler."""
        path = request.path

        # Skip auth for public endpoints
        if _is_public_endpoint(path):
            g.tenant_id = None
            g.user_id = None
            g.user_role = None
            g.auth_info = None
            return None

        # Extract credentials
        creds = _extract_credentials(request)
        if not creds:
            _log_auth_event(
                None,
                None,
                "auth.failed",
                {
                    "action": "missing_credentials",
                    "path": path,
                    "method": request.method,
                },
                request.remote_addr or "unknown",
            )
            return jsonify({"error": "Authentication required", "code": "AUTH_REQUIRED"}), 401

        # Validate credentials
        start = time.time()
        auth_info = _validate_credentials(creds)
        duration_ms = int((time.time() - start) * 1000)

        if not auth_info:
            _log_auth_event(
                None,
                None,
                "auth.failed",
                {
                    "action": "invalid_credentials",
                    "auth_method": creds.get("method"),
                    "path": path,
                    "duration_ms": duration_ms,
                },
                request.remote_addr or "unknown",
            )
            return jsonify({"error": "Invalid credentials", "code": "AUTH_INVALID"}), 401

        # Set tenant context on Flask g
        g.tenant_id = auth_info["tenant_id"]
        g.user_id = auth_info["user_id"]
        g.user_role = auth_info["role"]
        g.auth_info = auth_info
        g.tenant_name = auth_info.get("tenant_name") or auth_info.get("tenant_slug") or "System"
        g.api_key_scopes = auth_info.get("scopes") or []

        # G-04: API key scope enforcement
        if creds.get("method") == "api_key" and not _check_api_key_scopes(
            g.api_key_scopes, request.method, path
        ):
            _log_auth_event(
                auth_info["tenant_id"],
                auth_info["user_id"],
                "auth.forbidden",
                {
                    "action": "scope_denied",
                    "path": path,
                    "method": request.method,
                    "scopes": g.api_key_scopes,
                },
                request.remote_addr or "unknown",
            )
            return jsonify(
                {
                    "error": "API key scope insufficient",
                    "code": "SCOPE_DENIED",
                    "path": path,
                }
            ), 403

        # Check RBAC
        from tools.saas.auth.rbac import require_permission

        if not require_permission(
            role=auth_info["role"],
            path=path,
            method=request.method,
            user_id=auth_info["user_id"],
        ):
            _log_auth_event(
                auth_info["tenant_id"],
                auth_info["user_id"],
                "auth.forbidden",
                {
                    "action": "permission_denied",
                    "path": path,
                    "method": request.method,
                    "role": auth_info["role"],
                },
                request.remote_addr or "unknown",
            )
            return jsonify(
                {
                    "error": "Insufficient permissions",
                    "code": "FORBIDDEN",
                    "role": auth_info["role"],
                    "path": path,
                }
            ), 403

        # G-20: Cross-tenant parameter tampering check.
        # Strip any tenant_id the caller injected into params/body and verify
        # it matches the auth-validated tenant to prevent privilege escalation.
        _enforce_tenant_param(request, auth_info["tenant_id"], path)

        # Log successful auth (debug level to avoid noise)
        logger.debug(
            "Auth OK: tenant=%s user=%s role=%s method=%s path=%s",
            auth_info["tenant_slug"],
            auth_info["email"],
            auth_info["role"],
            request.method,
            path,
        )

        return None  # Continue to handler

    @app.after_request
    def add_security_headers(response):
        """Add security headers to all responses."""
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        # CUI marking header
        response.headers["X-Classification"] = os.environ.get("CLASSIFICATION", "CUI")
        return response


def get_current_tenant_id() -> str | None:
    """Return the tenant_id for the current Flask request, or None for unauthenticated/system requests.

    All application blueprints should import only this function — no direct
    ``flask.g`` access in app code — so the source of truth is centralised here.
    """
    try:
        from flask import g
        return getattr(g, "tenant_id", None)
    except Exception:
        # RuntimeError: outside Flask request context (CLI/tests)
        # ImportError/ModuleNotFoundError: Flask not installed in this env
        return None


def get_current_tenant_name() -> str:
    """Return the human-readable tenant name for the current request, defaulting to 'System'."""
    try:
        from flask import g
        return getattr(g, "tenant_name", None) or "System"
    except Exception:
        return "System"
