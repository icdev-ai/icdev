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
    walk_api_v1_routes(app)       — Flask app.url_map → list of route dicts
    extract_query_params(handler) — ast-walk handler source for request.args
    infer_response_schema(value)  — infer JSON schema from a decoded payload
    generate_openapi_spec(app)    — dashboard equivalent of the SaaS function

Why re-implement the route walker? SaaS has 23 endpoints (hand-coding is
feasible and gives prose-quality descriptions). Dashboard has 57 blueprints
and 256 routes — hand-coding would rot immediately. We introspect instead,
and layer hand-written description overrides on top.

OpenAPI 3.0.3 → 3.1.0 diff (what matters for us)
================================================
- Version string: `"openapi": "3.1.0"` (vs `"3.0.3"`).
- Nullability: 3.1 drops `nullable: true`; use JSON Schema `type": ["string", "null"]`.
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
from tools.logging.icdev_logger import get_logger

import ast
import copy
import inspect
import logging
import re
import textwrap
import urllib.parse
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from flask import Flask

logger = get_logger("icdev.dashboard.api.openapi_generator")

# ---------------------------------------------------------------------------
# OpenAPI 3.1 base skeleton
# ---------------------------------------------------------------------------

OPENAPI_BASE: dict = {
    "openapi": "3.1.0",
    "info": {
        "title": "ICDEV™ Dashboard API",
        "version": "1.0.0",
        "description": (
            "CUI // SP-CTI — ICDEV™ Intelligent Certified Development dashboard "
            "REST API. 57 blueprints, all mounted under /api/v1/. Primary consumer "
            "is the Next.js frontend (Phase H); also consumed by schemathesis "
            "contract tests (Phase G1) and openapi-typescript codegen (Phase H2). "
            "Legacy /api/* alias paths are retained for one release."
        ),
        "contact": {"name": "ICDEV™ System Administrator"},
        "license": {"name": "Government Purpose Rights"},
    },
    "servers": [
        {"url": "/api/v1", "description": "REST API v1 (dashboard)"},
    ],
    "components": {
        "schemas": {},
        "securitySchemes": {
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

# Component schemas injected into every generated spec
_COMPONENT_SCHEMAS: dict[str, dict] = {
    "ErrorResponse": {
        "type": "object",
        "properties": {
            "error": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["error"],
    },
}

# Standard error responses present on every operation
_STD_ERROR_RESPONSES: dict[str, dict] = {
    "400": {"description": "Bad Request"},
    "500": {"description": "Internal Server Error"},
}

# ---------------------------------------------------------------------------
# Route introspection helpers
# ---------------------------------------------------------------------------

_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_CONVERTER_RE = re.compile(r"<(?:(?P<conv>[a-zA-Z_]+):)?(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)>")

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
        params.append({"name": name, "in": "path", "required": True, "schema": schema})
        return "{" + name + "}"

    path = _CONVERTER_RE.sub(replace, rule)
    return path, params


def _flask_path_to_openapi(rule: str) -> str:
    """Convert Flask URL rule to OpenAPI path string (no params list)."""
    return _CONVERTER_RE.sub(lambda m: "{" + m.group("name") + "}", rule)


def _fill_path_params(rule: str) -> str:
    """Replace Flask path params with sample values for HTTP probing."""
    def _filler(m: re.Match) -> str:
        name = m.group("name")
        conv = m.group("conv") or "default"
        if conv == "int" or name.endswith("_id"):
            return "1"
        return "example"
    return _CONVERTER_RE.sub(_filler, rule)


def _fill_openapi_path(openapi_path: str) -> str:
    """Fill OpenAPI {name} path params with sample values."""
    def _fill(m: re.Match) -> str:
        name = m.group(1)
        return "1" if name.endswith("_id") else "example"
    return re.sub(r"\{([^}]+)\}", _fill, openapi_path)


def _make_operation_id(method: str, openapi_path: str) -> str:
    """Generate a lowerCamelCase operationId from HTTP method + OpenAPI path."""
    parts = [seg for seg in openapi_path.split("/") if seg and not seg.startswith("{")]
    return method + "".join(p.capitalize() for p in parts)


def walk_api_v1_routes(app: "Flask") -> list[dict]:
    """Return all /api/v1/* routes as dicts with path, method, path_params, endpoint.

    Filters out HEAD, OPTIONS, and Flask's static route. Deduplicates on
    (method, path) so blueprint alias registrations don't double-emit.
    """
    seen: set[tuple[str, str]] = set()
    routes: list[dict] = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        if not rule.rule.startswith("/api/v1/"):
            continue
        openapi_path, path_params = _flask_rule_to_openapi_path(rule.rule)
        methods = rule.methods or set()
        for method in sorted(methods):
            if method in _HTTP_METHODS:
                m = method.lower()
                key = (m, openapi_path)
                if key in seen:
                    continue
                seen.add(key)
                routes.append({
                    "path": openapi_path,
                    "method": m,
                    "path_params": path_params,
                    "endpoint": rule.endpoint,
                })
    return routes


# ---------------------------------------------------------------------------
# B3 — query-param extraction via AST walk of handler source
# ---------------------------------------------------------------------------

_ARGS_TYPE_TO_SCHEMA: dict[str, dict] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
}


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Build node-id → parent mapping for a parsed AST."""
    parent_map: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node
    return parent_map


class _QueryParamVisitor(ast.NodeVisitor):
    """Collect query params from handler source with cast-wrapper and default support."""

    def __init__(self, parent_map: dict[int, ast.AST] | None = None) -> None:
        self._parents: dict[int, ast.AST] = parent_map or {}
        self.params: dict[str, dict] = {}

    def _record(self, name: str, *, required: bool, schema: dict) -> None:
        existing = self.params.get(name)
        if existing is None:
            self.params[name] = {"schema": dict(schema), "required": required}
        elif required and not existing["required"]:
            existing["required"] = True

    def _detect_cast(self, node: ast.AST) -> str | None:
        parent = self._parents.get(id(node))
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name):
            if parent.func.id in _ARGS_TYPE_TO_SCHEMA:
                return parent.func.id
        return None

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
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
                cast = self._detect_cast(node)

                if func.attr == "getlist":
                    schema: dict = {"type": "array", "items": {"type": "string"}}
                    self._record(name, required=False, schema=schema)
                    self.generic_visit(node)
                    return

                schema = {"type": "string"}
                # type= keyword overrides
                for kw in node.keywords:
                    if kw.arg == "type" and isinstance(kw.value, ast.Name):
                        schema = dict(_ARGS_TYPE_TO_SCHEMA.get(kw.value.id, schema))
                        break
                # cast wrapper overrides
                if cast:
                    schema = dict(_ARGS_TYPE_TO_SCHEMA.get(cast, schema))

                # Detect default from second positional arg
                if len(node.args) >= 2:
                    def_node = node.args[1]
                    if isinstance(def_node, ast.Constant):
                        raw = def_node.value
                        if cast == "int":
                            try:
                                schema["default"] = int(raw)
                            except (TypeError, ValueError):
                                schema["default"] = raw
                        elif cast == "float":
                            try:
                                schema["default"] = float(raw)
                            except (TypeError, ValueError):
                                schema["default"] = raw
                        else:
                            schema["default"] = raw

                self._record(name, required=False, schema=schema)

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        val = node.value
        if (
            isinstance(val, ast.Attribute)
            and val.attr == "args"
            and isinstance(val.value, ast.Name)
            and val.value.id == "request"
        ):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                self._record(sl.value, required=True, schema={"type": "string"})
        self.generic_visit(node)


def _parse_source_for_query_params(source: str) -> list[dict]:
    """Parse handler source text for request.args query parameters."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return []
    parent_map = _build_parent_map(tree)
    visitor = _QueryParamVisitor(parent_map)
    visitor.visit(tree)
    return [
        {"name": name, "in": "query", "required": info["required"], "schema": info["schema"]}
        for name, info in sorted(visitor.params.items())
    ]


def extract_query_params(handler: Any) -> list[dict]:
    """Return an OpenAPI parameters[] list for `request.args` usage in handler.

    On any failure (C-backed callable, lambda, unparseable source), returns [].
    """
    try:
        source = inspect.getsource(handler)
    except (OSError, TypeError):
        return []
    return _parse_source_for_query_params(source)


# ---------------------------------------------------------------------------
# B3 — response schema inference
# ---------------------------------------------------------------------------

_JSON_PRIMITIVE_TYPE: dict[type, str] = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    type(None): "null",
}


def infer_schema_from_json(value: Any, *, max_depth: int = 8, _depth: int = 0) -> dict:
    """Recursively infer an OpenAPI 3.1 schema from a decoded JSON value (uses first list element)."""
    if _depth >= max_depth:
        return {"type": "object"}
    t = type(value)
    if t in _JSON_PRIMITIVE_TYPE:
        return {"type": _JSON_PRIMITIVE_TYPE[t]}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        item = infer_schema_from_json(value[0], max_depth=max_depth, _depth=_depth + 1)
        return {"type": "array", "items": item}
    if isinstance(value, dict):
        props: dict[str, dict] = {}
        for k, v in value.items():
            props[str(k)] = infer_schema_from_json(v, max_depth=max_depth, _depth=_depth + 1)
        return {"type": "object", "properties": props}
    return {"type": "string"}


def infer_response_schema(value: Any, *, max_depth: int = 7, _depth: int = 0) -> dict:
    """Infer OpenAPI schema from decoded JSON. Samples first 3 list items for type consensus.

    Returns {} beyond max_depth. For lists, falls back to items:{} when first 3
    elements disagree on type.
    """
    if _depth >= max_depth:
        return {}
    t = type(value)
    if t in _JSON_PRIMITIVE_TYPE:
        return {"type": _JSON_PRIMITIVE_TYPE[t]}
    if isinstance(value, list):
        if not value:
            return {"type": "array", "items": {}}
        sample = value[:3]
        item_schemas = [infer_response_schema(e, max_depth=max_depth, _depth=_depth + 1) for e in sample]
        types = {s.get("type") for s in item_schemas}
        items = item_schemas[0] if len(types) == 1 else {}
        return {"type": "array", "items": items}
    if isinstance(value, dict):
        props: dict[str, dict] = {}
        for k, v in value.items():
            props[str(k)] = infer_response_schema(v, max_depth=max_depth, _depth=_depth + 1)
        return {"type": "object", "properties": props}
    return {"type": "string"}


_ALLOWED_SCHEMES = ("http", "https")


def _is_safe_url(url: str) -> bool:
    """Reject non-HTTP(S) schemes to prevent file:// and other unsafe accesses (B310)."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in _ALLOWED_SCHEMES
    except Exception:
        return False


def sample_response_schema(path: str, base_url: str, *, timeout: float = 2.0) -> dict | None:
    """GET `base_url + path`; infer schema from JSON payload; return schema or None."""
    if "{" in path:
        return None
    url = base_url.rstrip("/") + path
    if not _is_safe_url(url):
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code // 100 != 2:
            return None
        if "application/json" not in resp.headers.get("Content-Type", ""):
            return None
        payload = resp.json()
    except Exception as exc:
        logger.debug("sample_response_schema(%s): %s", path, exc)
        return None
    return infer_schema_from_json(payload)


def sample_get_route(url: str, *, timeout: float = 2.0) -> dict | None:
    """GET url; return decoded JSON body or None on any failure."""
    if not _is_safe_url(url):
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Spec assembly
# ---------------------------------------------------------------------------


def generate_openapi_spec(
    app: "Flask",
    *,
    sample_get: bool = False,
    sample_responses: bool = False,
    base_url: str = "http://localhost:5050",
    sample_timeout: float = 2.0,
) -> dict:
    """Build an OpenAPI 3.1 spec for the dashboard's /api/v1/* surface.

    Args:
        app: The live Flask application.
        sample_get: When True, issue a live GET for each GET route and infer
            response schema. Alias for sample_responses.
        sample_responses: Older alias for sample_get. Either flag enables sampling.
        base_url: Base URL to sample against.
        sample_timeout: Per-request timeout in seconds.

    Returns:
        A dict conforming to OpenAPI 3.1.0.
    """
    do_sample = sample_get or sample_responses
    spec = copy.deepcopy(OPENAPI_BASE)
    spec["components"]["schemas"].update(copy.deepcopy(_COMPONENT_SCHEMAS))
    paths: dict[str, dict] = {}

    for route in walk_api_v1_routes(app):
        method = route["method"]
        path = route["path"]
        path_params = route["path_params"]
        endpoint = route["endpoint"]

        handler = app.view_functions.get(endpoint)
        query_params = extract_query_params(handler) if handler else []
        parameters = list(path_params) + list(query_params)

        # Build 200 response — only GET routes get sampled; POST/etc. get no content
        response_200: dict
        if method == "get" and do_sample:
            sample_url = base_url.rstrip("/") + _fill_openapi_path(path)
            payload = sample_get_route(sample_url, timeout=sample_timeout)
            if payload is not None:
                schema = infer_response_schema(payload)
                response_200 = {"description": "OK", "content": {"application/json": {"schema": schema}}}
            else:
                response_200 = {"description": "OK"}
        else:
            response_200 = {"description": "OK"}

        operation: dict = {
            "operationId": _make_operation_id(method, path),
            "summary": f"{method.upper()} {endpoint}",
            "responses": {"200": response_200, **_STD_ERROR_RESPONSES},
        }
        if parameters:
            operation["parameters"] = parameters

        paths.setdefault(path, {})[method] = operation

    spec["paths"] = paths
    return spec


__all__ = [
    "OPENAPI_BASE",
    "_fill_path_params",
    "_flask_path_to_openapi",
    "_make_operation_id",
    "_parse_source_for_query_params",
    "extract_query_params",
    "generate_openapi_spec",
    "infer_response_schema",
    "infer_schema_from_json",
    "sample_get_route",
    "sample_response_schema",
    "walk_api_v1_routes",
]
