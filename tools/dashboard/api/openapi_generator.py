# CUI // SP-CTI
"""tools/dashboard/api/openapi_generator — Dashboard OpenAPI 3.1 spec generator.

**Status:** B1 (pattern reference) + B2 (route walker + minimal spec) landed.
B3 (query-param + response schema), B4 (routes), B5 (drift gate), B6
(coherence check) still to come in the Phase B chain.

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

import copy
import re
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from flask import Flask

# ---------------------------------------------------------------------------
# OpenAPI 3.1 base skeleton
# ---------------------------------------------------------------------------
# Patterned after tools/saas/openapi_spec.py::OPENAPI_BASE but trimmed for
# the dashboard's scope. Hand-written prose for info/tags stays here; route
# detail is introspected from app.url_map in generate_openapi_spec(app).

OPENAPI_BASE: dict = {
    "openapi": "3.1.0",
    "info": {
        "title": "ICDEV\u2122 Dashboard API",
        "version": "1.0.0",
        "description": (
            "CUI // SP-CTI \u2014 ICDEV\u2122 Intelligent Certified Development dashboard "
            "REST API. 57 blueprints, all mounted under /api/v1/. Primary consumer "
            "is the Next.js frontend (Phase H); also consumed by schemathesis "
            "contract tests (Phase G1) and openapi-typescript codegen (Phase H2). "
            "Legacy /api/* alias paths are retained for one release."
        ),
        "contact": {"name": "ICDEV\u2122 System Administrator"},
        "license": {"name": "Government Purpose Rights"},
    },
    "servers": [
        {"url": "/api/v1", "description": "REST API v1 (dashboard)"},
    ],
    "components": {
        "schemas": {},
        "securitySchemes": {
            # Placeholder \u2014 JWT auth wires in during Phase C (C2/C3).
            # Bearer-token stub so tooling doesn't reject the spec as auth-less.
            "BearerJWT": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Phase C will populate issuance + CSRF details.",
            },
        },
    },
    "tags": [],
}


# ---------------------------------------------------------------------------
# Route introspection
# ---------------------------------------------------------------------------
# Flask stores converters in URL rules as <converter:name>. OpenAPI wants
# {name} path params with a separate parameters[] describing type. We do the
# minimum conversion here; full schema refinement lands in B3.

_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_CONVERTER_RE = re.compile(r"<(?:(?P<conv>[a-zA-Z_]+):)?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)>")

# Flask converter \u2192 OpenAPI schema mapping. `default` is Flask's implicit
# string converter when no prefix given.
_CONVERTER_SCHEMA: dict[str, dict] = {
    "string": {"type": "string"},
    "default": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "path": {"type": "string"},
    "uuid": {"type": "string", "format": "uuid"},
    "any": {"type": "string"},
}


def _flask_rule_to_openapi_path(rule: str) -> tuple[str, list[dict]]:
    """Convert a Flask URL rule into an OpenAPI path + parameters list.

    >>> _flask_rule_to_openapi_path('/api/v1/projects/<int:project_id>')
    ('/api/v1/projects/{project_id}', [{'name': 'project_id', 'in': 'path', 'required': True, 'schema': {'type': 'integer'}}])
    """
    params: list[dict] = []

    def replace(match: re.Match) -> str:
        conv = match.group("conv") or "default"
        name = match.group("name")
        schema = dict(_CONVERTER_SCHEMA.get(conv, _CONVERTER_SCHEMA["default"]))
        params.append({
            "name": name,
            "in": "path",
            "required": True,
            "schema": schema,
        })
        return "{" + name + "}"

    path = _CONVERTER_RE.sub(replace, rule)
    return path, params


def walk_api_v1_routes(app: "Flask") -> Iterator[tuple[str, str, list[dict], str]]:
    """Yield (method, openapi_path, path_params, endpoint) for every /api/v1/* route.

    Filters out HEAD and OPTIONS (auto-generated by Flask) and Flask's
    built-in static route. Deduplication is the caller's responsibility \u2014
    Phase A's blueprint aliasing means each blueprint is registered once at
    /api/v1/ and once at /api/ for legacy compat; only the /api/v1/ side
    appears here.
    """
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        if not rule.rule.startswith("/api/v1/"):
            continue
        openapi_path, path_params = _flask_rule_to_openapi_path(rule.rule)
        methods = rule.methods or set()
        for method in sorted(methods):
            if method in _HTTP_METHODS:
                yield method.lower(), openapi_path, path_params, rule.endpoint


# ---------------------------------------------------------------------------
# Minimal operation + spec assembly (B2 scope; B3 will enrich responses)
# ---------------------------------------------------------------------------


def _minimal_operation(method: str, endpoint: str, path_params: list[dict]) -> dict:
    """Build a stub OpenAPI operation object.

    Fields emitted here are the minimum a schemathesis run (Phase G1) needs
    to enumerate the route: operationId, parameters, and a default response.
    B3 replaces the default response with an inferred schema per route.
    """
    operation: dict = {
        "operationId": f"{method}_{endpoint.replace('.', '_')}",
        "summary": f"{method.upper()} {endpoint}",
        "responses": {
            "default": {
                "description": "Response schema pending (B3 inference).",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
        },
    }
    if path_params:
        operation["parameters"] = path_params
    return operation


def generate_openapi_spec(app: "Flask") -> dict:
    """Build a minimal OpenAPI 3.1 spec for the dashboard's /api/v1/* surface.

    B2 scope: emit version, info, paths with operationId + path params,
    empty components.schemas. B3 populates query params + response schemas
    by introspecting handlers and (optionally) sampling against a live dev
    server. B5/B6 enforce route-vs-spec parity via coherence_checker.
    """
    spec = copy.deepcopy(OPENAPI_BASE)
    paths: dict[str, dict] = {}
    for method, path, params, endpoint in walk_api_v1_routes(app):
        op = _minimal_operation(method, endpoint, params)
        paths.setdefault(path, {})[method] = op
    spec["paths"] = paths
    return spec


__all__ = [
    "OPENAPI_BASE",
    "generate_openapi_spec",
    "walk_api_v1_routes",
]
