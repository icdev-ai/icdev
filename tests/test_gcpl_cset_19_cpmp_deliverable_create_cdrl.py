# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/deliverables — creates CDRL with due date.

Verifies:
1. Successful creation returns 201.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains a 'deliverable_id' key.
5. Returned deliverable_id is a non-empty string.
6. create_deliverable is called with the correct contract_id.
7. create_deliverable is called with cdrl_number 'A001'.
8. create_deliverable is called with did_number 'DI-MGMT-81466'.
9. create_deliverable is called with due_date '2026-12-31'.
10. create_deliverable is called with type 'cdrl'.
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
_DELIVERABLE_ID = "deliv-cdrl-a001"

_CDRL_PAYLOAD = {
    "cdrl_number": "A001",
    "did_number": "DI-MGMT-81466",
    "title": "Contract Performance Report",
    "description": "Monthly contract performance reporting deliverable",
    "type": "cdrl",
    "frequency": "monthly",
    "due_date": "2026-12-31",
}

_OK_RESULT = {"status": "ok", "deliverable_id": _DELIVERABLE_ID}
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
    return client.post(f"/api/cpmp/contracts/{contract_id}/deliverables", **kwargs)


# ---------------------------------------------------------------------------
# Tests: successful CDRL creation with due date
# ---------------------------------------------------------------------------


class TestCreateCdrlWithDueDate:
    """POST with CDRL A001 / DI-MGMT-81466 and due date must return 201 with deliverable_id."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, payload=None):
        body = payload if payload is not None else _CDRL_PAYLOAD
        with patch(
            "tools.govcon.contract_manager.create_deliverable",
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

    def test_response_has_deliverable_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "deliverable_id" in data

    def test_deliverable_id_is_non_empty_string(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["deliverable_id"], str)
        assert len(data["deliverable_id"]) > 0

    def test_create_deliverable_called_with_correct_contract_id(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        assert args[0] == _CONTRACT_ID

    def test_create_deliverable_called_with_cdrl_number_a001(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("cdrl_number") == "A001"

    def test_create_deliverable_called_with_did_number(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("did_number") == "DI-MGMT-81466"

    def test_create_deliverable_called_with_due_date(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("due_date") == "2026-12-31"

    def test_create_deliverable_called_with_type_cdrl(self, api_app):
        _, mock_create = self._call(api_app)
        args, _ = mock_create.call_args
        payload_sent = args[1]
        assert payload_sent.get("type") == "cdrl"


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestCreateCdrlEdgeCases:
    """Edge cases: missing body, contract not found."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_body_does_not_crash(self, api_app):
        """No request body — route must pass empty dict to create_deliverable, not crash."""
        with patch(
            "tools.govcon.contract_manager.create_deliverable",
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
            "tools.govcon.contract_manager.create_deliverable",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _CDRL_PAYLOAD)
        assert resp.status_code == 400

    def test_contract_not_found_response_has_message(self, api_app):
        with patch(
            "tools.govcon.contract_manager.create_deliverable",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _CDRL_PAYLOAD)
        data = resp.get_json()
        assert "message" in data
