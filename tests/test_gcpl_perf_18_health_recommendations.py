# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/health — recommendations array field.

Verifies:
1. GET /health returns 200.
2. Response is JSON.
3. Response body contains 'recommendations' key.
4. recommendations is a list.
5. recommendations is empty when all dimension scores are healthy (>= threshold).
6. recommendations is non-empty when at least one dimension score is below threshold.
7. compute_contract_health called with correct contract_id.
8. Each recommendation item has a 'dimension' key.
9. Each recommendation item has an 'action' key.
10. Recommendation dimension values are valid health dimensions.
11. Number of recommendations matches number of dims below threshold.
12. Backend exception returns 500.
13. Contract not found returns error status.
14. recommendations field survives JSON round-trip as list.
15. No duplicate dimension entries in recommendations.
16. action values are non-empty strings.
17. recommendations absent-dims get no entries.
18. _build_recommendations returns empty list when all scores at threshold.
19. _build_recommendations returns entry for each dim strictly below threshold.
20. RECOMMENDATION_THRESHOLD is exported from portfolio_manager.
"""

from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0018"
_VALID_DIMENSIONS = {"evm", "deliverables", "cpars", "negative_events", "funding"}

_HEALTH_ALL_GREEN = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "health": "green",
    "health_score": 0.9000,
    "dimension_scores": {
        "evm": 0.95,
        "deliverables": 0.90,
        "cpars": 0.88,
        "negative_events": 0.85,
        "funding": 0.92,
    },
    "weights": {"evm": 0.30, "deliverables": 0.25, "cpars": 0.20, "negative_events": 0.15, "funding": 0.10},
    "recommendations": [],
}

_HEALTH_WITH_RECOMMENDATIONS = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "health": "red",
    "health_score": 0.4800,
    "dimension_scores": {
        "evm": 0.40,
        "deliverables": 0.30,
        "cpars": 0.88,
        "negative_events": 0.85,
        "funding": 0.92,
    },
    "weights": {"evm": 0.30, "deliverables": 0.25, "cpars": 0.20, "negative_events": 0.15, "funding": 0.10},
    "recommendations": [
        {"dimension": "evm", "action": "Review earned value management — cost or schedule variance exceeds acceptable limits"},
        {"dimension": "deliverables", "action": "Address overdue or at-risk deliverables to prevent CPARS impact"},
    ],
}

_HEALTH_NOT_FOUND = {
    "status": "error",
    "message": f"Contract {_CONTRACT_ID} not found",
}


# ---------------------------------------------------------------------------
# Flask test app
# ---------------------------------------------------------------------------


def _build_cpmp_api_app() -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    @flask_app.before_request
    def _inject_fake_auth_0():
        from flask import g
        g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

    flask_app.register_blueprint(cpmp_api)
    return flask_app


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_health(client, contract_id):
    return client.get(
        f"/api/cpmp/contracts/{contract_id}/health",
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: successful health check — all green
# ---------------------------------------------------------------------------


class TestHealthRecommendationsAllGreen:
    """Health endpoint returns empty recommendations when all dimensions are healthy."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID, health_result=None):
        if health_result is None:
            health_result = _HEALTH_ALL_GREEN
        with patch(
            "tools.govcon.portfolio_manager.compute_contract_health",
            return_value=health_result,
        ) as mock_health:
            with api_app.test_client() as c:
                resp = _get_health(c, contract_id)
        return resp, mock_health

    def test_returns_200(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_recommendations_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "recommendations" in data

    def test_recommendations_is_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["recommendations"], list)

    def test_recommendations_empty_when_all_green(self, api_app):
        resp, _ = self._call(api_app, health_result=_HEALTH_ALL_GREEN)
        data = resp.get_json()
        assert data["recommendations"] == []

    def test_compute_contract_health_called_with_contract_id(self, api_app):
        _, mock_health = self._call(api_app)
        args, _ = mock_health.call_args
        assert args[0] == _CONTRACT_ID

    def test_recommendations_survives_json_roundtrip_as_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert type(data["recommendations"]) is list


# ---------------------------------------------------------------------------
# Tests: health with recommendations (dims below threshold)
# ---------------------------------------------------------------------------


class TestHealthRecommendationsPresent:
    """Health endpoint returns non-empty recommendations when dimensions fall below threshold."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.portfolio_manager.compute_contract_health",
            return_value=_HEALTH_WITH_RECOMMENDATIONS,
        ):
            with api_app.test_client() as c:
                resp = _get_health(c, _CONTRACT_ID)
        return resp

    def test_recommendations_non_empty_when_dims_below_threshold(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert len(data["recommendations"]) > 0

    def test_each_recommendation_has_dimension_key(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        for rec in data["recommendations"]:
            assert "dimension" in rec

    def test_each_recommendation_has_action_key(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        for rec in data["recommendations"]:
            assert "action" in rec

    def test_recommendation_dimensions_are_valid(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        for rec in data["recommendations"]:
            assert rec["dimension"] in _VALID_DIMENSIONS

    def test_recommendation_count_matches_below_threshold_dims(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        below_threshold = [
            dim
            for dim, score in _HEALTH_WITH_RECOMMENDATIONS["dimension_scores"].items()
            if score < 0.75
        ]
        assert len(data["recommendations"]) == len(below_threshold)

    def test_no_duplicate_dimension_entries(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        dims = [rec["dimension"] for rec in data["recommendations"]]
        assert len(dims) == len(set(dims))

    def test_action_values_are_non_empty_strings(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        for rec in data["recommendations"]:
            assert isinstance(rec["action"], str)
            assert len(rec["action"]) > 0


# ---------------------------------------------------------------------------
# Tests: _build_recommendations unit tests
# ---------------------------------------------------------------------------


class TestBuildRecommendations:
    """Unit tests for the _build_recommendations helper in portfolio_manager."""

    def _build(self, scores):
        from tools.govcon.portfolio_manager import _build_recommendations
        return _build_recommendations(scores)

    def test_empty_list_when_all_scores_at_threshold(self):
        scores = {dim: 0.75 for dim in _VALID_DIMENSIONS}
        result = self._build(scores)
        assert result == []

    def test_empty_list_when_all_scores_above_threshold(self):
        scores = {dim: 1.0 for dim in _VALID_DIMENSIONS}
        result = self._build(scores)
        assert result == []

    def test_returns_entry_for_each_dim_strictly_below_threshold(self):
        scores = {dim: 0.74 for dim in _VALID_DIMENSIONS}
        result = self._build(scores)
        assert len(result) == len(_VALID_DIMENSIONS)

    def test_only_below_threshold_dims_get_entries(self):
        scores = {dim: 1.0 for dim in _VALID_DIMENSIONS}
        scores["evm"] = 0.50
        result = self._build(scores)
        assert len(result) == 1
        assert result[0]["dimension"] == "evm"

    def test_recommendation_threshold_exported(self):
        from tools.govcon.portfolio_manager import RECOMMENDATION_THRESHOLD
        assert isinstance(RECOMMENDATION_THRESHOLD, float)
        assert 0.0 < RECOMMENDATION_THRESHOLD <= 1.0


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestHealthRecommendationsEdgeCases:
    """Backend exception returns 500; contract not found returns error status."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.portfolio_manager.compute_contract_health",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get_health(c, _CONTRACT_ID)
        assert resp.status_code == 500

    def test_backend_exception_returns_error_status(self, api_app):
        with patch(
            "tools.govcon.portfolio_manager.compute_contract_health",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get_health(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_contract_not_found_returns_error_status(self, api_app):
        with patch(
            "tools.govcon.portfolio_manager.compute_contract_health",
            return_value=_HEALTH_NOT_FOUND,
        ):
            with api_app.test_client() as c:
                resp = _get_health(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "error"
