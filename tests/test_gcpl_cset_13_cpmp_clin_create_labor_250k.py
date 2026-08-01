# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/clins — creates labor CLIN at $250K.

Verifies:
1. Successful creation returns 201.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains a 'clin_id' key.
5. Returned clin_id is a non-empty string.
6. create_clin is called with the correct contract_id.
7. create_clin is called with clin_type/type 'labor'.
8. create_clin is called with total_value 250000.0.
9. Missing request body defaults to an empty dict (no crash).
10. Contract-not-found result returns 400.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"
_CLIN_ID = "clin-labor-0001"

_LABOR_250K_PAYLOAD = {
    "clin_number": "0001",
    "description": "Software development labor",
    "type": "labor",
    "total_value": 250000.0,
    "funded_value": 250000.0,
}

_OK_RESULT = {"status": "ok", "clin_id": _CLIN_ID}
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
    return client.post(f"/api/cpmp/contracts/{contract_id}/clins", **kwargs)


# ---------------------------------------------------------------------------
# Tests: successful labor CLIN creation at $250K
# ---------------------------------------------------------------------------


class TestCreateLaborClin250K:
    """POST with labor type and $250K total_value must return 201 with clin_id."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, payload=None):
        body = payload if payload is not None else _LABOR_250K_PAYLOAD
        with patch(
            "tools.govcon.contract_manager.create_clin",
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

    def test_response_has_clin_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "clin_id" in data

    def test_clin_id_is_non_empty_string(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["clin_id"], str)
        assert len(data["clin_id"]) > 0

    def test_create_clin_called_with_correct_contract_id(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[0] == _CONTRACT_ID

    def test_create_clin_called_with_labor_type(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("type") == "labor"

    def test_create_clin_called_with_250k_total_value(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("total_value") == 250000.0


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestCreateClinEdgeCases:
    """Edge cases: missing body, contract not found."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_body_does_not_crash(self, api_app):
        """No request body — route must pass empty dict to create_clin, not crash."""
        with patch(
            "tools.govcon.contract_manager.create_clin",
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
            "tools.govcon.contract_manager.create_clin",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _LABOR_250K_PAYLOAD)
        assert resp.status_code == 400

    def test_contract_not_found_response_has_message(self, api_app):
        with patch(
            "tools.govcon.contract_manager.create_clin",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _LABOR_250K_PAYLOAD)
        data = resp.get_json()
        assert "message" in data
