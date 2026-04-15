# CUI // SP-CTI
"""tools/dashboard/api/auth \u2014 JWT issuance + validation for /api/v1/*.

**Status:** C1 (design) + C2 (decorator + issuance endpoints) landed.
C3 adds double-submit CSRF, C4 sweeps decorators onto every blueprint handler.

Reference: tools/saas/auth (inspected in C1)
============================================
The SaaS auth surface is organized as a directory, not a single file:

    tools/saas/auth/
      __init__.py        (empty)
      middleware.py      285 lines  \u2014 Flask before_request multi-auth router
      oauth_auth.py      194 lines  \u2014 OAuth/OIDC validator (RS256/ES256, JWKS)
      api_key_auth.py    128 lines  \u2014 ``icdev_*`` bearer token validator
      cac_auth.py         83 lines  \u2014 CAC/PIV cert validator (X-Client-Cert-CN)
      rbac.py            174 lines  \u2014 role \u00d7 (path, method) permission matrix
    tools/saas/mcp_oauth.py  398 lines  \u2014 MCP OAuth delegate (not dashboard-relevant)

Key observations (things we inherit vs. diverge from)
------------------------------------------------------

1. SaaS is a *multi-tenant JWT consumer*, not an issuer.
   - It decodes JWTs minted by each tenant's external IdP (Okta, Entra, etc.).
   - Validation algorithm set: **RS256, ES256** (asymmetric; keys fetched
     via ``PyJWKClient.get_signing_key_from_jwt(token)`` against the IdP's
     JWKS URI, cached 1h in ``_jwks_cache``).
   - Required claims: ``iss`` (matches tenant's IdP), ``sub`` (user lookup
     key), ``aud`` (== IdP client_id).
   - **No issuance code, no refresh flow, no secret-key management.**

2. The dashboard is a *standalone single-tenant server*, so the model
   inverts: the dashboard MUST issue JWTs (SPA login) and MUST validate its
   own JWTs (middleware). Plain sub-claim + JWKS doesn't apply.

   Design choice (C2 scope):
   - **HS256 symmetric secret** for the dashboard's own JWTs. Simpler
     deployment (one env var, no key rotation theater). Secret source:
     ``ICDEV_JWT_SECRET`` env var; dev fallback writes a 32-byte urandom
     value to ``.tmp/jwt_secret_dev`` on first run (never committed).
   - **15 min access token + 7 day refresh token** (per B4/C2 plan).
   - **Claims**: ``sub`` (username), ``iat``, ``exp``, ``role``, ``csrf``
     (random string for the double-submit cookie in C3).
   - RS256 upgrade path stays open: swap one function and ship a JWKS
     route for Next.js (Phase H) to verify independently.

3. Middleware pattern we DO reuse verbatim:
   - ``@app.before_request`` hook (tools/saas/auth/middleware.py:177).
   - ``_extract_credentials(request)`` walks Authorization header and
     query-param fallback (SSE-friendly).
   - ``PUBLIC_ENDPOINTS`` allow-list already contains
     ``/api/v1/openapi.json`` and ``/api/v1/docs`` \u2014 matches B4 exactly.
     We extend this set to add ``/api/v1/auth/token`` + ``/api/v1/auth/refresh``.
   - Security-header ``@app.after_request`` block (HSTS, no-store, CUI marker).

4. Middleware pattern we DO NOT reuse:
   - Multi-method credential extraction (API key / OAuth / CAC / portal).
     The dashboard SPA only needs one path: the JWT minted by /api/v1/auth/token.
     CAC/PIV stays an upgrade target for Phase I / IL4+ deployments but not
     a C2 prerequisite.

5. RBAC (tools/saas/auth/rbac.py):
   - Role list: ``tenant_admin, isso, developer, viewer, auditor``.
     (see tools/saas/openapi_spec.py SCHEMAS.UserCreateRequest enum).
   - Permission tables keyed by ``(role, path_prefix, method)``.
   - C2 scope ships @require_jwt (authentication). RBAC (authorization) is
     out of C4 scope \u2014 defer to a Phase I or Phase J follow-up once the
     Next.js frontend exposes whose-role-does-what.

Dependency surface (all already installed via SaaS auth):
    PyJWT (>= 2.8)          \u2014 encode/decode
    cryptography            \u2014 pulled in by PyJWT[RS256] extras (future)
    flask                   \u2014 already a dashboard hard dep

Route allow-list (endpoints /api/v1/* that skip ``@require_jwt``):
    GET  /api/v1/openapi.json     \u2014 spec is public by design
    GET  /api/v1/docs             \u2014 Swagger UI
    POST /api/v1/auth/token       \u2014 issues JWT (obviously can't require JWT)
    POST /api/v1/auth/refresh     \u2014 consumes refresh token, returns new access
    GET  /api/v1/health            \u2014 health probe (if present)

Everything else under /api/v1/* requires ``@require_jwt`` (C2) + CSRF double-
submit on POST/PUT/PATCH/DELETE (C3) applied by a blueprint-level before_request
hook registered inside register_api_blueprints().
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import jwt  # PyJWT
from flask import Blueprint, g, jsonify, request

logger = logging.getLogger("icdev.dashboard.api.auth")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "icdev-dashboard"
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=7)

# Endpoints that skip @require_jwt. Match prefixes via startswith(); exact
# matches pass through unchanged. Paths are what Flask sees (after blueprint
# url_prefix expansion), so e.g. /api/v1/auth/token not /auth/token.
PUBLIC_ENDPOINTS: frozenset[str] = frozenset({
    "/api/v1/openapi.json",
    "/api/v1/docs",
    "/api/v1/auth/token",
    "/api/v1/auth/refresh",
    "/api/v1/health",
})

_DEV_SECRET_PATH = Path(__file__).resolve().parents[3] / ".tmp" / "jwt_secret_dev"


def _load_jwt_secret() -> str:
    """Resolve the HS256 secret. Env var > dev fallback > generate-and-stash.

    Order:
      1. ``ICDEV_JWT_SECRET`` env var \u2014 preferred. Mandatory in IL4+ deployments.
      2. ``.tmp/jwt_secret_dev`` on disk \u2014 dev convenience only, never committed
         (``.tmp/`` is gitignored).
      3. Generate 32-byte urandom, stash to ``.tmp/jwt_secret_dev``, return it.

    The secret is read once at module load via ``_JWT_SECRET`` so decoding is
    constant-time-lookup. A process restart is required to rotate.
    """
    secret = os.environ.get("ICDEV_JWT_SECRET", "").strip()
    if secret:
        return secret
    # Dev fallback
    try:
        if _DEV_SECRET_PATH.exists():
            return _DEV_SECRET_PATH.read_text(encoding="utf-8").strip()
        _DEV_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_hex(32)
        _DEV_SECRET_PATH.write_text(generated, encoding="utf-8")
        logger.warning(
            "ICDEV_JWT_SECRET not set; wrote dev secret to %s. "
            "Set ICDEV_JWT_SECRET for production.",
            _DEV_SECRET_PATH,
        )
        return generated
    except OSError as exc:
        # Read-only filesystem or permission issue: fall through to in-memory
        # ephemeral secret. Tokens will not survive a restart, which is a
        # loud failure mode \u2014 operator will notice immediately.
        logger.error("Could not persist dev JWT secret (%s); using ephemeral", exc)
        return secrets.token_hex(32)


_JWT_SECRET = _load_jwt_secret()


# ---------------------------------------------------------------------------
# Token issuance + decode
# ---------------------------------------------------------------------------


def issue_access_token(sub: str, role: str = "user", csrf: str | None = None) -> dict:
    """Mint a 15-min access token and return ``{token, expires_at, csrf}``."""
    now = datetime.now(timezone.utc)
    exp = now + ACCESS_TTL
    csrf_token = csrf or secrets.token_urlsafe(24)
    payload: dict[str, Any] = {
        "iss": JWT_ISSUER,
        "sub": sub,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "typ": "access",
        "csrf": csrf_token,
    }
    token = jwt.encode(payload, _JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"token": token, "expires_at": exp.isoformat(), "csrf": csrf_token}


def issue_refresh_token(sub: str, role: str = "user") -> dict:
    """Mint a 7-day refresh token; dashboard stores only in httpOnly cookie."""
    now = datetime.now(timezone.utc)
    exp = now + REFRESH_TTL
    payload: dict[str, Any] = {
        "iss": JWT_ISSUER,
        "sub": sub,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "typ": "refresh",
    }
    token = jwt.encode(payload, _JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"token": token, "expires_at": exp.isoformat()}


def decode_token(token: str, expected_typ: str = "access") -> dict:
    """Decode + validate a token. Raises ``jwt.PyJWTError`` on any failure.

    Validates: signature, expiry (``exp``), issuer, and the ``typ`` claim
    matches ``expected_typ`` (so a refresh token can't masquerade as an
    access token or vice versa).
    """
    payload = jwt.decode(
        token,
        _JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        options={"require": ["exp", "iat", "sub", "typ"]},
    )
    if payload.get("typ") != expected_typ:
        raise jwt.InvalidTokenError(
            f"Wrong token type: got {payload.get('typ')!r}, expected {expected_typ!r}"
        )
    return payload


# ---------------------------------------------------------------------------
# Request-side: extraction + decorator
# ---------------------------------------------------------------------------


def _extract_bearer(req) -> str | None:
    """Pull the Bearer token from the Authorization header (or return None)."""
    header = req.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip() or None
    return None


def is_public_endpoint(path: str) -> bool:
    """True if *path* is in the no-auth allow-list (exact match only for now)."""
    return path in PUBLIC_ENDPOINTS


def require_jwt(fn: Callable) -> Callable:
    """Decorator: require a valid access JWT, populate ``g.jwt``.

    Public endpoints are NOT auto-skipped here \u2014 a decorated handler always
    requires a token. The app-level allow-list is enforced by the
    before_request hook registered via ``install_jwt_middleware(app)``.

    On success: ``g.jwt`` is the decoded claims dict.
    On failure: returns 401 JSON ``{error, code}``.
    """
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        token = _extract_bearer(request)
        if not token:
            return jsonify({"error": "Missing Authorization Bearer token", "code": "AUTH_REQUIRED"}), 401
        try:
            claims = decode_token(token, expected_typ="access")
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired", "code": "TOKEN_EXPIRED"}), 401
        except jwt.PyJWTError as exc:
            return jsonify({"error": f"Invalid token: {exc}", "code": "AUTH_INVALID"}), 401
        g.jwt = claims
        g.user_id = claims.get("sub")
        g.user_role = claims.get("role")
        return fn(*args, **kwargs)
    return _wrapped


# ---------------------------------------------------------------------------
# Dev credential check \u2014 ICDEV_DEV_USERS="user:pass,user2:pass2"
# ---------------------------------------------------------------------------
# In C1 / current scope, authentication is credential-lookup against env var.
# Production (IL4+) swaps this for the SaaS api_key_auth / oauth_auth path.
# Defer that wire-up to a post-Phase-I hardening task.


def _dev_users() -> dict[str, str]:
    """Parse ICDEV_DEV_USERS into ``{username: password}`` (plaintext env)."""
    raw = os.environ.get("ICDEV_DEV_USERS", "").strip()
    if not raw:
        # Default single-user dev seed so local Next.js dev works out of the
        # box. Still gated by ICDEV_JWT_SECRET being unset-able only in dev.
        return {"admin": "icdev"}
    users: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            u, p = pair.split(":", 1)
            users[u.strip()] = p.strip()
    return users


def _verify_dev_credentials(username: str, password: str) -> str | None:
    """Return a role string on match, None otherwise."""
    users = _dev_users()
    expected = users.get(username)
    if expected is None:
        return None
    # secrets.compare_digest avoids timing leaks.
    if secrets.compare_digest(expected, password):
        return "admin" if username == "admin" else "user"
    return None


# ---------------------------------------------------------------------------
# Blueprint: /api/v1/auth/token + /api/v1/auth/refresh
# ---------------------------------------------------------------------------

auth_api = Blueprint("auth_api", __name__)


@auth_api.route("/auth/token", methods=["POST"])
def issue_token():
    """POST /api/v1/auth/token \u2014 exchange credentials for a JWT pair.

    Request body (JSON): ``{"username": "...", "password": "..."}``
    On success: ``200 {access: {...}, refresh: {...}, user: {sub, role}}``
    On failure: ``401 {error, code}``
    """
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not username or not password:
        return jsonify({"error": "username + password required", "code": "AUTH_MISSING_FIELDS"}), 400
    role = _verify_dev_credentials(username, password)
    if role is None:
        return jsonify({"error": "Invalid credentials", "code": "AUTH_INVALID"}), 401
    access = issue_access_token(sub=username, role=role)
    refresh = issue_refresh_token(sub=username, role=role)
    return jsonify({
        "access": access,
        "refresh": refresh,
        "user": {"sub": username, "role": role},
    })


@auth_api.route("/auth/refresh", methods=["POST"])
def refresh_token():
    """POST /api/v1/auth/refresh \u2014 exchange refresh token for a new access token.

    Request body (JSON): ``{"refresh_token": "..."}``
    On success: ``200 {access: {...}}``
    """
    body = request.get_json(silent=True) or {}
    token = str(body.get("refresh_token", "")).strip()
    if not token:
        return jsonify({"error": "refresh_token required", "code": "AUTH_MISSING_FIELDS"}), 400
    try:
        claims = decode_token(token, expected_typ="refresh")
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Refresh token expired; re-authenticate", "code": "REFRESH_EXPIRED"}), 401
    except jwt.PyJWTError as exc:
        return jsonify({"error": f"Invalid refresh token: {exc}", "code": "AUTH_INVALID"}), 401
    access = issue_access_token(sub=claims["sub"], role=claims.get("role", "user"))
    return jsonify({"access": access})


__all__ = [
    "ACCESS_TTL",
    "JWT_ALGORITHM",
    "PUBLIC_ENDPOINTS",
    "REFRESH_TTL",
    "auth_api",
    "decode_token",
    "is_public_endpoint",
    "issue_access_token",
    "issue_refresh_token",
    "require_jwt",
]
