"""nav-plat-03 — GeoSIGINT static-reference provenance labeling.

The GeoSIGINT blueprint serves a **static reference model** (hardcoded
module-level constants — WEAPON_SYSTEMS, LANDING_ZONES, THAAD_BATTERIES,
CHOKEPOINTS, RADAR_SYSTEMS, DISRUPTION_SCENARIOS, ...), not a live feed. This
suite pins the labeling that lets a viewer tell:

  1. Every layer/API response carries ``data_source`` + ``as_of``.
  2. Every GeoSIGINT page renders a persistent "Reference data (static)" badge.

Wiring live sources is explicitly OUT of scope — only honest labeling is tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from apps.geosigint.blueprint import (
    DATA_VINTAGE,
    create_geosigint_api_blueprint,
    create_geosigint_blueprint,
)

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[1]
_TPL = _REPO / "apps" / "geosigint" / "templates"

# GET API endpoints (path, list of top-level keys the payload should still carry).
_GET_ENDPOINTS = [
    "/api/geosigint/a2ad/zones",
    "/api/geosigint/amphibious/summary",
    "/api/geosigint/amphibious/zones",
    "/api/geosigint/amphibious/lift",
    "/api/geosigint/amphibious/weather",
    "/api/geosigint/amphibious/crossing",
    "/api/geosigint/amphibious/detection",
    "/api/geosigint/strait-crossing/summary",
    "/api/geosigint/strait-crossing/speed-matrix",
    "/api/geosigint/strait-crossing/intercept",
    "/api/geosigint/strait-crossing/detection",
    "/api/geosigint/strait-crossing/corridor",
    "/api/geosigint/island-chain/summary",
    "/api/geosigint/island-chain/bases",
    "/api/geosigint/island-chain/thaad",
    "/api/geosigint/island-chain/chokepoints",
    "/api/geosigint/militia/summary",
    "/api/geosigint/militia/zones",
    "/api/geosigint/semiconductor/summary",
    "/api/geosigint/semiconductor/scenarios",
    "/api/geosigint/semiconductor/exposure-map",
    "/api/geosigint/semiconductor/ree-flow",
]

# POST endpoints (path, json body) — all carry static reference data too.
_POST_ENDPOINTS = [
    ("/api/geosigint/militia/classify",
     {"vessels": [{"id": "v1", "lat": 15.0, "lon": 115.0, "sog_kts": 0.2,
                   "ais_on": False, "gear_deployed": False}]}),
    ("/api/geosigint/militia/swarms",
     {"vessels": [{"id": "v1", "lat": 15.0, "lon": 115.0, "sog_kts": 0.2,
                   "ais_on": False}]}),
    ("/api/geosigint/semiconductor/simulate",
     {"scenario_id": "taiwan_blockade"}),
]

_PAGE_ROUTES = [
    "/geosigint/",
    "/geosigint/a2ad",
    "/geosigint/amphibious",
    "/geosigint/strait-crossing",
    "/geosigint/island-chain",
    "/geosigint/militia",
    "/geosigint/semiconductor",
]

_STATIC_TEMPLATES = [
    "geosigint_index.html",
    "a2ad.html",
    "amphibious.html",
    "strait_crossing.html",
    "island_chain.html",
    "militia.html",
    "semiconductor.html",
]

_BADGE_TEXT = "Reference data (static)"


@pytest.fixture(scope="module")
def client():
    app = Flask(__name__)
    app.register_blueprint(create_geosigint_blueprint())
    app.register_blueprint(create_geosigint_api_blueprint())
    app.config["TESTING"] = True
    return app.test_client()


# ── Vintage sanity ───────────────────────────────────────────────────────────

def test_data_vintage_is_honest_month_precision():
    # Month precision, not a fabricated exact date.
    assert DATA_VINTAGE == "2026-05"
    assert len(DATA_VINTAGE) == 7 and DATA_VINTAGE[4] == "-"


# ── API provenance ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", _GET_ENDPOINTS)
def test_get_endpoint_carries_provenance(client, path):
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    body = resp.get_json()
    assert isinstance(body, dict), f"{path} did not return a JSON object"
    assert body.get("data_source") == "static_reference", f"{path} missing data_source"
    assert body.get("as_of") == DATA_VINTAGE, f"{path} missing/incorrect as_of"


@pytest.mark.parametrize("path,payload", _POST_ENDPOINTS)
def test_post_endpoint_carries_provenance(client, path, payload):
    resp = client.post(path, json=payload)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    body = resp.get_json()
    assert body.get("data_source") == "static_reference", f"{path} missing data_source"
    assert body.get("as_of") == DATA_VINTAGE, f"{path} missing/incorrect as_of"


def test_error_response_is_not_falsely_labeled(client):
    # A validation error is not reference data; it must NOT be stamped.
    resp = client.post("/api/geosigint/militia/classify", json={"vessels": []})
    assert resp.status_code == 400
    body = resp.get_json()
    assert "data_source" not in body


# ── Page badge ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", _PAGE_ROUTES)
def test_page_renders_static_reference_badge(client, route):
    resp = client.get(route)
    assert resp.status_code == 200, f"{route} -> {resp.status_code}"
    html = resp.get_data(as_text=True)
    assert _BADGE_TEXT in html, f"{route} missing '{_BADGE_TEXT}' badge"
    assert DATA_VINTAGE in html, f"{route} badge missing vintage {DATA_VINTAGE}"


@pytest.mark.parametrize("tpl", _STATIC_TEMPLATES)
def test_static_template_opts_into_badge(tpl):
    src = (_TPL / tpl).read_text(encoding="utf-8")
    assert "static_reference = true" in src, f"{tpl} does not set static_reference flag"


def test_base_template_contains_badge_markup():
    src = (_TPL / "base.html").read_text(encoding="utf-8")
    assert _BADGE_TEXT in src, "base.html missing badge markup"
    assert "if static_reference" in src, "base.html badge not gated on static_reference"


def test_dynamic_page_does_not_falsely_show_badge():
    # A page that does NOT set static_reference (e.g. the DB-backed dashboard,
    # rendered here with no flag) must not display the reference badge.
    from jinja2 import DictLoader, Environment

    base_src = (_TPL / "base.html").read_text(encoding="utf-8")
    env = Environment(loader=DictLoader({
        "base.html": base_src,
        "child.html": '{% extends "base.html" %}{% block content %}live{% endblock %}',
    }))
    rendered = env.get_template("child.html").render()
    assert _BADGE_TEXT not in rendered
