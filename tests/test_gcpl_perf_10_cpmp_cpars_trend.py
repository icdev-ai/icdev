# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/cpars/trend — CPARS trend data array.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'contract_id' key.
5. Response body contains 'trend' key.
6. Response body contains 'total_periods' key.
7. Response body contains 'rating_thresholds' key.
8. 'trend' value is a list.
9. Each trend entry contains 'id' key.
10. Each trend entry contains 'period_start' key.
11. Each trend entry contains 'period_end' key.
12. Each trend entry contains 'overall_rating' key.
13. Each trend entry contains 'predicted_overall' key.
14. 'total_periods' matches length of 'trend' array.
15. get_cpars_trend is called with the correct contract_id.
16. Contract not found — response status is 'error'.
17. Backend exception — returns 500 with error status.
18. Empty trend array when contract has no assessments.
19. 'overall_rating' values are valid CPARS rating strings.
20. Trend entries are ordered chronologically by period_end.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0010"
_MISSING_CONTRACT_ID = "ctr-not-found"

_TREND_ENTRY_1 = {
    "id": "cpars-001",
    "period_start": "2024-01",
    "period_end": "2024-06",
    "overall_rating": "satisfactory",
    "predicted_overall": "satisfactory",
    "quality_rating": "Satisfactory",
    "schedule_rating": "Satisfactory",
    "cost_rating": "Very Good",
    "management_rating": "Satisfactory",
    "small_business_rating": None,
    "negative_event_count": 1,
    "status": "final",
}

_TREND_ENTRY_2 = {
    "id": "cpars-002",
    "period_start": "2024-07",
    "period_end": "2024-12",
    "overall_rating": "very_good",
    "predicted_overall": "very_good",
    "quality_rating": "Very Good",
    "schedule_rating": "Very Good",
    "cost_rating": "Very Good",
    "management_rating": "Exceptional",
    "small_business_rating": None,
    "negative_event_count": 0,
    "status": "final",
}

_TREND_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "total_periods": 2,
    "trend": [_TREND_ENTRY_1, _TREND_ENTRY_2],
    "rating_thresholds": {
        "exceptional": 0.90,
        "very_good": 0.80,
        "satisfactory": 0.65,
        "marginal": 0.40,
    },
}

_EMPTY_TREND_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "total_periods": 0,
    "trend": [],
    "rating_thresholds": {
        "exceptional": 0.90,
        "very_good": 0.80,
        "satisfactory": 0.65,
        "marginal": 0.40,
    },
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_MISSING_CONTRACT_ID} not found",
}

_VALID_RATINGS = {"exceptional", "very_good", "satisfactory", "marginal", "unsatisfactory"}

_REQUIRED_ENTRY_KEYS = {"id", "period_start", "period_end", "overall_rating", "predicted_overall"}


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


def _get(client, contract_id):
    return client.get(f"/api/cpmp/contracts/{contract_id}/cpars/trend")


# ---------------------------------------------------------------------------
# Tests: successful trend retrieval
# ---------------------------------------------------------------------------


class TestGetCparsTrend:
    """GET with a valid contract_id returns 200 with trend data array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID, result=None):
        if result is None:
            result = _TREND_RESULT
        with patch(
            "tools.govcon.cpars_predictor.get_cpars_trend",
            return_value=result,
        ) as mock_trend:
            with api_app.test_client() as c:
                resp = _get(c, contract_id)
        return resp, mock_trend

    def test_returns_200(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_status_is_ok(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_contract_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "contract_id" in data

    def test_response_has_trend(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "trend" in data

    def test_response_has_total_periods(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_periods" in data

    def test_response_has_rating_thresholds(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "rating_thresholds" in data

    def test_trend_is_a_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["trend"], list)

    def test_trend_entry_has_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "id" in data["trend"][0]

    def test_trend_entry_has_period_start(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "period_start" in data["trend"][0]

    def test_trend_entry_has_period_end(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "period_end" in data["trend"][0]

    def test_trend_entry_has_overall_rating(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "overall_rating" in data["trend"][0]

    def test_trend_entry_has_predicted_overall(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "predicted_overall" in data["trend"][0]

    def test_total_periods_matches_trend_length(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total_periods"] == len(data["trend"])

    def test_get_trend_called_with_correct_contract_id(self, api_app):
        _, mock_trend = self._call(api_app)
        args, _ = mock_trend.call_args
        assert args[0] == _CONTRACT_ID

    def test_overall_rating_values_are_valid(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["trend"]:
            assert entry["overall_rating"] in _VALID_RATINGS

    def test_trend_entries_ordered_chronologically(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        periods = [e["period_end"] for e in data["trend"]]
        assert periods == sorted(periods)


# ---------------------------------------------------------------------------
# Tests: empty trend
# ---------------------------------------------------------------------------


class TestGetCparsTrendEmpty:
    """GET for a contract with no assessments returns empty trend array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.cpars_predictor.get_cpars_trend",
            return_value=_EMPTY_TREND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        return resp

    def test_empty_trend_returns_200(self, api_app):
        resp = self._call(api_app)
        assert resp.status_code == 200

    def test_empty_trend_is_list(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["trend"], list)

    def test_empty_trend_length_is_zero(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert len(data["trend"]) == 0

    def test_empty_total_periods_is_zero(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["total_periods"] == 0


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestGetCparsTrendEdgeCases:
    """Edge cases: contract not found and backend exceptions."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_not_found_returns_error_status(self, api_app):
        """Trend function returning not-found error must surface as status='error'."""
        with patch(
            "tools.govcon.cpars_predictor.get_cpars_trend",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _MISSING_CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_backend_exception_returns_500(self, api_app):
        """Unhandled backend exception must return 500 with error status."""
        with patch(
            "tools.govcon.cpars_predictor.get_cpars_trend",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"
