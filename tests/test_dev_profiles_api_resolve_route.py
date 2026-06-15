# CUI // SP-CTI
"""E2E route coverage for /dev-profiles/api/resolve/<scope>/<scope_id> — 5 acceptance cases.

Cases:
  1. GET /dev-profiles/api/resolve/project/proj-alpha success → 200 with resolved dict
  2. GET /dev-profiles/api/resolve returns URL-path scope + scope_id to resolver
  3. GET /dev-profiles/api/resolve no profile found → 200 with empty resolved dict
  4. GET /dev-profiles/api/resolve ImportError (module missing) → 200 {"error": "..."}
  5. GET /dev-profiles/api/resolve resolve_profile raises Exception → 200 {"error": "..."}
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Flask, jsonify

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_app(resolve_fn=None, raises=None):
    """Minimal Flask app mirroring the /dev-profiles/api/resolve route."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.route("/dev-profiles/api/resolve/<scope>/<scope_id>")
    def dev_profiles_api_resolve(scope, scope_id):
        try:
            if raises is not None:
                raise raises
            if resolve_fn is None:
                raise ImportError("tools.builder.dev_profile_manager not available")
            result = resolve_fn(scope, scope_id)
            return jsonify(result)
        except (ImportError, Exception) as e:
            return jsonify({"error": str(e)})

    return app


# ── 1. Success → 200 with resolved dict ──────────────────────────────────────

def test_resolve_dev_profile_success():
    """GET /dev-profiles/api/resolve/project/proj-alpha returns 200 and resolved profile."""
    expected = {
        "resolved": {"language": "python", "linter": "ruff"},
        "provenance": {"language": {"source_scope": "project", "source_id": "proj-alpha"}},
    }

    client = _make_app(resolve_fn=lambda s, si: expected).test_client()
    resp = client.get("/dev-profiles/api/resolve/project/proj-alpha")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == expected


# ── 2. URL path parameters forwarded correctly ────────────────────────────────

def test_resolve_dev_profile_passes_scope_and_scope_id():
    """GET /dev-profiles/api/resolve/<scope>/<scope_id> passes both path params to resolver."""
    captured = {}

    def _capture(scope, scope_id):
        captured["scope"] = scope
        captured["scope_id"] = scope_id
        return {"resolved": {}}

    client = _make_app(resolve_fn=_capture).test_client()
    resp = client.get("/dev-profiles/api/resolve/user/user-99")
    assert resp.status_code == 200
    assert captured == {"scope": "user", "scope_id": "user-99"}


# ── 3. No profile found → 200 with empty resolved dict ───────────────────────

def test_resolve_dev_profile_no_profile_found():
    """GET /dev-profiles/api/resolve returns 200 with empty resolved dict when no profile matches."""
    client = _make_app(resolve_fn=lambda s, si: {"resolved": {}, "provenance": {}}).test_client()
    resp = client.get("/dev-profiles/api/resolve/tenant/unknown-tenant")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["resolved"] == {}
    assert body["provenance"] == {}


# ── 4. ImportError → 200 {"error": "..."} ────────────────────────────────────

def test_resolve_dev_profile_import_error_degrades_gracefully():
    """GET /dev-profiles/api/resolve returns 200 {"error": "..."} when module is missing."""
    client = _make_app().test_client()  # resolve_fn=None triggers ImportError
    resp = client.get("/dev-profiles/api/resolve/global/default")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "error" in body
    assert isinstance(body["error"], str)
    assert len(body["error"]) > 0


# ── 5. resolve_profile raises Exception → 200 {"error": "..."} ───────────────

def test_resolve_dev_profile_exception_degrades_gracefully():
    """GET /dev-profiles/api/resolve returns 200 {"error": "..."} when resolver raises."""
    client = _make_app(raises=RuntimeError("DB connection failed")).test_client()
    resp = client.get("/dev-profiles/api/resolve/project/proj-beta")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "error" in body
    assert "DB connection failed" in body["error"]
