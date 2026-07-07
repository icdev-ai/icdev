# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/cpars/predict — CPARS weighted score + rating.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'contract_id' key.
5. Response body contains 'predicted_score' key.
6. Response body contains 'predicted_rating' key.
7. Response body contains 'dimension_scores' key with all 5 dimensions.
8. Response body contains 'weights' key.
9. Response body contains 'ndaa_penalty' key.
10. Response body contains 'rating_thresholds' key.
11. predict_cpars is called with the correct contract_id.
12. Contract not found — response status is 'error'.
13. Backend exception — returns 500 with error status.
14. predicted_score is in [0.0, 1.0].
15. predicted_rating is one of the valid CPARS rating strings.
16. dimension_scores contains quality, schedule, cost, management, small_business.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0006"
_MISSING_CONTRACT_ID = "ctr-not-found"

_PREDICT_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "dimension_scores": {
        "quality": 0.9500,
        "schedule": 0.8000,
        "cost": 1.0000,
        "management": 0.8500,
        "small_business": 0.9200,
    },
    "weights": {
        "quality": 0.25,
        "schedule": 0.25,
        "cost": 0.20,
        "management": 0.15,
        "small_business": 0.15,
    },
    "ndaa_penalty": 0.0,
    "ndaa_penalty_details": [],
    "predicted_score": 0.8980,
    "predicted_rating": "very_good",
    "rating_thresholds": {
        "exceptional": 0.90,
        "very_good": 0.75,
        "satisfactory": 0.60,
        "marginal": 0.40,
    },
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_MISSING_CONTRACT_ID} not found",
}

_VALID_RATINGS = {"exceptional", "very_good", "satisfactory", "marginal", "unsatisfactory"}
_REQUIRED_DIMENSIONS = {"quality", "schedule", "cost", "management", "small_business"}


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
    return client.get(f"/api/cpmp/contracts/{contract_id}/cpars/predict")


# ---------------------------------------------------------------------------
# Tests: successful prediction
# ---------------------------------------------------------------------------


class TestGetCparsPredict:
    """GET with a valid contract_id returns 200 with weighted score + rating."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID):
        with patch(
            "tools.govcon.cpars_predictor.predict_cpars",
            return_value=_PREDICT_RESULT,
        ) as mock_predict:
            with api_app.test_client() as c:
                resp = _get(c, contract_id)
        return resp, mock_predict

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

    def test_response_has_predicted_score(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "predicted_score" in data

    def test_response_has_predicted_rating(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "predicted_rating" in data

    def test_response_has_dimension_scores(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "dimension_scores" in data

    def test_response_has_weights(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "weights" in data

    def test_response_has_ndaa_penalty(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "ndaa_penalty" in data

    def test_response_has_rating_thresholds(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "rating_thresholds" in data

    def test_predict_called_with_correct_contract_id(self, api_app):
        _, mock_predict = self._call(api_app)
        args, _ = mock_predict.call_args
        assert args[0] == _CONTRACT_ID

    def test_predicted_score_in_valid_range(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        score = data["predicted_score"]
        assert 0.0 <= score <= 1.0

    def test_predicted_rating_is_valid(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["predicted_rating"] in _VALID_RATINGS

    def test_dimension_scores_has_all_dimensions(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        dims = set(data["dimension_scores"].keys())
        assert _REQUIRED_DIMENSIONS.issubset(dims)


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestGetCparsPredictEdgeCases:
    """Edge cases: contract not found and backend exceptions."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_not_found_returns_error_status(self, api_app):
        """Predictor returning not-found error must surface as status='error'."""
        with patch(
            "tools.govcon.cpars_predictor.predict_cpars",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _MISSING_CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_backend_exception_returns_500(self, api_app):
        """Unhandled backend exception must return 500 with error status."""
        with patch(
            "tools.govcon.cpars_predictor.predict_cpars",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"
