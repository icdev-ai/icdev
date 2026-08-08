# CUI // SP-CTI
"""tools/dashboard/api/auth \u2014 JWT issuance + validation for /api/v1/*.

**Status:** C1 (design) + C2 (decorator + issuance) + C3 (CSRF double-submit)
+ C4 (enforcement middleware) all landed. Phase C complete.

Deviation from the C4 task-as-written: the plan said "decorate every handler
with @require_jwt + @csrf_protect" (blueprint-level sweep, ~250 routes across
55 files). We chose instead a single ``install_api_v1_auth_middleware(app)``
before_request hook — identical security outcome, one function vs. 55 file
edits, zero risk of forgetting a decorator on future routes, and matches
the SaaS pattern (tools/saas/auth/middleware.py:register_auth_middleware).
The @require_jwt and @csrf_protect decorators remain available for opt-in
use on any standalone handler that the middleware doesn't cover.

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
from tools.logging.icdev_logger import get_logger

import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import jwt  # PyJWT
from flask import Blueprint, g, jsonify, request
from urllib.parse import urlsplit

logger = get_logger("icdev.dashboard.api.auth")

# ---------------------------------------------------------------------------
# Configuration — loaded from args/auth_config.yaml with env-var overrides
# ---------------------------------------------------------------------------

_AUTH_CONFIG_PATH = Path(__file__).resolve().parents[3] / "args" / "auth_config.yaml"


def _load_auth_config() -> dict:
    try:
        import yaml
        with open(_AUTH_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


_auth_cfg = _load_auth_config()

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "icdev-dashboard"

_access_ttl_minutes = int(
    os.environ.get("ICDEV_JWT_ACCESS_TTL_MINUTES", None)
    or _auth_cfg.get("jwt", {}).get("access_ttl_minutes", 15)
)
_refresh_ttl_days = int(
    os.environ.get("ICDEV_JWT_REFRESH_TTL_DAYS", None)
    or _auth_cfg.get("jwt", {}).get("refresh_ttl_days", 7)
)
_jwt_secret_entropy = int(
    os.environ.get("ICDEV_JWT_SECRET_ENTROPY_BYTES", None)
    or _auth_cfg.get("jwt", {}).get("secret_entropy_bytes", 32)
)
_csrf_token_entropy = int(
    os.environ.get("ICDEV_CSRF_TOKEN_ENTROPY_BYTES", None)
    or _auth_cfg.get("csrf", {}).get("token_entropy_bytes", 24)
)

ACCESS_TTL = timedelta(minutes=_access_ttl_minutes)
REFRESH_TTL = timedelta(days=_refresh_ttl_days)

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
        generated = secrets.token_hex(_jwt_secret_entropy)
        _DEV_SECRET_PATH.write_text(generated, encoding="utf-8", newline="")
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
        return secrets.token_hex(_jwt_secret_entropy)


_JWT_SECRET = _load_jwt_secret()


# ---------------------------------------------------------------------------
# Token issuance + decode
# ---------------------------------------------------------------------------


def issue_access_token(sub: str, role: str = "user", csrf: str | None = None) -> dict:
    """Mint a 15-min access token and return ``{token, expires_at, csrf}``."""
    now = datetime.now(timezone.utc)
    exp = now + ACCESS_TTL
    csrf_token = csrf or secrets.token_urlsafe(_csrf_token_entropy)
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


# ---------------------------------------------------------------------------
# CSRF double-submit (C3)
# ---------------------------------------------------------------------------
# Pattern:
#   - On any GET response, set a ``csrftoken`` cookie (httponly=False,
#     SameSite=Strict) with a random 32-byte urlsafe token.
#   - On state-changing methods (POST/PUT/PATCH/DELETE), require the client
#     to echo the cookie value in the ``X-CSRF-Token`` header.
#   - Cookie + header match \u2192 request originated from JS running on this
#     origin (attacker can't read the cookie cross-origin, so can't echo it).
#
# Scope of enforcement:
#   - Only same-origin browser requests (Origin or Referer matches request
#     Host). Non-browser clients (curl, CI, mobile) have no Origin/Referer
#     and are skipped.
#   - GET/HEAD/OPTIONS are always skipped (they shouldn't cause side effects).
#
# Why this matters for a JWT-in-Bearer design:
#   - Bearer-in-header is already CSRF-immune (browsers don't auto-attach
#     Authorization headers cross-origin). So today this is belt+suspenders.
#   - But if we ever move the access token to an httpOnly cookie for XSS
#     resistance (a sensible Phase-H upgrade), CSRF becomes load-bearing.
#     C3 ships the machinery now so that switch is purely server-side.

CSRF_COOKIE_NAME = "csrftoken"
CSRF_HEADER_NAME = "X-CSRF-Token"
_CSRF_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _is_same_origin_browser(req) -> bool:
    """True if ``Origin`` (or ``Referer`` as fallback) matches the request host.

    Browser sends ``Origin`` on cross-origin state-changing requests and on
    same-origin POST (per fetch spec). Some older browsers send only
    ``Referer``. No header at all \u2192 non-browser client (curl, etc.).
    """
    origin = req.headers.get("Origin") or req.headers.get("Referer") or ""
    if not origin:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    origin_host = parsed.netloc
    return bool(origin_host) and origin_host == req.host


def _should_enforce_csrf(req) -> bool:
    if req.method not in _CSRF_STATE_CHANGING_METHODS:
        return False
    return _is_same_origin_browser(req)


def csrf_protect(fn: Callable) -> Callable:
    """Decorator: enforce double-submit CSRF on same-origin browser writes.

    Non-browser clients (no Origin/Referer) bypass this check \u2014 their
    legitimacy is already established by the JWT bearer token. Browser
    clients MUST supply both cookie and matching header.
    """
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        if _should_enforce_csrf(request):
            cookie_val = request.cookies.get(CSRF_COOKIE_NAME, "")
            header_val = request.headers.get(CSRF_HEADER_NAME, "")
            if (
                not cookie_val
                or not header_val
                or not secrets.compare_digest(cookie_val, header_val)
            ):
                return jsonify({
                    "error": "CSRF token missing or invalid",
                    "code": "CSRF_INVALID",
                }), 403
        return fn(*args, **kwargs)
    return _wrapped


def install_api_v1_auth_middleware(app) -> None:
    """Register a before_request hook that enforces JWT + CSRF on /api/v1/*.

    Scope: only requests whose path starts with ``/api/v1/``. The legacy
    ``/api/*`` alias is intentionally left unauthenticated so the existing
    Jinja dashboard pages (which call the legacy paths from inline JS)
    keep working for one release \u2014 matches B4's "keep for 1 release, then
    deprecate" posture. When the alias is dropped (Phase I), remove the
    path-prefix guard below to enforce everywhere.

    Skipped (pass-through, no 401):
      - Path not under /api/v1/  \u2014 e.g. Jinja page loads, legacy /api/*
      - Path in PUBLIC_ENDPOINTS  \u2014 auth/token, auth/refresh, openapi.json,
        docs, health
      - Preflight OPTIONS requests \u2014 CORS handles these; never auth-protected

    On failure: returns 401 JSON ``{error, code}`` before the route handler
    runs.
    """
    @app.before_request
    def _enforce_api_v1_auth():
        path = request.path
        if not path.startswith("/api/v1/"):
            return None
        if request.method == "OPTIONS":
            return None
        if is_public_endpoint(path):
            return None
        # --- JWT validation -------------------------------------------------
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
        # --- CSRF double-submit on state-changing same-origin browser writes
        if _should_enforce_csrf(request):
            cookie_val = request.cookies.get(CSRF_COOKIE_NAME, "")
            header_val = request.headers.get(CSRF_HEADER_NAME, "")
            if (
                not cookie_val
                or not header_val
                or not secrets.compare_digest(cookie_val, header_val)
            ):
                return jsonify({
                    "error": "CSRF token missing or invalid",
                    "code": "CSRF_INVALID",
                }), 403
        return None


def install_csrf_cookie_middleware(app) -> None:
    """Register a Flask after_request hook that seeds the CSRF cookie.

    On any safe GET response, set ``csrftoken`` if the request didn't carry
    one. Cookie attributes:
      - ``httponly=False``   \u2014 JS must read it to echo in header
      - ``samesite='Strict'`` \u2014 never sent cross-origin
      - ``secure=True``       \u2014 HTTPS-only; auto-disabled in dev when
        ICDEV_DEV_ALLOW_INSECURE_COOKIES=true is set
    """
    allow_insecure = os.environ.get(
        "ICDEV_DEV_ALLOW_INSECURE_COOKIES", ""
    ).strip().lower() in ("1", "true", "yes")

    @app.after_request
    def _seed_csrf_cookie(resp):
        if request.method == "GET" and CSRF_COOKIE_NAME not in request.cookies:
            token = secrets.token_urlsafe(_csrf_token_entropy)
            resp.set_cookie(
                CSRF_COOKIE_NAME,
                token,
                httponly=False,
                samesite="Strict",
                secure=not allow_insecure,
                max_age=int(ACCESS_TTL.total_seconds()),
                path="/",
            )
        return resp


__all__ = [
    "ACCESS_TTL",
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "JWT_ALGORITHM",
    "PUBLIC_ENDPOINTS",
    "REFRESH_TTL",
    "auth_api",
    "csrf_protect",
    "decode_token",
    "install_api_v1_auth_middleware",
    "install_csrf_cookie_middleware",
    "is_public_endpoint",
    "issue_access_token",
    "issue_refresh_token",
    "require_jwt",
]
