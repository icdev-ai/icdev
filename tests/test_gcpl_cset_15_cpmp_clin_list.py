# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/clins — returns CLIN list array.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'clins' key.
5. 'clins' value is a list (array).
6. list_clins is called with the correct contract_id.
7. Response body contains 'total' key.
8. 'total' equals the length of 'clins'.
9. Contract with no CLINs returns 200 with empty 'clins' list.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"

_CLIN_1 = {
    "id": "clin-test-0001",
    "contract_id": _CONTRACT_ID,
    "clin_number": "0001",
    "type": "labor",
    "total_value": 250000.0,
    "funded_value": 250000.0,
    "billed_value": 50000.0,
    "status": "active",
}
_CLIN_2 = {
    "id": "clin-test-0002",
    "contract_id": _CONTRACT_ID,
    "clin_number": "0002",
    "type": "materials",
    "total_value": 100000.0,
    "funded_value": 80000.0,
    "billed_value": 0.0,
    "status": "active",
}

_OK_RESULT = {"status": "ok", "total": 2, "clins": [_CLIN_1, _CLIN_2]}
_EMPTY_RESULT = {"status": "ok", "total": 0, "clins": []}


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
    return client.get(f"/api/cpmp/contracts/{contract_id}/clins")


# ---------------------------------------------------------------------------
# Tests: successful CLIN list retrieval
# ---------------------------------------------------------------------------


class TestListClinsReturnsClinArray:
    """GET returns 200 with a JSON body containing a clins array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.contract_manager.list_clins",
            return_value=_OK_RESULT,
        ) as mock_list:
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        return resp, mock_list

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

    def test_response_has_clins_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "clins" in data

    def test_clins_is_a_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["clins"], list)

    def test_list_clins_called_with_correct_contract_id(self, api_app):
        _, mock_list = self._call(api_app)
        args, _ = mock_list.call_args
        assert args[0] == _CONTRACT_ID

    def test_response_has_total(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_total_equals_clins_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["clins"])


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestListClinsEdgeCases:
    """Edge cases: contract with no CLINs."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_empty_contract_returns_200_with_empty_list(self, api_app):
        """Contract with no CLINs must return 200 with an empty clins array."""
        with patch(
            "tools.govcon.contract_manager.list_clins",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clins"] == []
        assert data["total"] == 0
