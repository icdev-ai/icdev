# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/negative-events/ndaa-thresholds — threshold status.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'contract_id' key.
5. contract_id in response matches path parameter.
6. Response body contains 'notification_required' key.
7. notification_required is a boolean.
8. Response body contains 'trigger_count' key.
9. trigger_count is a non-negative integer.
10. Response body contains 'triggers' key.
11. triggers is a list.
12. Response body contains 'total_penalty' key.
13. total_penalty is a non-negative float.
14. Response body contains 'critical_event_count' key.
15. Response body contains 'termination_event_count' key.
16. notification_required is True when triggers are present.
17. notification_required is False when no triggers (clean contract).
18. check_ndaa_thresholds called with correct contract_id.
19. Backend exception returns 500 with error status.
20. Contract not found returns error status in response body.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0014"

_THRESHOLDS_CLEAN = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "notification_required": False,
    "trigger_count": 0,
    "triggers": [],
    "total_penalty": 0.05,
    "critical_event_count": 0,
    "termination_event_count": 0,
}

_THRESHOLDS_TRIGGERED = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "notification_required": True,
    "trigger_count": 2,
    "triggers": [
        {
            "trigger": "critical_severity",
            "description": "1 critical severity event(s) open",
            "events": [{"id": "evt-001", "event_type": "cost_overrun", "description": "Cost exceeded ceiling"}],
        },
        {
            "trigger": "penalty_threshold",
            "description": "Total active penalty (0.3500) exceeds 0.30 threshold",
            "total_penalty": 0.35,
        },
    ],
    "total_penalty": 0.35,
    "critical_event_count": 1,
    "termination_event_count": 0,
}

_THRESHOLDS_NOT_FOUND = {
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


def _get(client, contract_id):
    return client.get(
        f"/api/cpmp/contracts/{contract_id}/negative-events/ndaa-thresholds",
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: successful threshold check (clean contract)
# ---------------------------------------------------------------------------


class TestGetNdaaThresholdsSuccess:
    """GET ndaa-thresholds returns 200 with full threshold status for a clean contract."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID, threshold_result=None):
        if threshold_result is None:
            threshold_result = _THRESHOLDS_CLEAN
        with patch(
            "tools.govcon.negative_event_tracker.check_ndaa_thresholds",
            return_value=threshold_result,
        ) as mock_check:
            with api_app.test_client() as c:
                resp = _get(c, contract_id)
        return resp, mock_check

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

    def test_contract_id_matches_path_parameter(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["contract_id"] == _CONTRACT_ID

    def test_response_has_notification_required(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "notification_required" in data

    def test_notification_required_is_boolean(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["notification_required"], bool)

    def test_response_has_trigger_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "trigger_count" in data

    def test_trigger_count_is_non_negative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["trigger_count"] >= 0

    def test_response_has_triggers(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "triggers" in data

    def test_triggers_is_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["triggers"], list)

    def test_response_has_total_penalty(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_penalty" in data

    def test_total_penalty_is_non_negative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total_penalty"] >= 0.0

    def test_response_has_critical_event_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "critical_event_count" in data

    def test_response_has_termination_event_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "termination_event_count" in data

    def test_check_ndaa_thresholds_called_with_contract_id(self, api_app):
        _, mock_check = self._call(api_app)
        args, _ = mock_check.call_args
        assert args[0] == _CONTRACT_ID

    def test_notification_required_false_when_no_triggers(self, api_app):
        resp, _ = self._call(api_app, threshold_result=_THRESHOLDS_CLEAN)
        data = resp.get_json()
        assert data["notification_required"] is False

    def test_triggers_empty_when_clean(self, api_app):
        resp, _ = self._call(api_app, threshold_result=_THRESHOLDS_CLEAN)
        data = resp.get_json()
        assert data["triggers"] == []


# ---------------------------------------------------------------------------
# Tests: threshold triggered — notification required
# ---------------------------------------------------------------------------


class TestGetNdaaThresholdsTriggered:
    """GET returns notification_required=True when any NDAA threshold is breached."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_notification_required_true_when_triggers_present(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.check_ndaa_thresholds",
            return_value=_THRESHOLDS_TRIGGERED,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["notification_required"] is True

    def test_trigger_count_matches_triggers_list_length(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.check_ndaa_thresholds",
            return_value=_THRESHOLDS_TRIGGERED,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["trigger_count"] == len(data["triggers"])


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestGetNdaaThresholdsEdgeCases:
    """Backend exception returns 500; contract not found returns error status."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_backend_exception_returns_500(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.check_ndaa_thresholds",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 500

    def test_backend_exception_returns_error_status(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.check_ndaa_thresholds",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "error"

    def test_contract_not_found_returns_error_status(self, api_app):
        with patch(
            "tools.govcon.negative_event_tracker.check_ndaa_thresholds",
            return_value=_THRESHOLDS_NOT_FOUND,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data.get("status") == "error"
