# CUI // SP-CTI
"""Scope enforcement + health probe on the Cortex REST v1 surface (ctx-expose-02).

Service-key callers (g.cortex_binding set by the central auth hook) must hold
``cortex:<operation>`` per endpoint; session users (no binding) are unaffected.
"""
from __future__ import annotations

import pytest
from flask import Flask, g

import tools.cortex.rest_v1 as rest_v1
from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext, CortexSearchResult


def make_client(*, binding=None, authed: bool = True):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        if authed:
            g.current_user = {"id": "u1", "role": "service", "tenant_id": "compass"}
            g.security_context = {
                "tenant_id": "compass", "user_id": "u1", "classification": "CUI",
            }
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


@pytest.fixture
def fake_search(monkeypatch):
    def _fake(query, top_k=5, ctx=None, strategy="auto", config=None):
        return [CortexSearchResult(content="hit", score=0.9, backend="rag")]

    monkeypatch.setattr(rest_v1, "search", _fake)
    return _fake


def _binding(scopes):
    return {
        "ctx": CortexContext(tenant_id="compass", classification="CUI"),
        "scopes": scopes,
        "key_id": "k1",
        "label": "compass",
        "tenant_id": "compass",
    }


def test_scope_granted_passes(fake_search):
    client = make_client(binding=_binding(["cortex:search"]))
    resp = client.post("/cortex/api/v1/search", json={"query": "hello"})
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 1


def test_scope_missing_403(fake_search):
    client = make_client(binding=_binding(["cortex:ask"]))
    resp = client.post("/cortex/api/v1/search", json={"query": "hello"})
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["code"] == "forbidden"
    assert "cortex:search" in body["error"]


def test_session_user_without_binding_unaffected(fake_search):
    client = make_client(binding=None)
    resp = client.post("/cortex/api/v1/search", json={"query": "hello"})
    assert resp.status_code == 200


def test_unauthenticated_401(fake_search):
    client = make_client(binding=None, authed=False)
    resp = client.post("/cortex/api/v1/search", json={"query": "hello"})
    assert resp.status_code == 401


def test_health_requires_no_auth():
    client = make_client(binding=None, authed=False)
    resp = client.get("/cortex/api/v1/health")
    assert resp.status_code in (200, 503)
    body = resp.get_json()
    assert "status" in body
    # Status only — never data or tenant material.
    assert "results" not in body and "text" not in body
