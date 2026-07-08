# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/cpars — CPARS assessment record creation.

Verifies:
1. Successful POST returns 201.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'assessment_id' key.
5. Response body contains 'contract_id' key.
6. Response body contains 'period_start' key.
7. Response body contains 'period_end' key.
8. Response body contains 'predicted_score' key.
9. Response body contains 'predicted_rating' key.
10. create_assessment called with correct contract_id.
11. create_assessment called with correct period_start.
12. create_assessment called with correct period_end.
13. create_assessment called with score data payload.
14. Missing period_start — returns 400 with error status.
15. Missing period_end — returns 400 with error status.
16. Both period fields absent — returns 400 with error status.
17. Contract not found — response status is 'error'.
18. Backend exception — returns 500 with error status.
19. Scores (quality/schedule/cost/mgmt/sb) forwarded in data arg.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0009"
_MISSING_CONTRACT_ID = "ctr-not-found"
_PERIOD_START = "2025-01"
_PERIOD_END = "2025-06"
_ASSESSMENT_ID = "asm-uuid-0009"

_SCORES = {
    "quality_rating": 0.92,
    "schedule_rating": 0.85,
    "cost_rating": 0.78,
    "management_rating": 0.88,
    "small_business_rating": 0.75,
}

_CREATE_RESULT = {
    "status": "ok",
    "assessment_id": _ASSESSMENT_ID,
    "contract_id": _CONTRACT_ID,
    "period_start": _PERIOD_START,
    "period_end": _PERIOD_END,
    "predicted_score": 0.8560,
    "predicted_rating": "very_good",
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_MISSING_CONTRACT_ID} not found",
}

_VALID_RATINGS = {"exceptional", "very_good", "satisfactory", "marginal", "unsatisfactory"}

_FULL_PAYLOAD = {
    "period_start": _PERIOD_START,
    "period_end": _PERIOD_END,
    **_SCORES,
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


def _post(client, contract_id, payload):
    return client.post(
        f"/api/cpmp/contracts/{contract_id}/cpars",
        json=payload,
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: successful creation
# ---------------------------------------------------------------------------


class TestPostCparsCreate:
    """POST with valid payload returns 201 with assessment record."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID, payload=None):
        if payload is None:
            payload = _FULL_PAYLOAD
        with patch(
            "tools.govcon.cpars_predictor.create_assessment",
            return_value=_CREATE_RESULT,
        ) as mock_create:
            with api_app.test_client() as c:
                resp = _post(c, contract_id, payload)
        return resp, mock_create

    def test_returns_201(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.status_code == 201

    def test_response_is_json(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_status_is_ok(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_assessment_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "assessment_id" in data

    def test_response_has_contract_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "contract_id" in data

    def test_response_has_period_start(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "period_start" in data

    def test_response_has_period_end(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "period_end" in data

    def test_response_has_predicted_score(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "predicted_score" in data

    def test_response_has_predicted_rating(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "predicted_rating" in data

    def test_create_called_with_correct_contract_id(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[0] == _CONTRACT_ID

    def test_create_called_with_correct_period_start(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[1] == _PERIOD_START

    def test_create_called_with_correct_period_end(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[2] == _PERIOD_END

    def test_create_called_with_data_containing_scores(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        data_arg = args[3]
        for field in _SCORES:
            assert field in data_arg, f"Expected score field '{field}' in data arg"

    def test_predicted_rating_is_valid(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["predicted_rating"] in _VALID_RATINGS


# ---------------------------------------------------------------------------
# Tests: validation — missing required fields
# ---------------------------------------------------------------------------


class TestPostCparsValidation:
    """Missing required fields return 400 with error status."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_period_start_returns_400(self, api_app):
        payload = {"period_end": _PERIOD_END, **_SCORES}
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, payload)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_missing_period_end_returns_400(self, api_app):
        payload = {"period_start": _PERIOD_START, **_SCORES}
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, payload)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_both_periods_absent_returns_400(self, api_app):
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, _SCORES)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestPostCparsEdgeCases:
    """Edge cases: contract not found and backend exceptions."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_not_found_returns_error_status(self, api_app):
        with patch(
            "tools.govcon.cpars_predictor.create_assessment",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _MISSING_CONTRACT_ID, _FULL_PAYLOAD)
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.cpars_predictor.create_assessment",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _FULL_PAYLOAD)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"
