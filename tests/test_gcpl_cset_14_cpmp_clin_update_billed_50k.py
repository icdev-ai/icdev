# CUI // SP-CTI
"""Tests for PUT /api/cpmp/clins/<id> — updates CLIN billed value to $50K.

Verifies:
1. Successful update returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'clin_id'.
5. Returned clin_id matches the requested CLIN id.
6. update_clin is called with the correct clin_id.
7. update_clin is called with billed_value == 50000.0.
8. Missing request body defaults to empty dict (no crash).
9. CLIN not found returns 404.
10. CLIN not found response includes 'message'.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLIN_ID = "clin-test-0001"

_UPDATE_BILLED_50K_PAYLOAD = {
    "billed_value": 50000.0,
}

_OK_RESULT = {"status": "ok", "clin_id": _CLIN_ID}
_NOT_FOUND_RESULT = {"status": "error", "message": f"CLIN {_CLIN_ID} not found"}


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


def _put(client, clin_id, body=None):
    kwargs = {"content_type": "application/json"}
    if body is not None:
        kwargs["data"] = json.dumps(body)
    return client.put(f"/api/cpmp/clins/{clin_id}", **kwargs)


# ---------------------------------------------------------------------------
# Tests: successful billed value update to $50K
# ---------------------------------------------------------------------------


class TestUpdateClinBilledValue50K:
    """PUT with billed_value=50000.0 must return 200 with clin_id."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, payload=None):
        body = payload if payload is not None else _UPDATE_BILLED_50K_PAYLOAD
        with patch(
            "tools.govcon.contract_manager.update_clin",
            return_value=_OK_RESULT,
        ) as mock_update:
            with api_app.test_client() as c:
                resp = _put(c, _CLIN_ID, body)
        return resp, mock_update

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

    def test_response_has_clin_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "clin_id" in data

    def test_response_clin_id_matches_requested_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["clin_id"] == _CLIN_ID

    def test_update_clin_called_with_correct_clin_id(self, api_app):
        _, mock_update = self._call(api_app)
        args, _ = mock_update.call_args
        assert args[0] == _CLIN_ID

    def test_update_clin_called_with_50k_billed_value(self, api_app):
        _, mock_update = self._call(api_app)
        args, _ = mock_update.call_args
        payload_sent = args[1]
        assert payload_sent.get("billed_value") == 50000.0


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestUpdateClinEdgeCases:
    """Edge cases: missing body, CLIN not found."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_body_does_not_crash(self, api_app):
        """No request body — route must pass empty dict to update_clin, not crash."""
        with patch(
            "tools.govcon.contract_manager.update_clin",
            return_value=_OK_RESULT,
        ) as mock_update:
            with api_app.test_client() as c:
                resp = _put(c, _CLIN_ID)
        assert resp.status_code == 200
        args, _ = mock_update.call_args
        assert isinstance(args[1], dict)

    def test_clin_not_found_returns_404(self, api_app):
        """When contract_manager signals not found, route must return 404."""
        with patch(
            "tools.govcon.contract_manager.update_clin",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _CLIN_ID, _UPDATE_BILLED_50K_PAYLOAD)
        assert resp.status_code == 404

    def test_clin_not_found_response_has_message(self, api_app):
        """Error response must include a human-readable message."""
        with patch(
            "tools.govcon.contract_manager.update_clin",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _CLIN_ID, _UPDATE_BILLED_50K_PAYLOAD)
        data = resp.get_json()
        assert "message" in data
