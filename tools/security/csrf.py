# CUI // SP-CTI
"""Shared CSRF protection for cookie-authenticated mutating JSON APIs (cnr-plat-01).

Before this module, no CSRF token mechanism existed on any session-cookie
authenticated JSON POST/PUT/PATCH/DELETE API across the dashboard, so a
logged-in user could be tricked into performing state-changing requests by a
malicious cross-site page (classic CSRF).

Design — layered, least-invasive
--------------------------------
1. **Double-submit token (explicit mechanism).** A per-session token is issued
   at login (``issue_csrf_token``) and lazily for any authenticated session.
   It is exposed to the page (meta tag / ``icdev_csrf`` cookie) and the shared
   ``fetch``/``XMLHttpRequest`` wrapper in ``base.html`` attaches it as the
   ``X-CSRF-Token`` header on every *same-origin* mutating request. So existing
   ``fetch()`` calls in templates are covered *without editing each one* — the
   wrapper is a single centralized shim.

2. **Fetch-metadata (compatibility + defense-in-depth).** Modern browsers stamp
   ``Sec-Fetch-Site`` on every request; page JavaScript cannot forge it. A
   ``same-origin`` / ``same-site`` / ``none`` value proves the request did not
   originate from a cross-site context, so it is accepted even without a token.
   This keeps any page that predates the wrapper working while still blocking
   the actual attack: a forged cross-site ``POST`` carries
   ``Sec-Fetch-Site: cross-site`` **and** no valid token, so it is rejected.

Exemptions (never CSRF-gated)
-----------------------------
* Non-mutating methods (GET/HEAD/OPTIONS).
* Token-authenticated / machine callers — ``Authorization: Bearer``,
  ``X-ICDEV-API-Key``, ``?api_key=``, A2A, and Cortex service keys. CSRF only
  matters for *ambient* cookie credentials; token auth is immune by definition.
* ``ICDEV_AUTH_BYPASS`` (test/CI opt-in).
* Public endpoints (login, logout, static, event ingest, health) and the
  versioned ``/api/v1/*`` JWT surface (its own middleware).
* Requests with no cookie session (``session['user_id']`` unset) — nothing to
  forge.

Configuration
-------------
* ``ICDEV_CSRF_ENFORCE`` — master kill-switch. Default **ON**. Set to
  ``0``/``false``/``no``/``off`` to disable entirely.
* ``ICDEV_CSRF_GRACE`` — grace/rollout mode. When truthy, a missing/invalid
  token is *logged* but the request is allowed. Default off.

NIST 800-53: SC-8, SC-23, SI-10.
"""
from __future__ import annotations

import hmac
import os
import secrets

from flask import g, jsonify, request, session

_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")

CSRF_SESSION_KEY = "_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_COOKIE = "icdev_csrf"
CSRF_FORM_FIELD = "csrf_token"

# Paths never subject to CSRF (public, token-auth, or machine surfaces).
_EXEMPT_PATH_PREFIXES = (
    "/static",
    "/api/v1/",           # JWT-authenticated versioned surface (own middleware)
    "/cortex/api/v1/",    # cortex service keys (server-to-server)
    "/api/databridge/v1/",
    "/api/events",        # public event ingest (own key)
    "/health",
    "/api/health",
)

_EXEMPT_ENDPOINTS = frozenset(
    {
        "login_page",
        "login",
        "logout",
        "static",
        "api_events.ingest_event",
        "api_events.healthcheck",
        "api_contact_submit",
    }
)


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def is_enforced() -> bool:
    """CSRF enforcement is ON unless explicitly disabled (fail-closed default)."""
    return os.environ.get("ICDEV_CSRF_ENFORCE", "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def is_grace() -> bool:
    return _truthy(os.environ.get("ICDEV_CSRF_GRACE"))


def get_csrf_token() -> str:
    """Return the current session CSRF token, issuing one if absent."""
    tok = session.get(CSRF_SESSION_KEY)
    if not tok:
        tok = secrets.token_hex(32)
        session[CSRF_SESSION_KEY] = tok
    return tok


def issue_csrf_token() -> str:
    """Force-issue (rotate) a fresh token. Call at successful login."""
    tok = secrets.token_hex(32)
    session[CSRF_SESSION_KEY] = tok
    return tok


def _is_token_authenticated() -> bool:
    """True when the request carries a token/machine credential (CSRF-immune)."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return True
    if request.headers.get("X-ICDEV-API-Key"):
        return True
    if request.args.get("api_key"):
        return True
    # Cortex service binding attached by the auth before_request hook.
    if getattr(g, "cortex_binding", None):
        return True
    return False


def _is_exempt() -> bool:
    ep = request.endpoint or ""
    if ep in _EXEMPT_ENDPOINTS:
        return True
    path = request.path
    return any(path == p or path.startswith(p) for p in _EXEMPT_PATH_PREFIXES)


def _reject():
    return (
        jsonify(
            {
                "error": "CSRF token missing or invalid",
                "code": "CSRF_FAILED",
                "message": "This request requires a valid CSRF token. Reload the "
                "page and retry.",
            }
        ),
        403,
    )


def csrf_protect():
    """Flask ``before_request`` hook: reject forged cross-site mutating requests.

    Returns ``None`` to let the request proceed, or a JSON 403 response tuple to
    short-circuit it.
    """
    if not is_enforced():
        return None
    if request.method not in _MUTATING_METHODS:
        return None
    if _truthy(os.environ.get("ICDEV_AUTH_BYPASS")):
        return None
    if _is_exempt():
        return None
    # Token / machine-authenticated callers use no ambient cookie credential.
    if _is_token_authenticated():
        return None
    # Only cookie/session-authenticated requests are CSRF-relevant.
    if not session.get("user_id"):
        return None

    # 1. Explicit double-submit token (header or form field).
    expected = session.get(CSRF_SESSION_KEY)
    presented = request.headers.get(CSRF_HEADER, "") or request.form.get(
        CSRF_FORM_FIELD, ""
    )
    if expected and presented and hmac.compare_digest(str(presented), str(expected)):
        return None

    # 2. Fetch-metadata: an unforgeable same-origin signal is sufficient proof
    #    the request is not a cross-site forgery.
    sec_fetch_site = request.headers.get("Sec-Fetch-Site")
    if sec_fetch_site in ("same-origin", "same-site", "none"):
        return None

    if is_grace():
        try:
            from tools.logging.icdev_logger import get_logger

            get_logger("security.csrf").warning(
                "CSRF grace: missing/invalid token method=%s path=%s endpoint=%s",
                request.method,
                request.path,
                request.endpoint,
            )
        except Exception:
            pass
        return None

    return _reject()


def register_csrf(app) -> None:
    """Install the CSRF before_request guard, cookie issuer, and template ctx."""
    app.before_request(csrf_protect)

    @app.after_request
    def _emit_csrf_cookie(response):  # noqa: ANN001
        try:
            if session.get("user_id"):
                tok = session.get(CSRF_SESSION_KEY)
                if not tok:
                    tok = secrets.token_hex(32)
                    session[CSRF_SESSION_KEY] = tok
                # Readable by page JS (double-submit) — intentionally not HttpOnly.
                response.set_cookie(
                    CSRF_COOKIE,
                    tok,
                    samesite="Strict",
                    secure=bool(getattr(request, "is_secure", False)),
                    httponly=False,
                )
        except Exception:
            pass
        return response

    @app.context_processor
    def _inject_csrf_token():
        try:
            return {"csrf_token": get_csrf_token() if session.get("user_id") else ""}
        except Exception:
            return {"csrf_token": ""}
