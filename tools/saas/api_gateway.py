#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV SaaS -- API Gateway.

Main Flask application that assembles the SaaS API gateway.  Registers
authentication middleware, rate limiter, request logger, REST API blueprint,
MCP Streamable HTTP blueprint, and (optionally) the portal blueprint.

Features:
    - CUI security headers on all responses
    - CORS support with configurable origins
    - Health check at GET /health
    - CLI entry point with argparse (port, debug, workers)

Usage:
    # Development
    python tools/saas/api_gateway.py --port 8443 --debug

    # Production (gunicorn)
    gunicorn "tools.saas.api_gateway:create_app()" \\
        --bind 0.0.0.0:8443 --workers 4 --certfile cert.pem --keyfile key.pem

    # Show help
    python tools/saas/api_gateway.py --help
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("saas.gateway")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GATEWAY_VERSION = "1.0.0"
GATEWAY_NAME = "icdev-saas-gateway"
DEFAULT_PORT = 8443
PLATFORM_DB_PATH = Path(
    os.environ.get("PLATFORM_DB_PATH", str(BASE_DIR / "data" / "platform.db"))
)

# Track server start time for uptime reporting
_start_time = time.time()


# ---------------------------------------------------------------------------
# Platform DB helper
# ---------------------------------------------------------------------------
def _get_platform_db():
    """Get a connection to the platform database for health checks."""
    if not PLATFORM_DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(str(PLATFORM_DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _check_platform_db_health():
    """Quick health check on the platform database."""
    conn = _get_platform_db()
    if conn is None:
        return {"status": "unavailable", "message": "Platform DB not found"}
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM tenants").fetchone()
        tenant_count = row["cnt"] if row else 0
        conn.close()
        return {"status": "ok", "tenant_count": tenant_count}
    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# CORS configuration
# ---------------------------------------------------------------------------
def _get_allowed_origins():
    """Read CORS allowed origins from environment or default."""
    origins_env = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    if origins_env:
        return [o.strip() for o in origins_env.split(",") if o.strip()]
    # Default: allow localhost for development
    return [
        "http://localhost:3000",
        "http://localhost:5000",
        "http://localhost:8080",
        "https://localhost:8443",
    ]


def _register_cors(app):
    """Register CORS headers on all responses."""
    allowed_origins = _get_allowed_origins()

    @app.after_request
    def _add_cors_headers(response):
        origin = None
        request_origin = None
        try:
            from flask import request as flask_request
            request_origin = flask_request.headers.get("Origin", "")
        except Exception:
            pass

        # Check if origin is in allowed list (or wildcard for dev)
        if request_origin:
            if request_origin in allowed_origins or "*" in allowed_origins:
                origin = request_origin

        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PATCH, PUT, DELETE, OPTIONS"
            )
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, X-Request-ID, X-Client-Cert-CN, "
                "X-Client-Cert-Serial"
            )
            response.headers["Access-Control-Max-Age"] = "3600"
            response.headers["Access-Control-Allow-Credentials"] = "true"

        return response

    @app.before_request
    def _handle_preflight():
        from flask import request as flask_request
        if flask_request.method == "OPTIONS":
            from flask import make_response
            resp = make_response("", 204)
            return resp
        return None


# ---------------------------------------------------------------------------
# CUI security headers
# ---------------------------------------------------------------------------
def _register_cui_headers(app):
    """Add CUI/classification headers to all responses."""
    classification = os.environ.get("CLASSIFICATION", "CUI // SP-CTI")

    @app.after_request
    def _add_cui_headers(response):
        response.headers["X-Classification"] = classification
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=()"
        )
        # ICDEV gateway identification
        response.headers["X-Powered-By"] = GATEWAY_NAME
        response.headers["X-Gateway-Version"] = GATEWAY_VERSION
        return response


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------
def _register_error_handlers(app):
    """Register global JSON error handlers."""
    from flask import jsonify

    @app.errorhandler(400)
    def bad_request(exc):
        return jsonify({
            "error": "Bad request",
            "code": "BAD_REQUEST",
            "details": str(exc),
        }), 400

    @app.errorhandler(404)
    def not_found(exc):
        return jsonify({
            "error": "Not found",
            "code": "NOT_FOUND",
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(exc):
        return jsonify({
            "error": "Method not allowed",
            "code": "METHOD_NOT_ALLOWED",
        }), 405

    @app.errorhandler(429)
    def rate_limited(exc):
        return jsonify({
            "error": "Rate limit exceeded",
            "code": "RATE_LIMITED",
        }), 429

    @app.errorhandler(500)
    def internal_error(exc):
        logger.error("Internal server error: %s", exc)
        return jsonify({
            "error": "Internal server error",
            "code": "INTERNAL_ERROR",
        }), 500


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
def _register_health_check(app):
    """Register the /health endpoint."""
    from flask import jsonify

    @app.route("/health", methods=["GET"])
    def health_check():
        """GET /health -- Gateway health check with uptime and DB status."""
        uptime_seconds = int(time.time() - _start_time)
        db_health = _check_platform_db_health()

        overall_status = "ok" if db_health["status"] == "ok" else "degraded"

        return jsonify({
            "status": overall_status,
            "service": GATEWAY_NAME,
            "version": GATEWAY_VERSION,
            "uptime_seconds": uptime_seconds,
            "uptime_human": _format_uptime(uptime_seconds),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "classification": os.environ.get("CLASSIFICATION", "CUI // SP-CTI"),
            "components": {
                "platform_db": db_health,
                "api": {"status": "ok"},
                "mcp": {"status": "ok"},
            },
        })


def _format_uptime(seconds):
    """Format seconds into a human-readable uptime string."""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if days > 0:
        parts.append("{}d".format(days))
    if hours > 0:
        parts.append("{}h".format(hours))
    if minutes > 0:
        parts.append("{}m".format(minutes))
    parts.append("{}s".format(secs))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(config=None):
    """Flask application factory for the ICDEV SaaS API Gateway.

    Creates and configures the Flask app with all middleware, blueprints,
    error handlers, and health checks.

    Args:
        config: Optional dict of Flask configuration overrides.

    Returns:
        Configured Flask app instance.
    """
    from flask import Flask

    app = Flask(__name__)

    # Base configuration
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max request

    # Load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            load_dotenv(str(env_path))
            logger.info("Loaded .env from %s", env_path)
    except ImportError:
        pass

    # Apply any config overrides
    if config:
        app.config.update(config)

    # ---- Register middleware (order matters) ----

    # 1. Auth middleware (sets g.tenant_id, g.user_id, g.user_role)
    try:
        from tools.saas.auth.middleware import register_auth_middleware
        register_auth_middleware(app)
        logger.info("Auth middleware registered")
    except ImportError as exc:
        logger.warning("Auth middleware not available: %s", exc)

    # 2. Rate limiter (requires auth context from step 1)
    try:
        from tools.saas.rate_limiter import register_rate_limiter
        register_rate_limiter(app)
        logger.info("Rate limiter registered")
    except ImportError as exc:
        logger.warning("Rate limiter not available: %s", exc)

    # 3. Request logger (logs all authenticated requests)
    try:
        from tools.saas.request_logger import register_request_logger
        register_request_logger(app)
        logger.info("Request logger registered")
    except ImportError as exc:
        logger.warning("Request logger not available: %s", exc)

    # ---- CORS ----
    _register_cors(app)
    logger.info("CORS configured: origins=%s", _get_allowed_origins())

    # ---- CUI security headers ----
    _register_cui_headers(app)
    logger.info("CUI security headers registered")

    # ---- Error handlers ----
    _register_error_handlers(app)

    # ---- Health check ----
    _register_health_check(app)

    # ---- Register blueprints ----

    # REST API v1
    try:
        from tools.saas.rest_api import api_bp
        app.register_blueprint(api_bp)
        logger.info("REST API v1 blueprint registered at /api/v1")
    except ImportError as exc:
        logger.warning("REST API blueprint not available: %s", exc)

    # MCP Streamable HTTP (spec 2025-03-26)
    try:
        from tools.saas.mcp_http import mcp_bp
        app.register_blueprint(mcp_bp)
        logger.info("MCP Streamable HTTP blueprint registered at /mcp/v1")
    except ImportError as exc:
        logger.warning("MCP Streamable HTTP blueprint not available: %s", exc)

    # Portal (optional web UI)
    try:
        from tools.saas.portal import portal_bp
        app.register_blueprint(portal_bp)
        logger.info("Portal blueprint registered")
    except ImportError:
        logger.debug("Portal blueprint not found (optional)")

    # ---- Startup banner ----
    logger.info(
        "ICDEV SaaS API Gateway v%s initialized "
        "(CUI // SP-CTI)",
        GATEWAY_VERSION,
    )

    return app


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    """CLI entry point for running the ICDEV SaaS API Gateway."""
    parser = argparse.ArgumentParser(
        description=(
            "CUI // SP-CTI -- ICDEV SaaS API Gateway\n\n"
            "Multi-tenant REST + MCP Streamable HTTP gateway for the ICDEV platform."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Production deployment:\n"
            "  gunicorn 'tools.saas.api_gateway:create_app()' \\\n"
            "    --bind 0.0.0.0:8443 --workers 4 \\\n"
            "    --certfile cert.pem --keyfile key.pem\n"
        ),
    )
    parser.add_argument(
        "--port", type=int,
        default=int(os.environ.get("GATEWAY_PORT", str(DEFAULT_PORT))),
        help="Port to listen on (default: {}, env: GATEWAY_PORT)".format(
            DEFAULT_PORT),
    )
    parser.add_argument(
        "--host", type=str,
        default=os.environ.get("GATEWAY_HOST", "0.0.0.0"),
        help="Host to bind to (default: 0.0.0.0, env: GATEWAY_HOST)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        default=os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true"),
        help="Enable Flask debug mode (env: FLASK_DEBUG)",
    )
    parser.add_argument(
        "--workers", type=int, default=1,
        help="Number of workers (only applies to gunicorn, ignored in dev mode)",
    )
    parser.add_argument(
        "--ssl-cert", type=str, default=None,
        help="Path to SSL certificate file",
    )
    parser.add_argument(
        "--ssl-key", type=str, default=None,
        help="Path to SSL private key file",
    )

    args = parser.parse_args()

    # Create app
    app = create_app()

    # SSL context
    ssl_context = None
    if args.ssl_cert and args.ssl_key:
        ssl_context = (args.ssl_cert, args.ssl_key)
        logger.info("SSL enabled: cert=%s key=%s", args.ssl_cert, args.ssl_key)
    elif not args.debug:
        logger.warning(
            "Running without SSL. In production, use --ssl-cert and --ssl-key "
            "or deploy behind a TLS-terminating reverse proxy."
        )

    # Print startup info
    scheme = "https" if ssl_context else "http"
    logger.info(
        "Starting ICDEV SaaS API Gateway on %s://%s:%d (debug=%s)",
        scheme, args.host, args.port, args.debug,
    )
    logger.info("Health check: %s://%s:%d/health", scheme, args.host, args.port)
    logger.info("REST API:     %s://%s:%d/api/v1/", scheme, args.host, args.port)
    logger.info("MCP HTTP:     %s://%s:%d/mcp/v1/", scheme, args.host, args.port)

    # Run Flask dev server
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        ssl_context=ssl_context,
        threaded=True,
    )


if __name__ == "__main__":
    main()
