# CUI // SP-CTI
"""Minimal OpenAPI 3.0.3 spec builder from Flask route docstrings.

Usage:
    from flask import current_app
    from tools.api.openapi_builder import build_openapi_spec
    spec = build_openapi_spec(current_app._get_current_object())
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


def build_openapi_spec(
    app: "Flask",
    title: str = "ICDEV External API",
    version: str = "1.0.0",
    path_prefix: str = "/v1",
) -> dict:
    """Return an OpenAPI 3.0.3 dict built from routes whose paths start with *path_prefix*.

    Each view function's first docstring line becomes the operation summary.
    All operations are marked as requiring bearerAuth.
    """
    paths: dict = {}

    for rule in app.url_map.iter_rules():
        path = rule.rule
        if not path.startswith(path_prefix):
            continue

        endpoint = rule.endpoint
        view_func = app.view_functions.get(endpoint)
        if not view_func:
            continue

        # Unwrap decorators to find the original docstring
        func = view_func
        while hasattr(func, "__wrapped__"):
            func = func.__wrapped__
        doc = (getattr(func, "__doc__", None) or "").strip()
        summary = doc.split("\n")[0] if doc else endpoint

        path_item = paths.setdefault(path, {})
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            path_item[method.lower()] = {
                "summary": summary,
                "operationId": f"{method.lower()}_{endpoint}",
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {"description": "Success"},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                    "404": {"description": "Not found"},
                    "429": {"description": "Rate limit exceeded"},
                },
            }

    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": version},
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "ICDEV API Key (ick_...)",
                }
            }
        },
        "paths": paths,
    }
