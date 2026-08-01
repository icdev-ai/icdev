# CUI // SP-CTI
"""Route-level hardening for ZIG external ingest — shx-auth-03.

`POST /security/api/zig/targets/<id>/ingest` must reject oversized payloads with
HTTP 413 BEFORE any parsing runs (DoS defense). These tests use the Flask test
client with ICDEV_AUTH_BYPASS so the auth guard added in shx-auth-01 is satisfied.
"""
from __future__ import annotations

import json

import pytest

from tools.security_canvas.constants import ZIG_INGEST_MAX_BYTES

_INGEST_ROUTE = "/security/api/zig/targets/test-app/ingest"


@pytest.fixture()
def app():
    from flask import Flask
    from tools.security_canvas.blueprint import create_security_blueprint
    from tools.security_canvas.db.init_db import init_db

    try:
        init_db()
    except Exception:
        pass

    import os
    os.environ.setdefault("ICDEV_SECURITY_ENABLED", "true")

    flask_app = Flask(__name__, template_folder="../tools/dashboard/templates")
    flask_app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    bp = create_security_blueprint()
    assert bp is not None, "create_security_blueprint() returned None"
    flask_app.register_blueprint(bp, url_prefix="/security")
    return flask_app


@pytest.fixture(autouse=True)
def _bypass_auth(monkeypatch):
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)


def test_oversized_string_payload_is_413(app):
    """A payload string over ZIG_INGEST_MAX_BYTES is rejected with HTTP 413."""
    big = "A" * (ZIG_INGEST_MAX_BYTES + 1)
    with app.test_client() as client:
        resp = client.post(_INGEST_ROUTE, json={"source_type": "nmap", "payload": big})
    assert resp.status_code == 413
    body = resp.get_json()
    assert body["ok"] is False
    assert body["max_bytes"] == ZIG_INGEST_MAX_BYTES
    assert "too large" in body["error"].lower()


def test_oversized_object_payload_is_413(app):
    """Oversized already-parsed dict/list payloads are also measured and rejected."""
    big_obj = {"components": [{"name": "x" * 32} for _ in range(200000)]}
    assert len(json.dumps(big_obj).encode("utf-8")) > ZIG_INGEST_MAX_BYTES
    with app.test_client() as client:
        resp = client.post(_INGEST_ROUTE, json={"source_type": "sbom", "payload": big_obj})
    assert resp.status_code == 413


def test_small_payload_not_413(app):
    """A small benign payload passes the size gate (does not return 413)."""
    small_nmap = ('<?xml version="1.0"?><nmaprun><host><ports>'
                  '<port protocol="tcp" portid="443">'
                  '<state state="open" reason="syn-ack"/></port>'
                  '</ports></host></nmaprun>')
    with app.test_client() as client:
        resp = client.post(_INGEST_ROUTE,
                           json={"source_type": "nmap", "payload": small_nmap})
    assert resp.status_code != 413


def test_billion_laughs_via_route_not_413_but_handled(app):
    """An entity-expansion XML under the size cap is accepted by the gate, then
    rejected cleanly by the adapter's XML defense (200 with an error field, no hang)."""
    laughs = ('<?xml version="1.0"?>\n<!DOCTYPE lolz [\n'
              '  <!ENTITY lol "lol">\n'
              '  <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
              ']>\n<nmaprun>&lol1;</nmaprun>')
    with app.test_client() as client:
        resp = client.post(_INGEST_ROUTE,
                           json={"source_type": "nmap", "payload": laughs})
    assert resp.status_code == 200
    body = resp.get_json()
    # Adapter returns ok=True with an embedded per-source error, findings=0.
    assert body.get("findings") == 0
    assert "error" in body
