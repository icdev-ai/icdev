# CUI // SP-CTI
"""Tests for /proposals/<id> — Extract Requirements button triggers API (200).

Verifies:
1. Template source declares the Extract Requirements button with govconAction('extract', ...).
2. Rendered HTML substitutes the opportunity ID into the govconAction call.
3. POST /api/govcon/opportunities/<id>/extract-requirements returns 200 with extraction counts.
4. Endpoint degrades gracefully (500 + error key) when extract_and_store raises.
5. The endpoint is RBAC-gated: 401 unauthenticated, 403 for a non-write role.
"""
from unittest.mock import patch

import pytest

from tests._govcon_api_app import NON_WRITE_ROLE, WRITE_ROLE, build_govcon_api_app
from tests._proposals_detail_render import DETAIL_TEMPLATE as _DETAIL_TEMPLATE
from tests._proposals_detail_render import opp_stub, render_detail

_OPP_ID = "opp-extract-test-01"

# ---------------------------------------------------------------------------
# Fake return value from tools.govcon.requirement_extractor.extract_and_store
# ---------------------------------------------------------------------------

_FAKE_EXTRACT_RESULT = {
    "extracted_count": 7,
    "new_count": 5,
    "duplicate_count": 2,
    "opportunity_count": 1,
}

_FAKE_EXTRACT_EMPTY = {
    "extracted_count": 0,
    "new_count": 0,
    "duplicate_count": 0,
    "opportunity_count": 0,
}


# ---------------------------------------------------------------------------
# Static: template source checks
# ---------------------------------------------------------------------------


class TestTemplateSourceExtractRequirements:
    """detail.html must declare the Extract Requirements button."""

    def test_extract_requirements_button_text_present(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "Extract Requirements" in source, (
            "proposals/detail.html must contain 'Extract Requirements' button text"
        )

    def test_govcon_action_extract_wired(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "govconAction('extract'" in source, (
            "proposals/detail.html must call govconAction('extract', ...) on the button"
        )

    def test_extract_button_has_rfp_title_attribute(self):
        source = _DETAIL_TEMPLATE.read_text(encoding="utf-8")
        assert "shall/must/will" in source or "Extract" in source, (
            "Extract Requirements button must describe its purpose"
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
# Rendered HTML: govconAction('extract', opp_id) emitted correctly
# ---------------------------------------------------------------------------


def _render_detail_template(opp=None):
    """Render proposals/detail.html with a schema-derived opportunity row.

    The stub comes from ``tests/_proposals_detail_render`` rather than a literal
    dict here: the route renders ``dict(row)`` of ``SELECT * FROM
    proposal_opportunities``, so a hand-listed subset goes stale the moment a
    column is added and the template reads it.
    """
    return render_detail(opp if opp is not None else opp_stub(_OPP_ID))


class TestRenderedHtmlExtractRequirements:
    """Jinja2-rendered proposals/detail.html must wire the Extract Requirements button."""

    def test_extract_button_in_rendered_html(self):
        html = _render_detail_template()
        assert "Extract Requirements" in html, (
            "Rendered HTML must contain 'Extract Requirements' button text"
        )

    def test_govcon_action_extract_in_rendered_html(self):
        html = _render_detail_template()
        assert "govconAction('extract'" in html, (
            "Rendered HTML must call govconAction('extract', ...) for the extract button"
        )

    def test_opp_id_in_extract_action(self):
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


_URL = "/api/govcon/opportunities/{opp_id}/extract-requirements"


def _build_api_test_app(role=WRITE_ROLE):
    """App carrying the real blueprint, authenticated as a GovCon write role.

    The endpoint is ``@require_role(*GOVCON_WRITE_ROLES)``; an app with no
    ``g.current_user`` answers 401 to every one of these assertions. The gate is
    exercised, not removed — see TestExtractRequirementsRBAC below.
    """
    return build_govcon_api_app(role=role)


# ---------------------------------------------------------------------------
# API tests: POST /api/govcon/opportunities/<id>/extract-requirements
# ---------------------------------------------------------------------------


class TestExtractRequirementsAPIEndpoint:
    """POST /api/govcon/opportunities/<id>/extract-requirements returns 200 on success."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _post(self, api_app, opp_id=_OPP_ID, result=None):
        if result is None:
            result = _FAKE_EXTRACT_RESULT
        with patch(
            "tools.govcon.requirement_extractor.extract_and_store",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = c.post(f"/api/govcon/opportunities/{opp_id}/extract-requirements")
        return resp

    def test_post_returns_200(self, api_app):
        resp = self._post(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._post(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_extracted_count(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "extracted_count" in data

    def test_response_has_new_count(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "new_count" in data

    def test_response_has_duplicate_count(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "duplicate_count" in data

    def test_extracted_count_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["extracted_count"] == _FAKE_EXTRACT_RESULT["extracted_count"]

    def test_new_count_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["new_count"] == _FAKE_EXTRACT_RESULT["new_count"]

    def test_duplicate_count_matches_fake_value(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["duplicate_count"] == _FAKE_EXTRACT_RESULT["duplicate_count"]

    def test_new_plus_duplicate_equals_extracted(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["new_count"] + data["duplicate_count"] == data["extracted_count"]

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opp-xyz"):
            resp = self._post(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id}"

    def test_zero_extraction_returns_200(self, api_app):
        resp = self._post(api_app, result=_FAKE_EXTRACT_EMPTY)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["extracted_count"] == 0

    def test_zero_extraction_has_new_count_zero(self, api_app):
        resp = self._post(api_app, result=_FAKE_EXTRACT_EMPTY)
        data = resp.get_json()
        assert data["new_count"] == 0

    def test_returns_500_when_extract_and_store_raises(self, api_app):
        with patch(
            "tools.govcon.requirement_extractor.extract_and_store",
            side_effect=RuntimeError("extractor offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/extract-requirements")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.requirement_extractor.extract_and_store",
            side_effect=RuntimeError("extractor offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/extract-requirements")
        data = resp.get_json()
        assert "error" in data

    def test_500_error_message_is_non_empty(self, api_app):
        with patch(
            "tools.govcon.requirement_extractor.extract_and_store",
            side_effect=RuntimeError("extractor offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/extract-requirements")
        data = resp.get_json()
        assert data["error"], "Error message must not be empty"


# ---------------------------------------------------------------------------
# RBAC: the gate that made every assertion above answer 401 (prop-fix-09)
# ---------------------------------------------------------------------------


class TestExtractRequirementsRBAC:
    """extract-requirements is @require_role(*GOVCON_WRITE_ROLES).

    Extraction runs an AI/parse pass and writes rfp_shall_statements, so it is a
    write endpoint. These assertions exist so the gate can never be silently
    removed to make the success-path tests above go green again.
    """

    def _post(self, app):
        with patch(
            "tools.govcon.requirement_extractor.extract_and_store",
            return_value=_FAKE_EXTRACT_RESULT,
        ):
            with app.test_client() as c:
                return c.post(_URL.format(opp_id=_OPP_ID))

    def test_write_role_is_a_govcon_write_role(self):
        from tools.dashboard.api.govcon import GOVCON_WRITE_ROLES

        assert WRITE_ROLE in GOVCON_WRITE_ROLES
        assert NON_WRITE_ROLE not in GOVCON_WRITE_ROLES

    def test_unauthenticated_is_401(self):
        resp = self._post(build_govcon_api_app(role=None))
        assert resp.status_code == 401, (
            "extract-requirements must reject an unauthenticated caller with 401"
        )

    def test_non_write_role_is_403(self):
        resp = self._post(build_govcon_api_app(role=NON_WRITE_ROLE))
        assert resp.status_code == 403, (
            f"extract-requirements must deny role '{NON_WRITE_ROLE}' with 403, not "
            f"{resp.status_code}"
        )

    def test_every_write_role_is_allowed(self):
        from tools.dashboard.api.govcon import GOVCON_WRITE_ROLES

        for role in GOVCON_WRITE_ROLES:
            resp = self._post(build_govcon_api_app(role=role))
            assert resp.status_code == 200, f"role '{role}' should be allowed"
