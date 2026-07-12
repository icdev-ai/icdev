# CUI // SP-CTI
"""DataBridge feeds blueprint — allowlist + scope enforcement (ctx-expose-05)."""
from __future__ import annotations

from flask import Flask, g

from tools.cortex.schemas import CortexContext
from tools.dashboard.api.databridge_feeds import databridge_feeds_bp


def make_client(*, binding=None):
    app = Flask(__name__)
    app.register_blueprint(databridge_feeds_bp)

    @app.before_request
    def _simulate_central_auth():
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


def _binding(scopes):
    return {
        "ctx": CortexContext(tenant_id="compass", classification="CUI"),
        "scopes": scopes,
        "key_id": "k1",
        "label": "compass",
        "tenant_id": "compass",
    }


def test_no_binding_401():
    client = make_client(binding=None)
    resp = client.get("/api/databridge/v1/iris/staffing_alignment")
    assert resp.status_code == 401


def test_unlisted_connector_403():
    client = make_client(binding=_binding(["databridge:iris:read"]))
    resp = client.get("/api/databridge/v1/splunk/events")
    assert resp.status_code == 403
    assert "not exposed" in resp.get_json()["error"]


def test_missing_scope_403():
    client = make_client(binding=_binding(["cortex:search"]))
    resp = client.get("/api/databridge/v1/iris/staffing_alignment")
    assert resp.status_code == 403
    assert "databridge:iris:read" in resp.get_json()["error"]


def test_read_stub_rows_ok(monkeypatch):
    monkeypatch.setenv("IRIS_STUB_MODE", "true")
    client = make_client(binding=_binding(["databridge:iris:read"]))
    resp = client.get("/api/databridge/v1/iris/staffing_alignment")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["row_count"] == len(body["data"]) > 0
    assert body["metadata"]["stub"] is True


def test_write_requires_write_scope(monkeypatch):
    monkeypatch.setenv("IRIS_STUB_MODE", "true")
    client = make_client(binding=_binding(["databridge:iris:read"]))
    resp = client.post(
        "/api/databridge/v1/iris/performance_reviews", json={"subject_id": "p-1"}
    )
    assert resp.status_code == 403

    client = make_client(binding=_binding(["databridge:iris:read", "databridge:iris:write"]))
    resp = client.post(
        "/api/databridge/v1/iris/performance_reviews", json={"subject_id": "p-1"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["accepted"] is True


def test_write_non_json_400(monkeypatch):
    monkeypatch.setenv("IRIS_STUB_MODE", "true")
    client = make_client(binding=_binding(["databridge:iris:write"]))
    resp = client.post(
        "/api/databridge/v1/iris/performance_reviews",
        data="not json", content_type="text/plain",
    )
    assert resp.status_code == 400
