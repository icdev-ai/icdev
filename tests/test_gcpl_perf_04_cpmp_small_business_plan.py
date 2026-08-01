# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/small-business — records small business plan.

Verifies:
1. Successful POST returns 201.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'report_id' key.
5. Response body contains 'report_type' key.
6. Response body contains 'reporting_period' key.
7. create_sb_report is called with the correct contract_id.
8. create_sb_report is called with the correct period.
9. SSR report type accepted — returns 201.
10. Missing period returns 400 with error message.
11. Backend error result returns 400.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"

_ISR_PAYLOAD = {
    "period": "2025-Q1",
    "type": "isr",
}
_SSR_PAYLOAD = {
    "period": "2025-Q1",
    "type": "ssr",
}

_OK_RESULT = {
    "status": "ok",
    "report_id": "sbp-test-0001",
    "report_type": "isr",
    "reporting_period": "2025-Q1",
    "total_subcontract_dollars": 750000.0,
    "compliant": True,
}
_SSR_OK_RESULT = {
    "status": "ok",
    "report_id": "sbp-test-0002",
    "report_type": "ssr",
    "reporting_period": "2025-Q1",
    "total_subcontract_dollars": 750000.0,
    "compliant": True,
}
_ERROR_RESULT = {
    "status": "error",
    "message": "Contract ctr-test-0001 not found",
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


def _post(client, contract_id, body=None):
    kwargs = {"content_type": "application/json"}
    if body is not None:
        kwargs["data"] = json.dumps(body)
    return client.post(
        f"/api/cpmp/contracts/{contract_id}/small-business", **kwargs
    )


# ---------------------------------------------------------------------------
# Tests: successful small business plan creation (ISR)
# ---------------------------------------------------------------------------


class TestCreateSmallBusinessPlanISR:
    """POST with valid ISR payload returns 201 with plan record."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, payload=None):
        body = payload if payload is not None else _ISR_PAYLOAD
        with patch(
            "tools.govcon.subcontractor_tracker.create_sb_report",
            return_value=_OK_RESULT,
        ) as mock_create:
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, body)
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

    def test_response_has_report_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "report_id" in data

    def test_response_has_report_type(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "report_type" in data

    def test_response_has_reporting_period(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "reporting_period" in data

    def test_create_called_with_correct_contract_id(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[0] == _CONTRACT_ID

    def test_create_called_with_correct_period(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[1] == _ISR_PAYLOAD["period"]


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestCreateSmallBusinessPlanEdgeCases:
    """Edge cases: SSR type, missing period, and backend errors."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_ssr_type_returns_201(self, api_app):
        """SSR report type is a valid FAR 52.219-9 submission — must return 201."""
        with patch(
            "tools.govcon.subcontractor_tracker.create_sb_report",
            return_value=_SSR_OK_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _SSR_PAYLOAD)
        assert resp.status_code == 201

    def test_missing_period_returns_400(self, api_app):
        """period is required; omitting it must return 400."""
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, {"type": "isr"})
        assert resp.status_code == 400

    def test_missing_period_error_message(self, api_app):
        """400 response body must include an error message for missing period."""
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, {"type": "isr"})
        data = resp.get_json()
        assert data.get("status") == "error"
        assert "period" in data.get("message", "").lower()

    def test_backend_error_result_returns_400(self, api_app):
        """When the backend returns status == 'error', the route must return 400."""
        with patch(
            "tools.govcon.subcontractor_tracker.create_sb_report",
            return_value=_ERROR_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _ISR_PAYLOAD)
        assert resp.status_code == 400
