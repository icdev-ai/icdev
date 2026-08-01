# CUI // SP-CTI
"""Dashboard export surface (prem-rpt-02).

The /slides ``image_path`` hole is the PRECEDENT for this endpoint, and the reason it
rebuilds every spec from a content-only allowlist. On a REMOTE surface, any path-bearing
key a renderer honours is an arbitrary-file-read primitive: the caller names a file, we
render it into a document, and we hand the document back to them.

The test that matters most here is the one that tries it.
"""
from __future__ import annotations

import base64
import importlib

import pytest
from flask import Flask, g

from tools.cortex.blueprint import cortex_bp
from tools.cortex.schemas import CortexContext

SCOPE = "cortex:dashboard"

CHART = {"kind": "chart", "chart_type": "bar", "title": "Burn",
         "categories": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]}


def make_client(*, binding=None):
    app = Flask(__name__)
    app.register_blueprint(cortex_bp)

    @app.before_request
    def _simulate_auth():
        g.current_user = {"id": "u1", "role": "service", "tenant_id": "compass"}
        g.security_context = {"tenant_id": "compass", "user_id": "u1", "classification": "CUI"}
        if binding is not None:
            g.cortex_binding = binding

    return app.test_client()


def _binding(scopes, *, ceiling="CUI"):
    return {
        "ctx": CortexContext(tenant_id="compass", classification=ceiling),
        "scopes": list(scopes),
        "label": "compass",
        "tenant_id": "compass",
        "classification_ceiling": ceiling,
    }


@pytest.fixture
def exported(monkeypatch):
    """Capture the dashboard dict that actually reaches the renderer."""
    calls = []
    mod = importlib.import_module("tools.bi_dashboard.export")
    real = mod.export_dashboard

    def _spy(dashboard, fmt, **kw):
        calls.append({"dashboard": dashboard, "fmt": fmt})
        return real(dashboard, fmt, **kw)

    rest = importlib.import_module("tools.cortex.rest_v1")
    monkeypatch.setattr(mod, "export_dashboard", _spy)
    assert rest
    return calls


# ---------------------------------------------------------------------------
# THE security test
# ---------------------------------------------------------------------------


def test_a_path_bearing_key_NEVER_reaches_the_renderer(exported):
    """The /slides image_path hole, attempted here.

    A caller who can name a file that a renderer will embed has an arbitrary-file-read
    primitive: they ask for /etc/passwd, we render it into a PDF, and we hand them the
    PDF. The allowlist is what stops it — and it is an ALLOWLIST, not a blocklist,
    because a blocklist is a list of the holes you already know about.
    """
    client = make_client(binding=_binding([SCOPE]))
    hostile = {
        **CHART,
        "image_path": "/etc/passwd",
        "background_image": "C:/Windows/win.ini",
        "src": "file:///etc/shadow",
        "template_path": "../../secrets.env",
    }

    resp = client.post("/cortex/api/v1/dashboard",
                       json={"title": "T", "tiles": [{"spec": hostile}], "format": "html"})
    assert resp.status_code == 200

    spec = exported[0]["dashboard"]["tiles"][0]["spec"]
    for key in ("image_path", "background_image", "src", "template_path"):
        assert key not in spec, f"{key} reached the renderer"
    # The CONTENT survived — we stripped the paths, not the chart.
    assert spec["categories"] == ["a", "b"]
    assert spec["title"] == "Burn"

    # And nothing that looks like a file path is in the document we handed back.
    assert "/etc/passwd" not in resp.get_json()["html"]


def test_an_unknown_spec_kind_is_refused(exported):
    """A kind with no allowlist entry has no allowlist — so it cannot be filtered, so it
    does not get in."""
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/dashboard", json={
        "title": "T", "tiles": [{"spec": {"kind": "raw_html", "html": "<script>x</script>"}}],
    })
    assert resp.status_code == 400
    assert "unsupported spec kind" in resp.get_json()["error"]
    assert exported == []


def test_the_caller_cannot_mark_its_own_export_DOWN(exported):
    """classification comes from the KEY's ceiling, never the body. An export leaves the
    platform, so the marking travels with it — and a caller must not be able to talk it
    down to UNCLASSIFIED on the way out."""
    client = make_client(binding=_binding([SCOPE], ceiling="SECRET"))
    resp = client.post("/cortex/api/v1/dashboard", json={
        "title": "T", "tiles": [{"spec": CHART}],
        "classification": "UNCLASSIFIED",     # hostile
        "format": "html",
    })
    assert resp.status_code == 200
    assert exported[0]["dashboard"]["classification"] == "SECRET"
    assert "SECRET" in resp.get_json()["html"]
    assert "UNCLASSIFIED" not in resp.get_json()["html"]


# ---------------------------------------------------------------------------
# It actually works
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["html", "pptx", "pdf"])
def test_each_format_comes_back_as_a_real_document(exported, fmt):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/dashboard",
                       json={"title": "T", "tiles": [{"spec": CHART}], "format": fmt})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["tile_count"] == 1
    if fmt == "html":
        assert "<svg" in body["html"]
    else:
        raw = base64.b64decode(body[f"{fmt}_base64"])
        assert raw[:4] in (b"%PDF", b"PK\x03\x04")


def test_a_bad_format_is_rejected(exported):
    client = make_client(binding=_binding([SCOPE]))
    resp = client.post("/cortex/api/v1/dashboard",
                       json={"title": "T", "tiles": [{"spec": CHART}], "format": "docx"})
    assert resp.status_code == 400
    assert exported == []


def test_no_tiles_is_rejected(exported):
    client = make_client(binding=_binding([SCOPE]))
    assert client.post("/cortex/api/v1/dashboard",
                       json={"title": "T", "tiles": []}).status_code == 400


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_the_scope_is_NOT_in_the_default_grant():
    """An export leaves the platform by design. A key that can search must not silently
    also be able to render our data into a document and walk out with it."""
    from tools.cortex.service_keys import ALL_SCOPES, DEFAULT_SCOPES

    assert SCOPE in ALL_SCOPES
    assert SCOPE not in DEFAULT_SCOPES


def test_a_key_without_the_scope_is_denied(exported):
    client = make_client(binding=_binding(["cortex:search"]))
    resp = client.post("/cortex/api/v1/dashboard",
                       json={"title": "T", "tiles": [{"spec": CHART}]})
    assert resp.status_code == 403
    assert exported == []


def test_dashboard_is_advertised_on_the_health_probe():
    client = make_client()
    assert "dashboard" in client.get("/cortex/api/v1/health").get_json()["operations"]


def test_the_client_has_an_export_method():
    from tools.cortex.client import CortexClient

    assert hasattr(CortexClient, "export_dashboard")
