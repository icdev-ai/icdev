# CUI // SP-CTI
"""tools/dashboard/api/openapi_generator — Dashboard OpenAPI 3.1 spec generator.

**Status:** skeleton only. B1 (pattern reference) — see docstring below.
B2 (walk app.url_map), B3 (query-param + response schema), B4 (routes),
B5 (drift gate), B6 (coherence check) will follow in the Phase B chain.

Pattern reference — `tools/saas/openapi_spec.py` (1,396 lines, OpenAPI 3.0.3)
============================================================================
The SaaS generator is the established ICDEV pattern for publishing an OpenAPI
contract. Key mechanics we inherit from it:

1. Assembly style — *static Python dicts merged at runtime* (ADR D153).
   - `OPENAPI_BASE`: skeleton with info, servers, security, tags, and
     `components.securitySchemes` (ApiKeyAuth, OAuthBearer, CACAuth).
   - `SCHEMAS` dict: reusable component schemas (ErrorResponse, TenantResponse,
     UserResponse, ProjectResponse, …) referenced via `$ref`.
   - `ENDPOINT_DOCS`: hand-authored dict keyed by `(method, path)`.
   - `generate_openapi_spec()` (line 1329) `copy.deepcopy`s the base, injects
     `components.schemas = SCHEMAS`, and builds `paths` from `ENDPOINT_DOCS`.

2. Delivery — `tools/saas/swagger_ui.py` (93 lines):
   - Blueprint at `/api/v1` serving `/openapi.json` (jsonify) + `/docs`
     (Swagger UI 5 via unpkg CDN).
   - **Air-gap note** (SaaS file comment): for air-gap, vendor
     `swagger-ui-dist@5` locally instead of CDN. We will follow suit.

Helpers to REUSE verbatim (lines 623–688; pure combinators, version-agnostic):
    _ref(name)                    → {"$ref": "#/components/schemas/..."}
    _json_response(ref, desc, st) → response-object dict
    _error_responses(*codes)      → common error response set
    _project_id_param() / _user_id_param() / _key_id_param() — path-param specs

Helpers to RE-IMPLEMENT here (dashboard-specific):
    walk_api_v1_routes(app)       — Flask app.url_map → (method, path) tuples
    extract_query_params(handler) — ast-walk handler source for request.args
    infer_response_schema(route)  — sample GET against live dev server (B3)
    generate_openapi_spec(app)    — dashboard equivalent of the SaaS function

Why re-implement the route walker? SaaS has 23 endpoints (hand-coding is
feasible and gives prose-quality descriptions). Dashboard has 57 blueprints
and 256 routes — hand-coding would rot immediately. We introspect instead,
and layer hand-written description overrides on top.

OpenAPI 3.0.3 → 3.1.0 diff (what matters for us)
================================================
- Version string: `"openapi": "3.1.0"` (vs `"3.0.3"`).
- Nullability: 3.1 drops `nullable: true`; use JSON Schema `type: ["string", "null"]`.
  Swagger UI 5 accepts both; openapi-typescript (Phase H2) prefers 3.1.
- JSON Schema: 3.1 aligns with JSON Schema 2020-12 — better interop with
  zod/openapi-typescript/schemathesis on the frontend + contract-test side.
- Examples: 3.1 prefers `examples: {key: {value: ...}}` map over 3.0's single
  `example:`. Both still valid; we keep `example:` for readability.
- Top-level `webhooks`: new in 3.1, not used by ICDEV dashboard.

Decision: emit `"openapi": "3.1.0"`, keep 3.0-compatible `example:` fields,
drop `nullable: true` in favor of union-null types. This gives maximum
downstream tool compatibility without awkward mixed-version shapes.

Air-gap considerations (will land in B4 / Phase H)
==================================================
- Swagger UI assets must be vendored at `vendor/swagger-ui/` (parallel to
  `vendor/drivers/` for msedgedriver). The dashboard `/api/v1/docs` route
  will point at `/static/vendor/swagger-ui/...` when `is_airgap()` is true,
  otherwise the unpkg CDN.
- `/api/v1/openapi.json` itself has zero external dependencies and works in
  any environment.
"""
from __future__ import annotations
