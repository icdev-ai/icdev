# CUI // SP-CTI
"""Tests for GET /api/govcon/opportunities/<id>/bid-recommendation — direct API returns 200.

Verifies:
1. GET returns 200 with JSON on success.
2. Response includes top-level keys: status, opportunity_id, overall, by_domain, bid_recommendation.
3. bid_recommendation contains a score key.
4. bid_recommendation.score is a float in [0.0, 1.0].
5. Endpoint handles arbitrary opportunity IDs.
6. Endpoint returns insufficient_data result (no requirements) with score == 0.0.
7. Endpoint returns 500 + error key when get_summary raises.
8. The endpoint is RBAC-gated: 401 unauthenticated, 403 for a non-write role.
"""
from unittest.mock import patch

import pytest

from tests._govcon_api_app import NON_WRITE_ROLE, WRITE_ROLE, build_govcon_api_app

# ---------------------------------------------------------------------------
# Fake return values from tools.govcon.compliance_populator.get_summary
# ---------------------------------------------------------------------------

_OPP_ID = "opp-bid-rec-test-01"

_FAKE_SUMMARY_RESULT = {
    "status": "ok",
    "opportunity_id": _OPP_ID,
    "overall": {
        "total": 4,
        "L": 3,
        "M": 1,
        "N": 0,
        "compliance_rate": 0.75,
    },
    "by_domain": {
        "devsecops": {"L": 2, "M": 1, "N": 0, "total": 3},
        "security": {"L": 1, "M": 0, "N": 0, "total": 1},
    },
    "bid_recommendation": {
        "decision": "strong_bid",
        "score": 0.75,
        "bid": True,
        "reason": "75% compliant, only 0% gaps. Strong capability alignment.",
        "confidence": "high",
    },
}

_FAKE_SUMMARY_NO_REQS = {
    "status": "ok",
    "opportunity_id": "opp-empty-bid-01",
    "overall": {"total": 0, "L": 0, "M": 0, "N": 0, "compliance_rate": 0.0},
    "by_domain": {},
    "bid_recommendation": {
        "decision": "insufficient_data",
        "score": 0.0,
        "bid": False,
        "reason": "No requirements extracted",
    },
}


# ---------------------------------------------------------------------------
# API test helper: minimal Flask app with govcon_api blueprint only
# ---------------------------------------------------------------------------


def _build_api_test_app(role=WRITE_ROLE):
    """App carrying the real blueprint, authenticated as a GovCon write role.

    The endpoint is ``@require_role(*GOVCON_WRITE_ROLES)`` (prop-fix-09); an app
    with no ``g.current_user`` answers 401 to every one of these assertions. The
    gate is exercised, not removed — see TestBidRecommendationRBAC below.
    """
    return build_govcon_api_app(role=role)


# ---------------------------------------------------------------------------
# API tests: GET /api/govcon/opportunities/<id>/bid-recommendation
# ---------------------------------------------------------------------------


class TestBidRecommendationAPIEndpoint:
    """GET /api/govcon/opportunities/<id>/bid-recommendation returns 200 with score."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _get(self, api_app, opp_id=_OPP_ID, result=None):
        if result is None:
            result = _FAKE_SUMMARY_RESULT
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{opp_id}/bid-recommendation")
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        data = self._get(api_app).get_json()
        assert "status" in data

    def test_response_has_bid_recommendation_key(self, api_app):
        data = self._get(api_app).get_json()
        assert "bid_recommendation" in data

    def test_bid_recommendation_has_score_key(self, api_app):
        data = self._get(api_app).get_json()
        assert "score" in data["bid_recommendation"]

    def test_bid_recommendation_score_is_numeric(self, api_app):
        data = self._get(api_app).get_json()
        score = data["bid_recommendation"]["score"]
        assert isinstance(score, (int, float))

    def test_bid_recommendation_score_in_valid_range(self, api_app):
        data = self._get(api_app).get_json()
        score = data["bid_recommendation"]["score"]
        assert 0.0 <= score <= 1.0

    def test_bid_recommendation_score_matches_seeded_data(self, api_app):
        data = self._get(api_app).get_json()
        assert data["bid_recommendation"]["score"] == 0.75

    def test_response_has_overall_key(self, api_app):
        data = self._get(api_app).get_json()
        assert "overall" in data

    def test_response_opportunity_id_matches(self, api_app):
        data = self._get(api_app).get_json()
        assert data["opportunity_id"] == _OPP_ID

    def test_arbitrary_opportunity_id_returns_200(self, api_app):
        resp = self._get(api_app, opp_id="opp-arbitrary-xyz-99")
        assert resp.status_code == 200

    def test_no_requirements_score_is_zero(self, api_app):
        resp = self._get(api_app, opp_id="opp-empty-bid-01", result=_FAKE_SUMMARY_NO_REQS)
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["bid_recommendation"]["score"] == 0.0

    def test_no_requirements_decision_is_insufficient_data(self, api_app):
        resp = self._get(api_app, opp_id="opp-empty-bid-01", result=_FAKE_SUMMARY_NO_REQS)
        data = resp.get_json()
        assert data["bid_recommendation"]["decision"] == "insufficient_data"

    def test_get_summary_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            side_effect=RuntimeError("DB connection failed"),
        ):
            with api_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{_OPP_ID}/bid-recommendation")
        assert resp.status_code == 500

    def test_exception_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            side_effect=RuntimeError("DB connection failed"),
        ):
            with api_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{_OPP_ID}/bid-recommendation")
        data = resp.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# RBAC: the gate that made every assertion above answer 401 (prop-fix-09)
# ---------------------------------------------------------------------------


class TestBidRecommendationRBAC:
    """bid-recommendation is @require_role(*GOVCON_WRITE_ROLES).

    The bid/no-bid score is capture-sensitive competitive intelligence, so the
    read is held to the write roles rather than being open to any authenticated
    caller. These assertions exist so the gate can never be silently removed to
    make the suite above go green.
    """

    def _get(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            return_value=_FAKE_SUMMARY_RESULT,
        ):
            with api_app.test_client() as c:
                return c.get(f"/api/govcon/opportunities/{_OPP_ID}/bid-recommendation")

    def test_role_fixtures_match_the_live_role_tuple(self):
        from tools.dashboard.api.govcon import GOVCON_WRITE_ROLES

        assert WRITE_ROLE in GOVCON_WRITE_ROLES
        assert NON_WRITE_ROLE not in GOVCON_WRITE_ROLES

    def test_unauthenticated_is_401(self):
        resp = self._get(build_govcon_api_app(role=None))
        assert resp.status_code == 401, (
            "bid-recommendation must reject an unauthenticated caller with 401"
        )

    def test_non_write_role_is_403(self):
        resp = self._get(build_govcon_api_app(role=NON_WRITE_ROLE))
        assert resp.status_code == 403, (
            f"bid-recommendation must deny role '{NON_WRITE_ROLE}' with 403, not "
            f"{resp.status_code}"
        )

    def test_every_write_role_is_allowed(self):
        from tools.dashboard.api.govcon import GOVCON_WRITE_ROLES

        for role in GOVCON_WRITE_ROLES:
            resp = self._get(build_govcon_api_app(role=role))
            assert resp.status_code == 200, f"role '{role}' should be allowed"
