#!/usr/bin/env python3
# CUI // SP-CTI
"""Property-based API contract tests — schemathesis.

Verifies that every operation documented in the ICDEV™ SaaS OpenAPI 3.0.3 spec:

  1. Returns a status code within its declared response set
     (auth middleware may short-circuit with 401/403; gateway may return 429/500)
  2. Returns Content-Type: application/json for non-empty, non-204 responses
  3. Has a response body that validates against the declared JSON schema

Implementation notes:
  - WSGI mode (case.call_wsgi): zero network overhead, sub-millisecond per call
  - max_examples=1: one Hypothesis-generated input per operation → 23 test cases
  - Suite target: <30 s on any CI runner

Run:
    pytest tests/api/test_contract.py -v --tb=short
"""

import copy
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path before any project imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# ---------------------------------------------------------------------------
# Optional dependency guard — skip whole module if schemathesis not installed
# ---------------------------------------------------------------------------
pytest.importorskip(
    "schemathesis",
    reason="schemathesis not installed — pip install 'schemathesis>=3.0'",
)

import schemathesis  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402  (schemathesis dep)
from schemathesis.checks import load_all_checks, CHECKS  # noqa: E402

from tools.saas.openapi_spec import generate_openapi_spec  # noqa: E402

# Resolve the unsupported_method check function (auth middleware intercepts
# TRACE/OPTIONS with 401 before Flask's route dispatch returns 405).
load_all_checks()
_unsupported_method_check = next(
    (c for c in CHECKS.get_all() if c.__name__ == "unsupported_method"), None
)


# ---------------------------------------------------------------------------
# Build a test-adjusted OpenAPI spec
# ---------------------------------------------------------------------------
# The published spec uses server base "/api/v1".  The Flask app exposes:
#   GET /health         → root level (no /api/v1 prefix, no auth required)
#   /api/v1/...         → REST API endpoints (auth required)
#
# We set the server base to "" (empty) so schemathesis uses paths as-is, then
# remap each spec path to its actual Flask URL.


def _build_test_spec() -> dict:
    """Return a copy of the OpenAPI spec remapped to actual Flask route paths.

    Mapping:
        /health                 → /health          (health check at app root)
        /{any other path}       → /api/v1/{path}   (REST API blueprint prefix)
    """
    spec = copy.deepcopy(generate_openapi_spec())
    # Override server base to empty so schemathesis uses paths verbatim
    spec["servers"] = [{"url": ""}]
    remapped: dict = {}
    for path, path_item in spec["paths"].items():
        if path == "/health":
            remapped[path] = path_item
        else:
            remapped[f"/api/v1{path}"] = path_item
    spec["paths"] = remapped
    return spec


_SPEC: dict = _build_test_spec()


# ---------------------------------------------------------------------------
# Pre-compute documented status codes per operation
# ---------------------------------------------------------------------------
# {(METHOD_UPPER, path_template) → frozenset[int]}
# Auth middleware may fire before any handler, so 401/403 are always included.

def _build_documented_statuses() -> dict:
    result: dict = {}
    for path, path_item in _SPEC.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            codes = frozenset(
                int(c)
                for c in operation.get("responses", {})
                if str(c).isdigit()
            )
            # Auth middleware short-circuits with 401/403 before handler runs
            result[(method.upper(), path)] = codes | {401, 403}
    return result


_DOCUMENTED: dict = _build_documented_statuses()

# Universal gateway-level codes always acceptable regardless of spec.
# 204: CORS OPTIONS preflight response from Flask-CORS.
# 405: Flask router returns Method Not Allowed for probe requests (e.g. TRACE).
# 429/500: rate-limiter / unhandled server error from gateway layer.
_GATEWAY_CODES: frozenset = frozenset({204, 405, 429, 500})


# ---------------------------------------------------------------------------
# Schemathesis schema (module-level — required by @schema.parametrize())
# ---------------------------------------------------------------------------
# schemathesis 4.x uses schemathesis.openapi.from_dict(); no base_url/validate_schema params.
# The WSGI app is passed at call time via case.call(app=...).
schema = schemathesis.openapi.from_dict(_SPEC)


# ---------------------------------------------------------------------------
# Contract test
# ---------------------------------------------------------------------------

@schema.parametrize()
@settings(max_examples=1, suppress_health_check=list(HealthCheck))
def test_api_contract(case, api_gateway_app):
    """Every API operation satisfies its OpenAPI contract.

    Three assertions per (operation, generated-input) pair:

    (a) Status code is in the declared response set for this operation
        (plus universal gateway codes 401/403/429/500).

    (b) Non-empty, non-204 responses include Content-Type: application/json.

    (c) Response body validates against the schema declared for the returned
        status code (delegated to schemathesis case.validate_response()).
    """
    # schemathesis 4.x: pass the WSGI app as a kwarg; transport is auto-selected.
    response = case.call(app=api_gateway_app)

    # (a) Status code in documented range -----------------------------------
    key = (case.method.upper(), case.path)
    allowed = _DOCUMENTED.get(key, frozenset({200, 201, 400, 401, 403, 404, 500}))
    allowed = allowed | _GATEWAY_CODES
    assert response.status_code in allowed, (
        f"{case.method.upper()} {case.path} → HTTP {response.status_code}; "
        f"documented: {sorted(allowed)}"
    )

    # (b) Content-Type: application/json for non-empty responses ------------
    # In schemathesis 4.x, response.headers is Mapping[str, list[str]].
    if response.status_code != 204 and response.content:
        ct_values = response.headers.get("Content-Type") or response.headers.get("content-type") or []
        ct = ct_values[0] if isinstance(ct_values, list) else str(ct_values)
        assert "application/json" in ct, (
            f"{case.method.upper()} {case.path} → {response.status_code}: "
            f"expected Content-Type: application/json, got '{ct}'"
        )

    # (c) Schema validation --------------------------------------------------
    # validate_response() checks the body against the declared schema for the
    # matched status code.  Raises if body does not conform.
    # Exclude unsupported_method: auth middleware fires before Flask's route
    # dispatch, so TRACE/OPTIONS return 401 instead of 405 — correct behavior.
    excluded = [_unsupported_method_check] if _unsupported_method_check else []
    case.validate_response(response, excluded_checks=excluded)
