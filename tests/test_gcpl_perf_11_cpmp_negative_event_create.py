# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/negative-events — negative event recording.

Verifies:
1. Successful POST returns 201.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'event_id' key.
5. Response body contains 'event_type' key.
6. Response body contains 'severity' key.
7. Response body contains 'cpars_impact' key.
8. record_event called with correct contract_id.
9. record_event called with payload containing event_type == 'delinquent_delivery'.
10. record_event called with payload containing severity.
11. record_event called with payload containing description.
12. event_type in response equals 'delinquent_delivery'.
13. cpars_impact value is positive (NDAA penalty applied).
14. Missing event_type — record_event returns error, response is 400.
15. Missing severity — record_event returns error, response is 400.
16. Invalid event_type — record_event returns error, response is 400.
17. Invalid severity — record_event returns error, response is 400.
18. Contract not found — response status is 'error'.
19. Backend exception — returns 500 with error status.
20. contract_id forwarded into request payload by blueprint.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0011"
_MISSING_CONTRACT_ID = "ctr-not-found"
_EVENT_ID = "evt-uuid-0011"
_EVENT_TYPE = "delinquent_delivery"
_SEVERITY = "high"
_DESCRIPTION = "Deliverable overdue by 14 days — NDAA §875 threshold breached"

_FULL_PAYLOAD = {
    "event_type": _EVENT_TYPE,
    "severity": _SEVERITY,
    "description": _DESCRIPTION,
}

_RECORD_RESULT = {
    "status": "ok",
    "event_id": _EVENT_ID,
    "event_type": _EVENT_TYPE,
    "severity": _SEVERITY,
    "cpars_impact": 0.05,
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_MISSING_CONTRACT_ID} not found",
}

_MISSING_EVENT_TYPE_RESULT = {
    "status": "error",
    "message": "Invalid event_type: None. Valid: ['delinquent_delivery', ...]",
}

_MISSING_SEVERITY_RESULT = {
    "status": "error",
    "message": "Invalid severity: None. Valid: ['low', 'medium', 'high', 'critical']",
}

_INVALID_EVENT_TYPE_RESULT = {
    "status": "error",
    "message": "Invalid event_type: bad_type.",
}

_INVALID_SEVERITY_RESULT = {
    "status": "error",
    "message": "Invalid severity: extreme.",
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
        f"/api/cpmp/contracts/{contract_id}/negative-events",
        json=payload,
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: successful creation
# ---------------------------------------------------------------------------


class TestPostNegativeEventCreate:
    """POST with valid delinquent_delivery payload returns 201 with event record."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID, payload=None):
        if payload is None:
            payload = _FULL_PAYLOAD
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            return_value=_RECORD_RESULT,
        ) as mock_record:
            with api_app.test_client() as c:
                resp = _post(c, contract_id, payload)
        return resp, mock_record

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

    def test_response_has_event_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "event_id" in data

    def test_response_has_event_type(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "event_type" in data

    def test_response_has_severity(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "severity" in data

    def test_response_has_cpars_impact(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "cpars_impact" in data

    def test_record_called_with_correct_contract_id(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        assert args[0] == _CONTRACT_ID

    def test_record_called_with_payload_containing_event_type(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        data_arg = args[1]
        assert data_arg.get("event_type") == _EVENT_TYPE

    def test_record_called_with_payload_containing_severity(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        data_arg = args[1]
        assert "severity" in data_arg

    def test_record_called_with_payload_containing_description(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        data_arg = args[1]
        assert "description" in data_arg

    def test_event_type_value_is_delinquent_delivery(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["event_type"] == "delinquent_delivery"

    def test_cpars_impact_is_positive(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["cpars_impact"] > 0


# ---------------------------------------------------------------------------
# Tests: validation — invalid or missing required fields
# ---------------------------------------------------------------------------


class TestPostNegativeEventValidation:
    """record_event error response causes blueprint to return 400."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_event_type_returns_400(self, api_app):
        payload = {"severity": _SEVERITY, "description": _DESCRIPTION}
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            return_value=_MISSING_EVENT_TYPE_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, payload)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_missing_severity_returns_400(self, api_app):
        payload = {"event_type": _EVENT_TYPE, "description": _DESCRIPTION}
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            return_value=_MISSING_SEVERITY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, payload)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_invalid_event_type_returns_400(self, api_app):
        payload = {"event_type": "bad_type", "severity": _SEVERITY, "description": _DESCRIPTION}
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            return_value=_INVALID_EVENT_TYPE_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, payload)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_invalid_severity_returns_400(self, api_app):
        payload = {"event_type": _EVENT_TYPE, "severity": "extreme", "description": _DESCRIPTION}
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            return_value=_INVALID_SEVERITY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, payload)
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("status") == "error"


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestPostNegativeEventEdgeCases:
    """Edge cases: contract not found, backend exceptions, and payload forwarding."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_not_found_returns_error_status(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _MISSING_CONTRACT_ID, _FULL_PAYLOAD)
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _FULL_PAYLOAD)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_contract_id_forwarded_in_payload(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.record_event",
            return_value=_RECORD_RESULT,
        ) as mock_record:
            with api_app.test_client() as c:
                _post(c, _CONTRACT_ID, _FULL_PAYLOAD)
        args, _ = mock_record.call_args
        data_arg = args[1]
        assert data_arg.get("contract_id") == _CONTRACT_ID
