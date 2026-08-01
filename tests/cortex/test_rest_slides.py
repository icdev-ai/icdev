# CUI // SP-CTI
"""Cortex /slides surface (prem-msr-07) — themed deck assembly for delivery tools.

Deterministic: the caller sends finished content, ICDEV renders it in a theme.
The security-critical property is that a caller-supplied ``image_path`` NEVER
reaches the builder — on a remote surface that would be arbitrary file read.
"""
from __future__ import annotations

import base64
import importlib

import pytest
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext


def make_client(*, binding=None):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        g.current_user = {"id": "u1", "role": "service", "tenant_id": "compass"}
        g.security_context = {
            "tenant_id": "compass", "user_id": "u1", "classification": "CUI",
        }
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


def _binding(scopes):
    return {
        "ctx": CortexContext(tenant_id="compass", classification="CUI"),
        "scopes": list(scopes),
        "label": "compass",
    }


DECK = [
    {"slide_type": "title", "title": "MSR — August 2026"},
    {"slide_type": "content", "title": "Accomplishments",
     "bullets": ["Closed 12 tasks", "CPI 0.98"]},
]


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """Stand in for pptx_builder.build; record what the endpoint passed it."""
    calls = {}

    def _fake_build(slides, theme="midnight_executive", title="ICDEV™ Presentation"):
        calls["slides"] = slides
        calls["theme"] = theme
        calls["title"] = title
        out = tmp_path / "deck.pptx"
        out.write_bytes(b"PK\x03\x04 fake pptx")
        return str(out)

    # String-form setattr resolves the wrong object across the tools/ shim —
    # patch the imported module object itself.
    builder = importlib.import_module("tools.slides.pptx_builder")
    monkeypatch.setattr(builder, "build", _fake_build)
    return calls


def test_builds_a_themed_deck_and_returns_base64(captured):
    client = make_client(binding=_binding(["cortex:slides"]))
    response = client.post("/cortex/api/v1/slides", json={
        "slides": DECK, "theme": "govcon_proposal", "title": "MSR"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["theme"] == "govcon_proposal"
    assert body["slide_count"] == 2
    assert base64.b64decode(body["pptx_base64"]).startswith(b"PK")
    assert captured["theme"] == "govcon_proposal"
    assert captured["title"] == "MSR"


def test_image_path_never_reaches_the_builder(captured):
    """A remote caller must not be able to embed a host file into the deck."""
    client = make_client(binding=_binding(["cortex:slides"]))
    response = client.post("/cortex/api/v1/slides", json={
        "slides": [
            {"slide_type": "title", "title": "Exfil",
             "image_path": "/etc/passwd"},
            {"slide_type": "content", "title": "Also exfil",
             "image_path": "C:/Windows/win.ini", "bullets": ["x"]},
        ],
    })

    assert response.status_code == 200
    for slide in captured["slides"]:
        assert "image_path" not in slide
    # The content the caller DID send still survives the filter.
    assert captured["slides"][0]["title"] == "Exfil"
    assert captured["slides"][1]["bullets"] == ["x"]


def test_unknown_theme_is_rejected(captured):
    client = make_client(binding=_binding(["cortex:slides"]))
    response = client.post("/cortex/api/v1/slides", json={
        "slides": DECK, "theme": "../../etc"})

    assert response.status_code == 400
    assert "unknown theme" in response.get_json()["error"]


def test_empty_deck_is_rejected(captured):
    client = make_client(binding=_binding(["cortex:slides"]))
    response = client.post("/cortex/api/v1/slides", json={"slides": []})
    assert response.status_code == 400


def test_oversized_deck_is_rejected(captured):
    client = make_client(binding=_binding(["cortex:slides"]))
    response = client.post("/cortex/api/v1/slides", json={
        "slides": [{"slide_type": "content", "title": f"S{i}"} for i in range(200)]})

    assert response.status_code == 400
    assert "too many slides" in response.get_json()["error"]


def test_key_without_the_slides_scope_is_denied(captured):
    client = make_client(binding=_binding(["cortex:search"]))
    response = client.post("/cortex/api/v1/slides", json={"slides": DECK})

    assert response.status_code == 403
    assert "cortex:slides" in response.get_json()["error"]


def test_slides_is_advertised_on_the_health_probe():
    client = make_client()
    body = client.get("/cortex/api/v1/health").get_json()
    assert "slides" in body["operations"]
