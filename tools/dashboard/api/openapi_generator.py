# CUI // SP-CTI
"""tools/dashboard/api/openapi_generator — Dashboard OpenAPI 3.1 spec generator.

**Status:** B1 (pattern reference), B2 (route walker + minimal spec), and
B3 (query-param AST extraction + live response-sample schema inference) all
landed. B4 (routes), B5 (drift gate), B6 (coherence check) still to come.

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

Helpers implemented here (dashboard-specific):
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

import ast
import copy
import inspect
import json
import logging
import re
import textwrap
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger("icdev.dashboard.api.openapi_generator")

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


# ---------------------------------------------------------------------------
# B3 \u2014 query-param extraction via AST walk of handler source
# ---------------------------------------------------------------------------
# Patterns we recognize:
#   request.args.get('name')                 \u2192 optional, string
#   request.args.get('name', 'default')      \u2192 optional with default
#   request.args.get('name', type=int)       \u2192 optional, integer
#   request.args.get('name', type=float)     \u2192 optional, number
#   request.args.get('name', type=bool)      \u2192 optional, boolean
#   request.args['name']                     \u2192 required, string
#   request.args.getlist('name')             \u2192 optional, array of strings
# We do not recognize dynamic key lookups (request.args.get(var_name)) \u2014
# those degrade to no-param, not a crash. Phase B6 coherence flags any route
# whose parameters[] looks empty but has runtime query-param usage.


_ARGS_TYPE_TO_SCHEMA: dict[str, dict] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
}


class _QueryParamVisitor(ast.NodeVisitor):
    """Collect (name, required, schema) triples from a handler's AST."""

    def __init__(self) -> None:
        self.params: dict[str, dict] = {}

    def _record(self, name: str, *, required: bool, schema: dict) -> None:
        # First sighting wins for schema, but `required` escalates: if any
        # call-site uses args[name] (required), the param is required.
        existing = self.params.get(name)
        if existing is None:
            self.params[name] = {"schema": dict(schema), "required": required}
        elif required and not existing["required"]:
            existing["required"] = True

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        # request.args.get('name', ...) / request.args.getlist('name')
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "request"
            and func.value.attr == "args"
            and func.attr in ("get", "getlist")
        ):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                name = node.args[0].value
                if func.attr == "getlist":
                    self._record(name, required=False, schema={"type": "array", "items": {"type": "string"}})
                else:
                    schema = {"type": "string"}
                    # Inspect keyword `type=` for type hinting
                    for kw in node.keywords:
                        if kw.arg == "type" and isinstance(kw.value, ast.Name):
                            schema = dict(_ARGS_TYPE_TO_SCHEMA.get(kw.value.id, schema))
                    self._record(name, required=False, schema=schema)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # request.args['name']  -> required string
        val = node.value
        if (
            isinstance(val, ast.Attribute)
            and val.attr == "args"
            and isinstance(val.value, ast.Name)
            and val.value.id == "request"
        ):
            # ast.Subscript.slice is the key in 3.9+
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                self._record(sl.value, required=True, schema={"type": "string"})
        self.generic_visit(node)


def extract_query_params(handler: Any) -> list[dict]:
    """Return an OpenAPI parameters[] list for `request.args` usage in handler.

    On any failure (C-backed callable, lambda, unparseable source), returns
    []. This is intentional: partial spec is better than a crash, and
    Phase B6 coherence flags empty-params routes whose handler source hints
    at query usage.
    """
    try:
        source = inspect.getsource(handler)
    except (OSError, TypeError):
        return []
    # textwrap.dedent (not inspect.cleandoc) \u2014 we need to preserve the
    # function body's relative indentation, only stripping the common
    # leading whitespace introduced by nesting.
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return []
    visitor = _QueryParamVisitor()
    visitor.visit(tree)
    params = []
    for name, info in sorted(visitor.params.items()):
        params.append({
            "name": name,
            "in": "query",
            "required": info["required"],
            "schema": info["schema"],
        })
    return params


# ---------------------------------------------------------------------------
# B3 \u2014 response schema inference (live sampling + homegrown inferrer)
# ---------------------------------------------------------------------------
# We avoid the `genson` dependency (extra install footprint in air-gap) and
# walk JSON values ourselves. This covers ~90% of real responses cleanly;
# operators can hand-override in B4's registration if a route needs richer
# schema (oneOf, discriminator, etc.).


_JSON_PRIMITIVE_TYPE: dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    type(None): "null",
}


def infer_schema_from_json(value: Any, *, max_depth: int = 8, _depth: int = 0) -> dict:
    """Recursively infer an OpenAPI 3.1 schema from a decoded JSON value.

    Depth-bounded so pathological payloads (deep self-referential dicts)
    don't blow the stack. Beyond max_depth we fall back to `{"type": "object"}`.
    """
    if _depth >= max_depth:
        return {"type": "object"}
    t = type(value)
    if t in _JSON_PRIMITIVE_TYPE:
        return {"type": _JSON_PRIMITIVE_TYPE[t]}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        # Use the first element as the representative item schema. Mixed-type
        # arrays degrade silently to the first shape; a future pass can
        # compute a union if it matters.
        item = infer_schema_from_json(value[0], max_depth=max_depth, _depth=_depth + 1)
        return {"type": "array", "items": item}
    if isinstance(value, dict):
        props: dict[str, dict] = {}
        for k, v in value.items():
            props[str(k)] = infer_schema_from_json(v, max_depth=max_depth, _depth=_depth + 1)
        return {"type": "object", "properties": props}
    # Fallback for exotic types (datetime, Decimal, etc. \u2014 shouldn't reach
    # here from json.loads output, but defensive).
    return {"type": "string"}


def sample_response_schema(path: str, base_url: str, *, timeout: float = 2.0) -> dict | None:
    """GET `base_url + path`; infer schema from JSON payload; return schema or None.

    Returns None on: network error, non-2xx, non-JSON, or timeout. Callers
    should treat None as "keep the default placeholder response schema".
    Path-parameter routes (with `{id}` segments) are skipped at the call
    site \u2014 there's no generic way to pick a legal id value.
    """
    if "{" in path:
        return None
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=timeout) as resp:
            if resp.status // 100 != 2:
                return None
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" not in ctype:
                return None
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        logger.debug("sample_response_schema(%s): %s", path, exc)
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.debug("sample_response_schema(%s): non-JSON body: %s", path, exc)
        return None
    return infer_schema_from_json(payload)


# ---------------------------------------------------------------------------
# Operation assembly (enriched in B3)
# ---------------------------------------------------------------------------


def _operation(
    method: str,
    endpoint: str,
    path_params: list[dict],
    query_params: list[dict],
    response_schema: dict | None,
) -> dict:
    """Build an OpenAPI operation with path + query params and (optionally) an inferred response schema."""
    parameters = list(path_params) + list(query_params)
    response_body = (
        response_schema
        if response_schema is not None
        else {"type": "object"}
    )
    response_desc = (
        "Inferred from live sample." if response_schema is not None
        else "Response schema not sampled (non-GET, path-parametric, or unreachable)."
    )
    operation: dict = {
        "operationId": f"{method}_{endpoint.replace('.', '_')}",
        "summary": f"{method.upper()} {endpoint}",
        "responses": {
            "200": {
                "description": response_desc,
                "content": {"application/json": {"schema": response_body}},
            },
        },
    }
    if parameters:
        operation["parameters"] = parameters
    return operation


def generate_openapi_spec(
    app: "Flask",
    *,
    sample_responses: bool = False,
    base_url: str = "http://localhost:5050",
    sample_timeout: float = 2.0,
) -> dict:
    """Build an OpenAPI 3.1 spec for the dashboard's /api/v1/* surface.

    Args:
        app: The live Flask application.
        sample_responses: When True, issue a live GET against `base_url + path`
            for every GET route without path parameters, and infer a JSON
            schema from the response body. Skipped for POST/PATCH/DELETE.
            Default False so the call is fast and has zero external side
            effects (e.g. during Swagger UI page rendering).
        base_url: Base URL to sample against (only used when
            `sample_responses=True`).
        sample_timeout: Per-request timeout in seconds. Default 2.0.

    Returns:
        A dict conforming to OpenAPI 3.1.0, with paths populated from
        `app.url_map` for all /api/v1/* routes.
    """
    spec = copy.deepcopy(OPENAPI_BASE)
    paths: dict[str, dict] = {}
    for method, path, path_params, endpoint in walk_api_v1_routes(app):
        handler = app.view_functions.get(endpoint)
        query_params = extract_query_params(handler) if handler else []
        response_schema: dict | None = None
        if sample_responses and method == "get":
            response_schema = sample_response_schema(path, base_url, timeout=sample_timeout)
        op = _operation(method, endpoint, path_params, query_params, response_schema)
        paths.setdefault(path, {})[method] = op
    spec["paths"] = paths
    return spec


__all__ = [
    "OPENAPI_BASE",
    "extract_query_params",
    "generate_openapi_spec",
    "infer_schema_from_json",
    "sample_response_schema",
    "walk_api_v1_routes",
]
