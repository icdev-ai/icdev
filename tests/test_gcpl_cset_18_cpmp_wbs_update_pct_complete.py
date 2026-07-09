# CUI // SP-CTI
"""Tests for PUT /api/cpmp/wbs/<id> — updates WBS percent_complete to 25%.

Verifies:
1. Successful update returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains a 'wbs_id' key.
5. Returned wbs_id is a non-empty string.
6. Response body contains a 'level' key.
7. update_wbs is called with the correct wbs_id.
8. update_wbs is called with percent_complete == 25.0.
9. Missing request body defaults to an empty dict (no crash).
10. WBS-not-found result returns 404.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WBS_ID = "wbs-test-0001"

_UPDATE_25PCT_PAYLOAD = {
    "percent_complete": 25.0,
}

_OK_RESULT = {"status": "ok", "wbs_id": _WBS_ID, "level": 2}
_NOT_FOUND_RESULT = {"status": "error", "message": f"WBS element {_WBS_ID} not found"}


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


def _put(client, wbs_id, body=None):
    kwargs = {"content_type": "application/json"}
    if body is not None:
        kwargs["data"] = json.dumps(body)
    return client.put(f"/api/cpmp/wbs/{wbs_id}", **kwargs)


# ---------------------------------------------------------------------------
# Tests: successful WBS percent_complete update to 25%
# ---------------------------------------------------------------------------


class TestUpdateWbsPercentComplete:
    """PUT with percent_complete=25.0 must return 200 with wbs_id and level."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, payload=None):
        body = payload if payload is not None else _UPDATE_25PCT_PAYLOAD
        with patch(
            "tools.govcon.contract_manager.update_wbs",
            return_value=_OK_RESULT,
        ) as mock_update:
            with api_app.test_client() as c:
                resp = _put(c, _WBS_ID, body)
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

    def test_update_wbs_called_with_correct_wbs_id(self, api_app):
        _, mock_update = self._call(api_app)
        args, _ = mock_update.call_args
        assert args[0] == _WBS_ID

    def test_update_wbs_called_with_25_pct_complete(self, api_app):
        _, mock_update = self._call(api_app)
        args, _ = mock_update.call_args
        payload_sent = args[1]
        assert payload_sent.get("percent_complete") == 25.0


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestUpdateWbsEdgeCases:
    """Edge cases: missing body, WBS not found."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_body_does_not_crash(self, api_app):
        """No request body — route must pass empty dict to update_wbs, not crash."""
        with patch(
            "tools.govcon.contract_manager.update_wbs",
            return_value=_OK_RESULT,
        ) as mock_update:
            with api_app.test_client() as c:
                resp = _put(c, _WBS_ID)
        assert resp.status_code == 200
        args, _ = mock_update.call_args
        assert isinstance(args[1], dict)

    def test_wbs_not_found_returns_404(self, api_app):
        """When contract_manager signals not found, route must return 404."""
        with patch(
            "tools.govcon.contract_manager.update_wbs",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _WBS_ID, _UPDATE_25PCT_PAYLOAD)
        assert resp.status_code == 404

    def test_wbs_not_found_response_has_message(self, api_app):
        with patch(
            "tools.govcon.contract_manager.update_wbs",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _put(c, _WBS_ID, _UPDATE_25PCT_PAYLOAD)
        data = resp.get_json()
        assert "message" in data
