# CUI // SP-CTI
"""Tests for PUT /api/cpmp/contracts/<id>/status — draft→active state machine.

Verifies:
1. Valid draft→active transition returns 200 with correct payload.
2. Missing status field in request body returns 400.
3. Invalid transition (state machine violation) returns 400 with message.
4. Contract not found returns 400 with message.
5. Unexpected exception returns 500.
6. changed_by and reason are forwarded to the state machine.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Fake payloads
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-draft-0001"

_OK_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "old_status": "draft",
    "new_status": "active",
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_CONTRACT_ID} not found",
}

_INVALID_TRANSITION_RESULT = {
    "status": "error",
    "message": "Invalid transition: draft → complete. Allowed: ['active']",
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
# Tests: valid draft→active transition
# ---------------------------------------------------------------------------


class TestDraftToActiveTransition:
    """PUT /api/cpmp/contracts/<id>/status with draft→active must succeed."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _transition(self, api_app, body=None, result=None):
        if body is None:
            body = {"status": "active"}
        if result is None:
            result = _OK_RESULT
        with patch(
            "tools.govcon.contract_manager.transition_contract",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _CONTRACT_ID, body)
        return resp

    def test_returns_200(self, api_app):
        resp = self._transition(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = self._transition(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_status_is_ok(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_contract_id(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["contract_id"] == _CONTRACT_ID

    def test_response_has_old_status_draft(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["old_status"] == "draft"

    def test_response_has_new_status_active(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["new_status"] == "active"


# ---------------------------------------------------------------------------
# Tests: missing status field
# ---------------------------------------------------------------------------


class TestMissingStatusField:
    """PUT without a 'status' field must return 400 before calling the state machine."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_returns_400(self, api_app):
        with api_app.test_client() as c:
            resp = _put(c, _CONTRACT_ID, {})
        assert resp.status_code == 400

    def test_response_has_message_key(self, api_app):
        with api_app.test_client() as c:
            resp = _put(c, _CONTRACT_ID, {})
        data = resp.get_json()
        assert "message" in data

    def test_response_message_mentions_status(self, api_app):
        with api_app.test_client() as c:
            resp = _put(c, _CONTRACT_ID, {})
        data = resp.get_json()
        assert "status" in data["message"].lower()


# ---------------------------------------------------------------------------
# Tests: invalid state machine transition
# ---------------------------------------------------------------------------


class TestInvalidTransition:
    """draft→complete is not an allowed transition; endpoint must return 400."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _bad_transition(self, api_app):
        with patch(
            "tools.govcon.contract_manager.transition_contract",
            return_value=_INVALID_TRANSITION_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _CONTRACT_ID, {"status": "complete"})
        return resp

    def test_returns_400(self, api_app):
        resp = self._bad_transition(api_app)
        assert resp.status_code == 400

    def test_response_is_json(self, api_app):
        resp = self._bad_transition(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_message_key(self, api_app):
        resp = self._bad_transition(api_app)
        data = resp.get_json()
        assert "message" in data

    def test_response_message_contains_invalid_transition(self, api_app):
        resp = self._bad_transition(api_app)
        data = resp.get_json()
        assert "Invalid transition" in data["message"]

    def test_response_message_mentions_allowed(self, api_app):
        resp = self._bad_transition(api_app)
        data = resp.get_json()
        assert "Allowed" in data["message"]


# ---------------------------------------------------------------------------
# Tests: contract not found
# ---------------------------------------------------------------------------


class TestContractNotFound:
    """Transition on an unknown contract_id must return 400."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _not_found(self, api_app):
        with patch(
            "tools.govcon.contract_manager.transition_contract",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, "nonexistent-id", {"status": "active"})
        return resp

    def test_returns_400(self, api_app):
        resp = self._not_found(api_app)
        assert resp.status_code == 400

    def test_response_has_message_key(self, api_app):
        resp = self._not_found(api_app)
        data = resp.get_json()
        assert "message" in data

    def test_response_message_mentions_contract(self, api_app):
        resp = self._not_found(api_app)
        data = resp.get_json()
        assert "not found" in data["message"].lower()


# ---------------------------------------------------------------------------
# Tests: exception → 500
# ---------------------------------------------------------------------------


class TestTransitionException:
    """Unexpected exception from transition_contract must return 500."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _boom(self, api_app):
        with patch(
            "tools.govcon.contract_manager.transition_contract",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = _put(c, _CONTRACT_ID, {"status": "active"})
        return resp

    def test_returns_500(self, api_app):
        resp = self._boom(api_app)
        assert resp.status_code == 500

    def test_response_has_message_key(self, api_app):
        resp = self._boom(api_app)
        data = resp.get_json()
        assert "message" in data


# ---------------------------------------------------------------------------
# Tests: forwarding changed_by and reason
# ---------------------------------------------------------------------------


class TestFieldForwarding:
    """changed_by and reason must be forwarded to the state machine call."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_changed_by_forwarded(self, api_app):
        captured = {}

        def _fake_transition(contract_id, new_status, changed_by=None, reason=None):
            captured["changed_by"] = changed_by
            return _OK_RESULT

        with patch(
            "tools.govcon.contract_manager.transition_contract",
            side_effect=_fake_transition,
        ):
            with api_app.test_client() as c:
                _put(c, _CONTRACT_ID, {"status": "active", "changed_by": "user-42"})

        assert captured.get("changed_by") == "user-42"

    def test_reason_forwarded(self, api_app):
        captured = {}

        def _fake_transition(contract_id, new_status, changed_by=None, reason=None):
            captured["reason"] = reason
            return _OK_RESULT

        with patch(
            "tools.govcon.contract_manager.transition_contract",
            side_effect=_fake_transition,
        ):
            with api_app.test_client() as c:
                _put(
                    c,
                    _CONTRACT_ID,
                    {"status": "active", "reason": "contract fully executed"},
                )

        assert captured.get("reason") == "contract fully executed"
