# CUI // SP-CTI
"""Central auth hook — icdev_ctx_ service-key branch (ctx-expose-02).

Verifies tools/dashboard/auth.py honors Cortex service keys ONLY on the two
service prefixes, sets the g seams rest_v1 reads, and rejects invalid keys.
"""
from __future__ import annotations

import importlib

import pytest
from flask import Flask, g, jsonify

from tools.cortex.schemas import CortexContext

auth = importlib.import_module("tools.dashboard.auth")


def _binding():
    return {
        "ctx": CortexContext(tenant_id="compass", user_id="", classification="CUI"),
        "scopes": ["cortex:search"],
        "key_id": "k1",
        "label": "compass",
        "tenant_id": "compass",
    }


@pytest.fixture
def app(monkeypatch):
    # Isolate from the real DB: stub key resolution + auth-event logging.
    service_keys = importlib.import_module("tools.cortex.service_keys")
    monkeypatch.setattr(
        service_keys, "resolve_context",
        lambda raw, requested=None: _binding() if raw == "icdev_ctx_good" else None,
    )
    monkeypatch.setattr(auth, "log_auth_event", lambda *a, **k: None)

    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    flask_app.before_request(auth._auth_before_request)

    @flask_app.route("/cortex/api/v1/search", methods=["POST"])
    def cortex_route():
        binding = getattr(g, "cortex_binding", None)
        return jsonify({
            "tenant": g.current_user["tenant_id"],
            "role": g.current_user["role"],
            "scopes": binding["scopes"] if binding else [],
            "sec_tenant": g.security_context["tenant_id"],
        })

    @flask_app.route("/api/other", methods=["POST"])
    def other_route():
        return jsonify({"reached": True})

    return flask_app


def _post(app, path, key):
    return app.test_client().post(
        path, json={}, headers={"Authorization": f"Bearer {key}"}
    )


def test_valid_key_on_service_path(app):
    resp = _post(app, "/cortex/api/v1/search", "icdev_ctx_good")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tenant"] == "compass"
    assert body["role"] == "service"
    assert body["scopes"] == ["cortex:search"]
    assert body["sec_tenant"] == "compass"


def test_invalid_key_401(app):
    resp = _post(app, "/cortex/api/v1/search", "icdev_ctx_bad")
    assert resp.status_code == 401


def test_service_key_rejected_off_service_paths(app):
    # A cortex service key is not a dashboard credential.
    resp = _post(app, "/api/other", "icdev_ctx_good")
    assert resp.status_code == 401
