# CUI // SP-CTI
"""Tests for PUT /api/cpmp/deliverables/<id>/status — not_started→in_progress state machine.

Verifies:
1. Valid not_started→in_progress transition returns 200 with correct payload.
2. Missing status field in request body returns 400.
3. Invalid transition (state machine violation) returns 400 with message.
4. Deliverable not found returns 400 with message.
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

_DELIVERABLE_ID = "deliv-notstarted-0001"

_OK_RESULT = {
    "status": "ok",
    "deliverable_id": _DELIVERABLE_ID,
    "old_status": "not_started",
    "new_status": "in_progress",
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Deliverable {_DELIVERABLE_ID} not found",
}

_INVALID_TRANSITION_RESULT = {
    "status": "error",
    "message": "Invalid transition: not_started → submitted. Allowed: ['in_progress']",
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


def _put(client, deliverable_id, body):
    return client.put(
        f"/api/cpmp/deliverables/{deliverable_id}/status",
        data=json.dumps(body),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Tests: valid not_started→in_progress transition
# ---------------------------------------------------------------------------


class TestNotStartedToInProgressTransition:
    """PUT /api/cpmp/deliverables/<id>/status with not_started→in_progress must succeed."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _transition(self, api_app, body=None, result=None):
        if body is None:
            body = {"status": "in_progress"}
        if result is None:
            result = _OK_RESULT
        with patch(
            "tools.govcon.contract_manager.transition_deliverable",
            return_value=result,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _DELIVERABLE_ID, body)
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

    def test_response_has_deliverable_id(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["deliverable_id"] == _DELIVERABLE_ID

    def test_response_has_old_status_not_started(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["old_status"] == "not_started"

    def test_response_has_new_status_in_progress(self, api_app):
        resp = self._transition(api_app)
        data = resp.get_json()
        assert data["new_status"] == "in_progress"


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
            resp = _put(c, _DELIVERABLE_ID, {})
        assert resp.status_code == 400

    def test_response_has_message_key(self, api_app):
        with api_app.test_client() as c:
            resp = _put(c, _DELIVERABLE_ID, {})
        data = resp.get_json()
        assert "message" in data

    def test_response_message_mentions_status(self, api_app):
        with api_app.test_client() as c:
            resp = _put(c, _DELIVERABLE_ID, {})
        data = resp.get_json()
        assert "status" in data["message"].lower()


# ---------------------------------------------------------------------------
# Tests: invalid state machine transition
# ---------------------------------------------------------------------------


class TestInvalidTransition:
    """not_started→submitted is not an allowed transition; endpoint must return 400."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _bad_transition(self, api_app):
        with patch(
            "tools.govcon.contract_manager.transition_deliverable",
            return_value=_INVALID_TRANSITION_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _DELIVERABLE_ID, {"status": "submitted"})
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
# Tests: deliverable not found
# ---------------------------------------------------------------------------


class TestDeliverableNotFound:
    """Transition on an unknown deliverable_id must return 400."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _not_found(self, api_app):
        with patch(
            "tools.govcon.contract_manager.transition_deliverable",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, "nonexistent-id", {"status": "in_progress"})
        return resp

    def test_returns_400(self, api_app):
        resp = self._not_found(api_app)
        assert resp.status_code == 400

    def test_response_has_message_key(self, api_app):
        resp = self._not_found(api_app)
        data = resp.get_json()
        assert "message" in data

    def test_response_message_mentions_not_found(self, api_app):
        resp = self._not_found(api_app)
        data = resp.get_json()
        assert "not found" in data["message"].lower()


# ---------------------------------------------------------------------------
# Tests: exception → 500
# ---------------------------------------------------------------------------


class TestTransitionException:
    """Unexpected exception from transition_deliverable must return 500."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _boom(self, api_app):
        with patch(
            "tools.govcon.contract_manager.transition_deliverable",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = _put(c, _DELIVERABLE_ID, {"status": "in_progress"})
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

        def _fake_transition(deliverable_id, new_status, changed_by=None, reason=None):
            captured["changed_by"] = changed_by
            return _OK_RESULT

        with patch(
            "tools.govcon.contract_manager.transition_deliverable",
            side_effect=_fake_transition,
        ):
            with api_app.test_client() as c:
                _put(c, _DELIVERABLE_ID, {"status": "in_progress", "changed_by": "user-42"})

        assert captured.get("changed_by") == "user-42"

    def test_reason_forwarded(self, api_app):
        captured = {}

        def _fake_transition(deliverable_id, new_status, changed_by=None, reason=None):
            captured["reason"] = reason
            return _OK_RESULT

        with patch(
            "tools.govcon.contract_manager.transition_deliverable",
            side_effect=_fake_transition,
        ):
            with api_app.test_client() as c:
                _put(
                    c,
                    _DELIVERABLE_ID,
                    {"status": "in_progress", "reason": "work order issued"},
                )

        assert captured.get("reason") == "work order issued"
