# CUI // SP-CTI
"""tools/dashboard/api/openapi_generator — Dashboard OpenAPI 3.1 spec generator.

**Status:** B3 implemented — query-param extraction + response-schema inference.
B4 (Swagger UI route), B5 (drift gate), B6 (coherence check) follow.

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
from typing import Any

logger = logging.getLogger("icdev.dashboard.openapi")

# ---------------------------------------------------------------------------
# 1. Route walker — enumerate /api/v1/* routes from Flask url_map
# ---------------------------------------------------------------------------

# Matches Flask path variable syntax: <converter:name> or <name>
_FLASK_PARAM_RE = re.compile(r"<(?:[^:>]+:)?([^>]+)>")


def _flask_path_to_openapi(flask_path: str) -> str:
    """Convert ``/api/v1/foo/<bar_id>`` → ``/api/v1/foo/{bar_id}``."""
    return _FLASK_PARAM_RE.sub(lambda m: "{" + m.group(1) + "}", flask_path)


def _extract_path_params(flask_path: str) -> list[dict]:
    """Return OpenAPI path-parameter objects for every ``<var>`` in *flask_path*."""
    params = []
    for match in _FLASK_PARAM_RE.finditer(flask_path):
        params.append(
            {
                "name": match.group(1),
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        )
    return params


def walk_api_v1_routes(app) -> list[dict]:
    """Return route descriptors for every ``/api/v1/*`` endpoint in *app*.

    Each descriptor dict contains::

        method      — lowercase HTTP verb, e.g. "get"
        path        — OpenAPI path with ``{var}`` placeholders
        flask_path  — original Flask rule string
        endpoint    — Flask endpoint key (blueprint.function_name)
        handler     — view function, or None if lookup fails
        path_params — list of OpenAPI path-parameter objects
    """
    routes: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1/"):
            continue

        openapi_path = _flask_path_to_openapi(rule.rule)
        path_params = _extract_path_params(rule.rule)
        handler = app.view_functions.get(rule.endpoint)

        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            key = (method, openapi_path)
            if key in seen:
                continue
            seen.add(key)
            routes.append(
                {
                    "method": method.lower(),
                    "path": openapi_path,
                    "flask_path": rule.rule,
                    "endpoint": rule.endpoint,
                    "handler": handler,
                    "path_params": path_params,
                }
            )

    routes.sort(key=lambda r: (r["path"], r["method"]))
    return routes


# ---------------------------------------------------------------------------
# 2. Query-param extractor — AST-walk handler for request.args.get(…) calls
# ---------------------------------------------------------------------------

_CAST_MAP = {"int": "integer", "float": "number", "bool": "boolean"}


class _QueryParamVisitor(ast.NodeVisitor):
    """Collect ``request.args.get(name[, default][, type=T])`` call sites.

    Also detects wrapping casts: ``int(request.args.get(…))``,
    ``float(request.args.get(…))``.  Deduplicates by parameter name so
    the same name is never emitted twice even when it appears in multiple
    branches of one handler.
    """

    def __init__(self) -> None:
        self.params: list[dict] = []
        self._seen: set[str] = set()

    # ------------------------------------------------------------------
    # Node-classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_args_get(node: ast.AST) -> bool:
        """True when *node* is a ``request.args.get(…)`` call."""
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "args"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "request"
        )

    @staticmethod
    def _outer_cast(node: ast.Call) -> str | None:
        """Return JSON-Schema type name if *node* is ``int/float/bool(…)``."""
        if isinstance(node.func, ast.Name):
            return _CAST_MAP.get(node.func.id)
        return None

    # ------------------------------------------------------------------
    # Visitor entry-point
    # ------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._is_args_get(node):
            self._process(node, outer_cast=None)
        else:
            cast = self._outer_cast(node)
            if cast and node.args and self._is_args_get(node.args[0]):
                self._process(node.args[0], outer_cast=cast)
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Core extraction logic
    # ------------------------------------------------------------------

    def _process(self, node: ast.Call, *, outer_cast: str | None) -> None:
        """Build one OpenAPI query-param object from a single call node."""
        if not node.args:
            return

        # First positional arg must be a string literal (the param name).
        name_node = node.args[0]
        if not (
            isinstance(name_node, ast.Constant) and isinstance(name_node.value, str)
        ):
            return
        name: str = name_node.value
        if name in self._seen:
            return
        self._seen.add(name)

        param_type = outer_cast or "string"
        default: Any = None
        # All request.args.get() calls are inherently optional — .get() never
        # raises; the caller checks for None.  We still set required=True when
        # there is no default to signal "callers normally supply this".
        required = len(node.args) < 2

        if len(node.args) >= 2:
            dnode = node.args[1]
            if isinstance(dnode, ast.Constant):
                raw = dnode.value
                if outer_cast == "integer":
                    try:
                        default = int(raw)
                    except (ValueError, TypeError):
                        default = raw
                elif outer_cast == "number":
                    try:
                        default = float(raw)
                    except (ValueError, TypeError):
                        default = raw
                else:
                    default = raw
                    # Infer type from default value when no outer cast
                    if outer_cast is None:
                        if isinstance(raw, bool):
                            param_type = "boolean"
                        elif isinstance(raw, int):
                            param_type = "integer"
                        elif isinstance(raw, float):
                            param_type = "number"

        # Flask-style ``type=int`` keyword overrides inferred type.
        for kw in node.keywords:
            if kw.arg == "type" and isinstance(kw.value, ast.Name):
                mapped = _CAST_MAP.get(kw.value.id)
                if mapped:
                    param_type = mapped

        schema: dict = {"type": param_type}
        if default is not None:
            schema["default"] = default

        self.params.append(
            {
                "name": name,
                "in": "query",
                "required": required,
                "schema": schema,
            }
        )


def _parse_source_for_query_params(src: str) -> list[dict]:
    """Parse *src* text for ``request.args.get(…)`` calls.

    Low-level helper used by :func:`extract_query_params` and directly by
    tests that supply synthetic source strings.  Returns ``[]`` on any
    parse error.
    """
    try:
        tree = ast.parse(textwrap.dedent(src))
    except (SyntaxError, IndentationError):
        return []
    visitor = _QueryParamVisitor()
    visitor.visit(tree)
    return visitor.params


def extract_query_params(handler_fn) -> list[dict]:
    """Return OpenAPI query-parameter objects extracted from *handler_fn* source.

    Uses ``inspect.getsource`` + ``ast`` to find every
    ``request.args.get(…)`` call, including patterns like
    ``int(request.args.get("limit", "100"))`` and
    ``request.args.get("status", type=int)``.

    Returns an empty list on any parse error (missing source, syntax error,
    compiled-only bytecode).
    """
    try:
        src = inspect.getsource(handler_fn)
    except (OSError, TypeError):
        return []
    return _parse_source_for_query_params(src)


# ---------------------------------------------------------------------------
# 3. Homegrown JSON Schema inferrer (no genson dependency)
# ---------------------------------------------------------------------------

_MAX_INFER_DEPTH = 6
_ARRAY_SAMPLE_SIZE = 3


def infer_response_schema(payload: Any, *, _depth: int = 0) -> dict:
    """Infer a JSON Schema (draft-2020-12 subset) dict from *payload*.

    Covers: object, array, string, integer, number, boolean, null.
    Array items are sampled from the first ``_ARRAY_SAMPLE_SIZE`` elements.
    Recursion is capped at ``_MAX_INFER_DEPTH`` levels to avoid runaway
    depth on deeply-nested payloads.
    """
    if _depth > _MAX_INFER_DEPTH:
        return {}

    if payload is None:
        return {"type": "null"}
    if isinstance(payload, bool):
        # bool must be checked before int (bool is a subclass of int).
        return {"type": "boolean"}
    if isinstance(payload, int):
        return {"type": "integer"}
    if isinstance(payload, float):
        return {"type": "number"}
    if isinstance(payload, str):
        return {"type": "string"}
    if isinstance(payload, list):
        if not payload:
            return {"type": "array", "items": {}}
        sample = payload[:_ARRAY_SAMPLE_SIZE]
        item_schemas = [infer_response_schema(el, _depth=_depth + 1) for el in sample]
        # Use the first item's schema when all sampled items share the same type.
        types = {s.get("type") for s in item_schemas}
        items_schema = item_schemas[0] if len(types) == 1 else {}
        return {"type": "array", "items": items_schema}
    if isinstance(payload, dict):
        props = {
            k: infer_response_schema(v, _depth=_depth + 1)
            for k, v in payload.items()
        }
        return {"type": "object", "properties": props}

    return {}


# ---------------------------------------------------------------------------
# 4. Live GET sampler — hit a route on the local dev server
# ---------------------------------------------------------------------------

_INT_OR_ID_RE = re.compile(r"(_id|_num|_count|_version|^id$|^pk$)", re.IGNORECASE)


def _placeholder(var_name: str) -> str:
    """Return a plausible URL placeholder value for *var_name*."""
    if _INT_OR_ID_RE.search(var_name):
        return "1"
    return "example"


def _fill_path_params(flask_path: str) -> str:
    """Replace ``<type:var>`` / ``<var>`` with placeholder values."""
    return _FLASK_PARAM_RE.sub(lambda m: _placeholder(m.group(1)), flask_path)


def sample_get_route(url: str, *, timeout: float = 2.0) -> Any:
    """GET *url* and return parsed JSON payload, or ``None`` on any error.

    Uses only stdlib ``urllib`` — no ``requests`` dependency required.
    Timeouts and non-2xx responses are silently swallowed; callers
    check for ``None`` and omit the response schema when sampling fails.
    """
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        # nosec B310 — url is always http://localhost:<port>/api/v1/... constructed
        # from the base_url parameter (default "http://localhost:5050").  No user-
        # controlled input reaches this call; file:/ and custom schemes are never
        # passed here.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return json.loads(resp.read())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 5. Utility helpers — summaries and operationIds
# ---------------------------------------------------------------------------


def _make_summary(handler) -> str:
    """Return a one-line summary from the handler docstring or its name."""
    if handler is None:
        return ""
    doc = inspect.getdoc(handler) or ""
    first_line = doc.split("\n")[0].strip()
    return first_line or handler.__name__.replace("_", " ").title()


def _make_operation_id(method: str, openapi_path: str) -> str:
    """Build a camelCase operationId from the HTTP method + path segments.

    Path variables (``{project_id}``) are excluded; only literal segments
    contribute to the name.  Example::

        ("get", "/api/v1/projects/{project_id}/status")
        → "getApiV1ProjectsStatus"
    """
    segments = [
        s
        for s in openapi_path.split("/")
        if s and not s.startswith("{")
    ]
    parts = [method] + segments
    return parts[0] + "".join(p.replace("-", "_").capitalize() for p in parts[1:])


# ---------------------------------------------------------------------------
# 6. OpenAPI base template and common responses
# ---------------------------------------------------------------------------

_OPENAPI_BASE: dict = {
    "openapi": "3.1.0",
    "info": {
        "title": "ICDEV\u2122 Dashboard API",
        "version": "1.0.0",
        "description": (
            "ICDEV\u2122 Dashboard REST API \u2014 auto-generated from Flask route "
            "introspection (Phase B3). Covers all /api/v1/* endpoints."
        ),
        "contact": {"name": "ICDEV\u2122 Engineering"},
    },
    "servers": [
        {"url": "/api/v1", "description": "Dashboard API v1"},
    ],
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
            },
        },
        "schemas": {
            "ErrorResponse": {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
        },
    },
}

_COMMON_ERROR_RESPONSES: dict = {
    "400": {"description": "Bad Request"},
    "401": {"description": "Unauthorized"},
    "403": {"description": "Forbidden"},
    "404": {"description": "Not Found"},
    "500": {"description": "Internal Server Error"},
}


# ---------------------------------------------------------------------------
# 7. Top-level spec generator
# ---------------------------------------------------------------------------


def generate_openapi_spec(
    app,
    *,
    base_url: str = "http://localhost:5050",
    sample_timeout: float = 2.0,
    sample_get: bool = True,
) -> dict:
    """Generate an OpenAPI 3.1.0 spec for all ``/api/v1/*`` routes in *app*.

    For each route the generator:

    1. Collects path parameters from the URL rule.
    2. AST-walks the handler source for ``request.args.get(…)`` calls and
       appends them as query parameters.
    3. For GET routes (only), GETs the corresponding URL on the live dev
       server at *base_url* with *sample_timeout* seconds and infers the
       response JSON Schema from the returned payload.  POST / DELETE / PATCH
       routes are **not** sampled.

    Parameters
    ----------
    app:
        The Flask application instance.
    base_url:
        Root URL of the running dev server used for GET sampling.
        Default: ``http://localhost:5050``.
    sample_timeout:
        Per-request timeout in seconds.  Default: 2.0.
    sample_get:
        When *False*, skip live GET sampling entirely (CI / offline mode).

    Returns
    -------
    dict
        Full OpenAPI 3.1.0 document as a Python dict (JSON-serialisable).
    """
    spec = copy.deepcopy(_OPENAPI_BASE)
    paths: dict = {}

    routes = walk_api_v1_routes(app)
    logger.info(
        "openapi_generator: discovered %d /api/v1/* route-method pairs", len(routes)
    )

    for route in routes:
        path = route["path"]
        method = route["method"]
        handler = route["handler"]

        if path not in paths:
            paths[path] = {}

        operation: dict = {
            "summary": _make_summary(handler),
            "operationId": _make_operation_id(method, path),
            "responses": {
                "200": {"description": "OK"},
                **_COMMON_ERROR_RESPONSES,
            },
        }

        # ---- parameters: path vars + query args ----
        parameters: list[dict] = list(route["path_params"])
        if handler is not None:
            parameters.extend(extract_query_params(handler))
        if parameters:
            operation["parameters"] = parameters

        # ---- response schema via live GET sampling ----
        if method == "get" and sample_get and handler is not None:
            sample_url = base_url + _fill_path_params(route["flask_path"])
            payload = sample_get_route(sample_url, timeout=sample_timeout)
            if payload is not None:
                schema = infer_response_schema(payload)
                operation["responses"]["200"] = {
                    "description": "OK",
                    "content": {"application/json": {"schema": schema}},
                }
                logger.debug(
                    "openapi_generator: sampled schema for GET %s", path
                )
            else:
                logger.debug(
                    "openapi_generator: sampling failed/skipped for GET %s", path
                )

        paths[path][method] = operation

    spec["paths"] = paths
    return spec
