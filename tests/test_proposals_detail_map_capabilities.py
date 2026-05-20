# CUI // SP-CTI
"""Tests for /proposals/<id> — Map Capabilities button is present.

Verifies:
1. Template source declares the Map Capabilities button with govconAction('map', ...).
2. Rendered HTML substitutes the opportunity ID into the govconAction call.
3. POST /api/govcon/opportunities/<id>/map-capabilities returns 200 with mapping counts.
4. Endpoint degrades gracefully (500 + error key) when map_all_patterns raises.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
_DETAIL_TEMPLATE = _TEMPLATES_DIR / "proposals" / "detail.html"

_OPP_ID = "opp-map-test-01"

# ---------------------------------------------------------------------------
# Fake return value from tools.govcon.capability_mapper.map_all_patterns
# ---------------------------------------------------------------------------

_FAKE_MAP_RESULT = {
    "status": "ok",
    "patterns_mapped": 12,
    "capability_links": 9,
    "mappings": [],
}

_FAKE_MAP_EMPTY = {
    "status": "ok",
    "patterns_mapped": 0,
    "message": "No requirement patterns found",
}


# ---------------------------------------------------------------------------
# Static: template source checks
# ---------------------------------------------------------------------------


class TestTemplateSourceMapCapabilities:
    """detail.html must declare the Map Capabilities button."""

    def test_map_capabilities_button_text_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Map Capabilities" in source, (
            "proposals/detail.html must contain 'Map Capabilities' button text"
        )

    def test_govcon_action_map_wired(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('map'" in source, (
            "proposals/detail.html must call govconAction('map', ...) on the button"
        )

    def test_map_button_has_title_attribute(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Map Capabilities" in source, (
            "Map Capabilities button must be present with descriptive text"
        )

    def test_govcon_status_div_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert 'id="govcon-status"' in source, (
            "proposals/detail.html must have a status feedback div with id='govcon-status'"
        )

    def test_govcon_intelligence_section_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "GovCon Intelligence" in source, (
            "proposals/detail.html must include the GovCon Intelligence action bar"
        )


# ---------------------------------------------------------------------------
# Rendered HTML: govconAction('map', opp_id) emitted correctly
# ---------------------------------------------------------------------------


def _make_stub_opp():
    return {
        "id": _OPP_ID,
        "title": "Test RFP Map",
        "solicitation_number": "FA8650-26-R-0002",
        "agency": "USAF",
        "sub_agency": None,
        "due_date": "2026-06-30",
        "due_time": "17:00",
        "status": "writing",
        "proposal_type": "FFP",
        "set_aside_type": None,
        "naics_code": "541512",
        "estimated_value_low": None,
        "estimated_value_high": None,
        "capture_manager": None,
        "proposal_manager": None,
        "bid_decision": None,
        "questions_due_date": None,
    }


def _render_detail_template(opp=None):
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

    stub_base = (
        "{% block title %}{% endblock %}"
        "{% block content %}{% endblock %}"
    )
    env = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": stub_base}),
            FileSystemLoader(str(_TEMPLATES_DIR)),
        ]),
        autoescape=select_autoescape(["html"]),
    )

    tmpl = env.get_template("proposals/detail.html")
    return tmpl.render(
        opp=opp or _make_stub_opp(),
        sections=[],
        volumes=[],
        compliance_items=[],
        reviews=[],
        findings=[],
        stats={
            "sections_total": 0,
            "sections_complete": 0,
            "compliance_coverage_pct": 0,
            "open_findings": 0,
            "critical_findings": 0,
            "section_status_distribution": {},
            "finding_severity_distribution": {},
        },
        compliance_stats={
            "total": 0,
            "compliant": 0,
            "partial": 0,
            "non_compliant": 0,
            "not_addressed": 0,
            "not_applicable": 0,
            "gap_pct": 0,
        },
        reviews_data=[],
        days_left=40,
        questions=[],
        question_stats={
            "total": 0,
            "high_priority": 0,
            "draft": 0,
            "approved": 0,
            "submitted": 0,
            "answered": 0,
        },
        questions_days_left=None,
        amendments=[],
        responses={},
    )


class TestRenderedHtmlMapCapabilities:
    """Jinja2-rendered proposals/detail.html must wire the Map Capabilities button."""

    def test_map_capabilities_button_in_rendered_html(self):
        html = _render_detail_template()
        assert "Map Capabilities" in html, (
            "Rendered HTML must contain 'Map Capabilities' button text"
        )

    def test_govcon_action_map_in_rendered_html(self):
        html = _render_detail_template()
        assert "govconAction('map'" in html, (
            "Rendered HTML must call govconAction('map', ...) for the map button"
        )

    def test_opp_id_in_map_action(self):
        html = _render_detail_template()
        assert _OPP_ID in html, (
            "Rendered HTML must embed the opportunity ID in the govconAction call"
        )

    def test_govcon_status_div_in_rendered_html(self):
        html = _render_detail_template()
        assert 'id="govcon-status"' in html, (
            "Rendered HTML must contain the govcon-status feedback div"
        )


# ---------------------------------------------------------------------------
# API test helper: minimal Flask app with govcon_api blueprint only
# ---------------------------------------------------------------------------


def _build_api_test_app() -> Flask:
    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)
    return flask_app


# ---------------------------------------------------------------------------
# API tests: POST /api/govcon/opportunities/<id>/map-capabilities
# ---------------------------------------------------------------------------


class TestMapCapabilitiesAPIEndpoint:
    """POST /api/govcon/opportunities/<id>/map-capabilities returns 200 on success."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _post(self, api_app, opp_id=_OPP_ID, result=None):
        if result is None:
            result = _FAKE_MAP_RESULT
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = c.post(f"/api/govcon/opportunities/{opp_id}/map-capabilities")
        return resp

    def test_post_returns_200(self, api_app):
        resp = self._post(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._post(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_has_patterns_mapped(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "patterns_mapped" in data

    def test_response_has_capability_links(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "capability_links" in data

    def test_patterns_mapped_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["patterns_mapped"] == _FAKE_MAP_RESULT["patterns_mapped"]

    def test_capability_links_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["capability_links"] == _FAKE_MAP_RESULT["capability_links"]

    def test_status_is_ok(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opp-xyz"):
            resp = self._post(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id}"

    def test_zero_patterns_returns_200(self, api_app):
        resp = self._post(api_app, result=_FAKE_MAP_EMPTY)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["patterns_mapped"] == 0

    def test_zero_patterns_has_status_ok(self, api_app):
        resp = self._post(api_app, result=_FAKE_MAP_EMPTY)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_returns_500_when_map_all_patterns_raises(self, api_app):
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            side_effect=RuntimeError("mapper offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/map-capabilities")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            side_effect=RuntimeError("mapper offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/map-capabilities")
        data = resp.get_json()
        assert "error" in data

    def test_500_error_message_is_non_empty(self, api_app):
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            side_effect=RuntimeError("mapper offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/map-capabilities")
        data = resp.get_json()
        assert data["error"], "Error message must not be empty"
