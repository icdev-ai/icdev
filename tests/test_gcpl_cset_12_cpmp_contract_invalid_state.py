# CUI // SP-CTI
"""Tests for PUT /api/cpmp/contracts/<id>/status — invalid state 'invalid_state_xyz' returns 400.

Verifies:
1. Sending status='invalid_state_xyz' (unknown state) returns 400.
2. Response is JSON.
3. Response body contains a 'message' key.
4. Message identifies the invalid transition.
5. Completely unknown state values (not just wrong direction) are rejected.
6. Response status field is 'error'.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-draft-0001"
_INVALID_STATE = "invalid_state_xyz"

_INVALID_STATE_RESULT = {
    "status": "error",
    "message": f"Invalid transition: draft → {_INVALID_STATE}. Allowed: ['active']",
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


def _put(client, contract_id, body):
    return client.put(
        f"/api/cpmp/contracts/{contract_id}/status",
        data=json.dumps(body),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: invalid_state_xyz returns 400
# ---------------------------------------------------------------------------


class TestInvalidStateXyz:
    """PUT with status='invalid_state_xyz' must return 400 with an error message."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.contract_manager.transition_contract",
            return_value=_INVALID_STATE_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _CONTRACT_ID, {"status": _INVALID_STATE})
        return resp

    def test_returns_400(self, api_app):
        resp = self._call(api_app)
        assert resp.status_code == 400

    def test_response_is_json(self, api_app):
        resp = self._call(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_message_key(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert "message" in data

    def test_response_status_is_error(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["status"] == "error"

    def test_response_message_identifies_invalid_transition(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert "Invalid transition" in data["message"]

    def test_response_message_contains_invalid_state_value(self, api_app):
        resp = self._call(api_app)
        data = resp.get_json()
        assert _INVALID_STATE in data["message"]


# ---------------------------------------------------------------------------
# Tests: other fully-unknown state values are also rejected
# ---------------------------------------------------------------------------


class TestOtherUnknownStates:
    """Any unrecognised state value must be rejected with 400."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    @pytest.mark.parametrize("bad_state", ["", "DRAFT", "unknown", "null", "12345"])
    def test_unknown_state_returns_400(self, api_app, bad_state):
        error_result = {
            "status": "error",
            "message": f"Invalid transition: draft → {bad_state}. Allowed: ['active']",
        }
        with patch(
            "tools.govcon.contract_manager.transition_contract",
            return_value=error_result,
        ):
            with api_app.test_client() as c:
                if bad_state == "":
                    # Empty string — route guard catches missing status before calling state machine
                    resp = _put(c, _CONTRACT_ID, {"status": bad_state})
                else:
                    resp = _put(c, _CONTRACT_ID, {"status": bad_state})
        assert resp.status_code == 400
