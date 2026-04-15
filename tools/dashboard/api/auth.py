# CUI // SP-CTI
"""tools/dashboard/api/auth \u2014 JWT issuance + validation for /api/v1/*.

**Status:** C1 (reference + design) only. C2 adds ``@require_jwt``, C3 adds
double-submit CSRF, C4 sweeps decorators onto every blueprint handler.

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
