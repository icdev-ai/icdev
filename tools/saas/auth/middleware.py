#!/usr/bin/env python3
"""ICDEV SaaS — Authentication & Authorization Middleware.
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
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("saas.auth.middleware")

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


def _log_auth_event(tenant_id: Optional[str], user_id: Optional[str],
                    event_type: str, details: dict, ip_address: str):
    """Log authentication event to platform audit trail."""
    try:
        import sqlite3
        platform_db = Path(os.environ.get(
            "PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db")
        ))
        if not platform_db.exists():
            return
        conn = sqlite3.connect(str(platform_db))
        conn.execute("""
            INSERT INTO audit_platform (tenant_id, user_id, event_type, action, details, ip_address, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            tenant_id, user_id, event_type, details.get("action", event_type),
            json.dumps(details), ip_address,
            datetime.now(timezone.utc).isoformat()
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Could not log auth event: %s", e)


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
            _log_auth_event(None, None, "auth.failed", {
                "action": "missing_credentials",
                "path": path,
                "method": request.method,
            }, request.remote_addr or "unknown")
            return jsonify({"error": "Authentication required", "code": "AUTH_REQUIRED"}), 401

        # Validate credentials
        start = time.time()
        auth_info = _validate_credentials(creds)
        duration_ms = int((time.time() - start) * 1000)

        if not auth_info:
            _log_auth_event(None, None, "auth.failed", {
                "action": "invalid_credentials",
                "auth_method": creds.get("method"),
                "path": path,
                "duration_ms": duration_ms,
            }, request.remote_addr or "unknown")
            return jsonify({"error": "Invalid credentials", "code": "AUTH_INVALID"}), 401

        # Set tenant context on Flask g
        g.tenant_id = auth_info["tenant_id"]
        g.user_id = auth_info["user_id"]
        g.user_role = auth_info["role"]
        g.auth_info = auth_info

        # Check RBAC
        from tools.saas.auth.rbac import require_permission
        if not require_permission(
            role=auth_info["role"],
            path=path,
            method=request.method,
            user_id=auth_info["user_id"],
        ):
            _log_auth_event(auth_info["tenant_id"], auth_info["user_id"],
                          "auth.forbidden", {
                              "action": "permission_denied",
                              "path": path,
                              "method": request.method,
                              "role": auth_info["role"],
                          }, request.remote_addr or "unknown")
            return jsonify({
                "error": "Insufficient permissions",
                "code": "FORBIDDEN",
                "role": auth_info["role"],
                "path": path,
            }), 403

        # Log successful auth (debug level to avoid noise)
        logger.debug("Auth OK: tenant=%s user=%s role=%s method=%s path=%s",
                     auth_info["tenant_slug"], auth_info["email"],
                     auth_info["role"], request.method, path)

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
