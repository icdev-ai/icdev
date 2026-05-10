# CUI // SP-CTI
"""tools/dashboard/api/meta \u2014 OpenAPI + Swagger UI meta blueprint.

Exposes the dashboard's OpenAPI 3.1 spec and a Swagger UI browser at
/api/v1/openapi.json and /api/v1/docs.

Both routes are read-only and auth-free: they describe the API surface,
not tenant data. Phase C's @require_jwt decorator skips these two
endpoints via an explicit allow-list (see C2 task).

Caching:
    generate_openapi_spec(app) walks app.url_map (431 paths in current
    build) and ast-parses ~250+ handler sources. That runs in ~200-400ms
    cold and produces a ~0.5 MB dict. Cache it for 5 minutes so repeated
    fetches from Swagger UI (which retries on user interaction) don't
    re-introspect the whole app.

Swagger UI reuse:
    render_swagger_ui_html() is imported from tools.saas.swagger_ui \u2014
    the same template the SaaS surface uses, parameterized by
    (openapi_url, title) so both surfaces share one source of truth.
"""
from __future__ import annotations

import time

from flask import Blueprint, current_app, jsonify

from tools.dashboard.api.openapi_generator import generate_openapi_spec
from tools.saas.swagger_ui import render_swagger_ui_html

meta_api = Blueprint("meta_api", __name__)

_CACHE_TTL_SECONDS = 300  # 5 min
_spec_cache: dict = {"spec": None, "ts": 0.0}


def _get_cached_spec() -> dict:
    now = time.time()
    if _spec_cache["spec"] is None or (now - _spec_cache["ts"]) > _CACHE_TTL_SECONDS:
        _spec_cache["spec"] = generate_openapi_spec(current_app)
        _spec_cache["ts"] = now
    return _spec_cache["spec"]


@meta_api.route("/openapi.json", methods=["GET"])
def openapi_json():
    """Return the dashboard OpenAPI 3.1 spec (5-min cached)."""
    return jsonify(_get_cached_spec())


@meta_api.route("/docs", methods=["GET"])
def openapi_docs():
    """Swagger UI page pointing at /api/v1/openapi.json."""
    html = render_swagger_ui_html(
        openapi_url="/api/v1/openapi.json",
        title="ICDEV\u2122 Dashboard API \u2014 Documentation",
    )
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


def invalidate_spec_cache() -> None:
    """Clear the cached spec. Intended for tests and dev-reload hooks."""
    _spec_cache["spec"] = None
    _spec_cache["ts"] = 0.0


__all__ = ["meta_api", "invalidate_spec_cache"]
