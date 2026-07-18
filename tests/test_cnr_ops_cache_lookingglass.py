# CUI // SP-CTI
"""cnr-ops-02: probe_all TTL cache + looking-glass URL validation."""
from __future__ import annotations

import importlib

import pytest
from flask import Flask


# ── probe_all TTL cache ──────────────────────────────────────────────────────

def test_probe_all_cached_reuses_within_ttl(monkeypatch):
    reg = importlib.import_module("tools.ops_hub.adapter_registry")
    reg.invalidate_probe_cache()
    calls = {"n": 0}

    def fake_probe_all(persist=True):
        calls["n"] += 1
        return [{"adapter_name": "x", "available": True}]

    monkeypatch.setattr(reg, "probe_all", fake_probe_all)

    r1 = reg.probe_all_cached(persist=True)
    r2 = reg.probe_all_cached(persist=True)
    assert r1 == r2
    assert calls["n"] == 1, "second call within TTL must hit the cache, not re-probe"

    reg.invalidate_probe_cache()
    reg.probe_all_cached(persist=True)
    assert calls["n"] == 2, "after invalidation the probe must run again"


def test_probe_all_cached_expires(monkeypatch):
    reg = importlib.import_module("tools.ops_hub.adapter_registry")
    reg.invalidate_probe_cache()
    calls = {"n": 0}
    monkeypatch.setattr(reg, "probe_all", lambda persist=True: calls.__setitem__("n", calls["n"] + 1) or [])
    reg.probe_all_cached(persist=True, ttl=0.0)  # zero TTL -> always re-probe
    reg.probe_all_cached(persist=True, ttl=0.0)
    assert calls["n"] == 2


# ── looking-glass URL validation ─────────────────────────────────────────────

@pytest.fixture
def noc_client(monkeypatch):
    mod = importlib.import_module("tools.noc_canvas.blueprint")
    captured: dict = {}

    def fake_render(template, **kw):
        captured.clear()
        captured.update(kw)
        captured["_template"] = template
        return "OK"

    monkeypatch.setattr(mod, "render_template", fake_render)
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(mod.create_noc_canvas_blueprint())
    return app.test_client(), captured


def test_looking_glass_rejects_non_http_scheme(noc_client, monkeypatch):
    client, captured = noc_client
    monkeypatch.setenv("HYPERGLASS_URL", "javascript:alert(1)")
    resp = client.get("/noc/looking-glass")
    assert resp.status_code == 200
    assert captured["hyperglass_url"] == ""
    assert captured["hyperglass_invalid"] is True


def test_looking_glass_accepts_https(noc_client, monkeypatch):
    client, captured = noc_client
    monkeypatch.setenv("HYPERGLASS_URL", "https://lg.example.mil/")
    client.get("/noc/looking-glass")
    assert captured["hyperglass_url"] == "https://lg.example.mil/"
    assert captured["hyperglass_invalid"] is False


def test_looking_glass_not_configured(noc_client, monkeypatch):
    client, captured = noc_client
    monkeypatch.delenv("HYPERGLASS_URL", raising=False)
    client.get("/noc/looking-glass")
    assert captured["hyperglass_url"] == ""
    assert captured["hyperglass_invalid"] is False
