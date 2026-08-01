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


# nav-plat-06: the badge markup moved out of base.html into a shared partial so
# it renders regardless of which base.html wins the Jinja search path (the
# geosigint-local one standalone, or the main dashboard one when mounted). The
# 7 static templates include the partial directly.
_BADGE_PARTIAL = "_geosigint_reference_badge.html"


def test_badge_partial_contains_markup():
    src = (_TPL / _BADGE_PARTIAL).read_text(encoding="utf-8")
    assert _BADGE_TEXT in src, f"{_BADGE_PARTIAL} missing badge markup"
    assert "if static_reference" in src, f"{_BADGE_PARTIAL} badge not gated on static_reference"


@pytest.mark.parametrize("tpl", _STATIC_TEMPLATES)
def test_static_template_includes_badge_partial(tpl):
    src = (_TPL / tpl).read_text(encoding="utf-8")
    assert _BADGE_PARTIAL in src, f"{tpl} does not include the badge partial"


def test_base_template_does_not_hardcode_badge():
    # The badge must NOT live in base.html anymore — otherwise pages rendered via
    # geosigint's own base.html AND the content-block include would show it twice.
    src = (_TPL / "base.html").read_text(encoding="utf-8")
    assert _BADGE_TEXT not in src, "base.html should no longer carry badge markup (moved to partial)"


def test_dynamic_page_does_not_falsely_show_badge():
    # A page that does NOT include the badge partial and does not set
    # static_reference (e.g. the DB-backed dashboard) must not display the badge.
    from jinja2 import DictLoader, Environment

    base_src = (_TPL / "base.html").read_text(encoding="utf-8")
    partial_src = (_TPL / _BADGE_PARTIAL).read_text(encoding="utf-8")
    env = Environment(loader=DictLoader({
        "base.html": base_src,
        _BADGE_PARTIAL: partial_src,
        # No include, no static_reference — mimics a DB-backed dynamic page.
        "child.html": '{% extends "base.html" %}{% block content %}live{% endblock %}',
    }))
    rendered = env.get_template("child.html").render()
    assert _BADGE_TEXT not in rendered


def test_badge_renders_when_dashboard_base_shadows_geosigint_base():
    # Regression for nav-plat-06 / the E2E honesty-banner defect: when the
    # geosigint blueprint mounts under the main dashboard, `{% extends
    # "base.html" %}` resolves to the app-level base.html (which has no badge),
    # shadowing geosigint's own base.html. Because the badge now lives in a
    # partial the static templates include directly, it must still render. This
    # test simulates the collision with a competing app-level base.html.
    app = Flask(__name__)

    # A minimal main-dashboard-like base.html WITHOUT the badge, registered on
    # the app loader so it wins over the blueprint's base.html.
    from jinja2 import ChoiceLoader, DictLoader

    app.jinja_loader = ChoiceLoader([
        DictLoader({
            "base.html": (
                "<!doctype html><html><head><title>"
                "{% block title %}Dash{% endblock %}</title>{% block head %}{% endblock %}"
                "</head><body><nav>MAIN DASHBOARD NAV</nav><main>"
                "{% block content %}{% endblock %}</main>{% block scripts %}{% endblock %}"
                "</body></html>"
            ),
        }),
        app.jinja_loader,  # falls through to the app's own 'templates' dir (empty)
    ])
    app.register_blueprint(create_geosigint_blueprint())
    app.config["TESTING"] = True
    client = app.test_client()

    for route in _PAGE_ROUTES:
        resp = client.get(route)
        assert resp.status_code == 200, f"{route} -> {resp.status_code}"
        html = resp.get_data(as_text=True)
        # Confirm the app-level base actually shadowed geosigint's base...
        assert "MAIN DASHBOARD NAV" in html, f"{route} did not render the shadowing base"
        # ...and the badge STILL renders via the included partial.
        assert _BADGE_TEXT in html, f"{route} badge lost under dashboard base collision"
        # Exactly one badge — no double-render.
        assert html.count(_BADGE_TEXT) == 1, f"{route} rendered the badge {html.count(_BADGE_TEXT)} times"
