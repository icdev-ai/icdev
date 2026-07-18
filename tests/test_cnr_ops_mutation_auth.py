# CUI // SP-CTI
"""cnr-ops-01: fail-closed auth on mutating OHC / NOCC routes.

Verifies that state-changing endpoints return 401 to unauthenticated callers
regardless of ICDEV_ENFORCE_CANVAS_ACCESS, and that session / ICDEV_AUTH_BYPASS /
a presented ICDEV_DASHBOARD_API_KEY all satisfy the gate (while a wrong key does
not). Read (GET) routes stay ungated.
"""
from __future__ import annotations

import pytest
from flask import Flask

# All mutating routes that must fail closed when unauthenticated.
_OHC_MUTATIONS = [
    ("post", "/api/ops/models/run"),
    ("post", "/api/ops/models/register"),
    ("post", "/api/ops/models/transition"),
    ("post", "/api/ops/slos"),
    ("post", "/api/ops/incidents"),
    ("post", "/api/ops/reasoned-codegen/advise"),
]
_NOC_MUTATIONS = [
    ("post", "/api/noc/alarms"),
    ("post", "/api/noc/alarms/abc/ack"),
    ("post", "/api/noc/alarms/abc/clear"),
    ("post", "/api/noc/incidents"),
    ("put", "/api/noc/incidents/abc"),
    ("post", "/api/noc/rfcs"),
    ("post", "/api/noc/mops/generate"),
    ("post", "/api/noc/maintenance"),
]


@pytest.fixture
def app(monkeypatch):
    # Clean auth baseline: no bypass, no configured key.
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
    monkeypatch.delenv("ICDEV_ENFORCE_CANVAS_ACCESS", raising=False)

    from tools.noc_canvas.blueprint import create_noc_canvas_blueprint
    from tools.ops_hub.blueprint import create_ops_hub_blueprint

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.register_blueprint(create_ops_hub_blueprint())
    flask_app.register_blueprint(create_noc_canvas_blueprint())
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _call(client, method, path, **kw):
    return getattr(client, method)(path, **kw)


@pytest.mark.parametrize("method,path", _OHC_MUTATIONS + _NOC_MUTATIONS)
def test_unauth_mutation_is_401(client, method, path):
    resp = _call(client, method, path, json={})
    assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"


def test_get_routes_not_gated(client):
    # get_ops_overview is fully _safe-wrapped, so this always renders (200), never 401.
    resp = client.get("/api/ops/overview")
    assert resp.status_code != 401


def test_bypass_env_satisfies_gate(client, monkeypatch):
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
    # Gate passes -> handler runs -> 400 for missing title (NOT 401).
    resp = client.post("/api/noc/incidents", json={})
    assert resp.status_code == 400


def test_session_satisfies_gate(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
    resp = client.post("/api/noc/alarms", json={})  # missing fields -> 400, not 401
    assert resp.status_code == 400


def test_presented_api_key_satisfies_gate(client, monkeypatch):
    monkeypatch.setenv("ICDEV_DASHBOARD_API_KEY", "secret-key-123")
    resp = client.post("/api/noc/rfcs", json={}, headers={"X-ICDEV-API-Key": "secret-key-123"})
    assert resp.status_code == 400  # gate passed; missing title


def test_wrong_api_key_is_401(client, monkeypatch):
    monkeypatch.setenv("ICDEV_DASHBOARD_API_KEY", "secret-key-123")
    resp = client.post("/api/noc/rfcs", json={}, headers={"X-ICDEV-API-Key": "wrong"})
    assert resp.status_code == 401
