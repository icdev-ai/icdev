# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/wbs — creates WBS element with BAC=$500K and planned dates.

Verifies:
1. Successful creation returns 201.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains a 'wbs_id' key.
5. Returned wbs_id is a non-empty string.
6. Response body contains a 'level' key.
7. create_wbs is called with the correct contract_id.
8. create_wbs is called with budget_at_completion 500000.0.
9. create_wbs is called with planned_start date.
10. create_wbs is called with planned_finish date.
11. Missing request body defaults to an empty dict (no crash).
12. Contract-not-found result returns 400.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"
_WBS_ID = "wbs-test-0001"

_WBS_500K_PAYLOAD = {
    "wbs_number": "1.1",
    "title": "System Engineering",
    "description": "Top-level system engineering WBS element",
    "budget_at_completion": 500000.0,
    "planned_start": "2026-01-01",
    "planned_finish": "2026-12-31",
    "responsible_person": "Jane Smith",
    "status": "not_started",
}

_OK_RESULT = {"status": "ok", "wbs_id": _WBS_ID, "level": 2}
_NOT_FOUND_RESULT = {"status": "error", "message": f"Contract {_CONTRACT_ID} not found"}


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
    return client.post(f"/api/cpmp/contracts/{contract_id}/wbs", **kwargs)


# ---------------------------------------------------------------------------
# Tests: successful WBS creation at $500K BAC with planned dates
# ---------------------------------------------------------------------------


class TestCreateWbs500K:
    """POST with BAC=$500K and planned dates must return 201 with wbs_id and level."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, payload=None):
        body = payload if payload is not None else _WBS_500K_PAYLOAD
        with patch(
            "tools.govcon.contract_manager.create_wbs",
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

    def test_response_has_wbs_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "wbs_id" in data

    def test_wbs_id_is_non_empty_string(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["wbs_id"], str)
        assert len(data["wbs_id"]) > 0

    def test_response_has_level(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "level" in data

    def test_create_wbs_called_with_correct_contract_id(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[0] == _CONTRACT_ID

    def test_create_wbs_called_with_500k_bac(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("budget_at_completion") == 500000.0

    def test_create_wbs_called_with_planned_start(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("planned_start") == "2026-01-01"

    def test_create_wbs_called_with_planned_finish(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("planned_finish") == "2026-12-31"


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestCreateWbsEdgeCases:
    """Edge cases: missing body, contract not found."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_body_does_not_crash(self, api_app):
        """No request body — route must pass empty dict to create_wbs, not crash."""
        with patch(
            "tools.govcon.contract_manager.create_wbs",
            return_value=_OK_RESULT,
        ) as mock_create:
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID)
        assert resp.status_code == 201
        args, _ = mock_create.call_args
        assert isinstance(args[1], dict)

    def test_contract_not_found_returns_400(self, api_app):
        """When contract_manager signals not found, route must return 400."""
        with patch(
            "tools.govcon.contract_manager.create_wbs",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _WBS_500K_PAYLOAD)
        assert resp.status_code == 400

    def test_contract_not_found_response_has_message(self, api_app):
        with patch(
            "tools.govcon.contract_manager.create_wbs",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _WBS_500K_PAYLOAD)
        data = resp.get_json()
        assert "message" in data
