# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/subcontractors — returns subcontractor list array.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'subcontractors' key.
5. 'subcontractors' value is a list (array).
6. list_subcontractors is called with the correct contract_id.
7. Response body contains 'total' key.
8. 'total' equals the length of 'subcontractors'.
9. Contract with no subcontractors returns 200 with empty list.
10. Optional business_size filter is forwarded to list_subcontractors.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"

_SUB_1 = {
    "id": "sub-test-0001",
    "contract_id": _CONTRACT_ID,
    "company_name": "Acme Small Business LLC",
    "cage_code": "1ABC2",
    "uei": "UEITEST0001",
    "business_size": "small",
    "subcontract_value": 150000.0,
    "status": "active",
}
_SUB_2 = {
    "id": "sub-test-0002",
    "contract_id": _CONTRACT_ID,
    "company_name": "Beta Services Inc",
    "cage_code": "2DEF3",
    "uei": "UEITEST0002",
    "business_size": "large",
    "subcontract_value": 500000.0,
    "status": "active",
}

_OK_RESULT = {"status": "ok", "total": 2, "subcontractors": [_SUB_1, _SUB_2]}
_EMPTY_RESULT = {"status": "ok", "total": 0, "subcontractors": []}
_SMALL_RESULT = {"status": "ok", "total": 1, "subcontractors": [_SUB_1]}


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


def _get(client, contract_id, **params):
    return client.get(
        f"/api/cpmp/contracts/{contract_id}/subcontractors",
        query_string=params or None,
    )


# ---------------------------------------------------------------------------
# Tests: successful subcontractor list retrieval
# ---------------------------------------------------------------------------


class TestListSubcontractorsReturnsArray:
    """GET returns 200 with a JSON body containing a subcontractors array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.subcontractor_tracker.list_subcontractors",
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

    def test_response_has_subcontractors_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "subcontractors" in data

    def test_subcontractors_is_a_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["subcontractors"], list)

    def test_list_subcontractors_called_with_correct_contract_id(self, api_app):
        _, mock_list = self._call(api_app)
        args, _ = mock_list.call_args
        assert args[0] == _CONTRACT_ID

    def test_response_has_total_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_total_equals_subcontractors_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["subcontractors"])


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestListSubcontractorsEdgeCases:
    """Edge cases: empty list and business_size filter."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_empty_contract_returns_200_with_empty_list(self, api_app):
        """Contract with no subcontractors must return 200 with an empty array."""
        with patch(
            "tools.govcon.subcontractor_tracker.list_subcontractors",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["subcontractors"] == []
        assert data["total"] == 0

    def test_business_size_filter_forwarded(self, api_app):
        """business_size query param is passed to list_subcontractors."""
        with patch(
            "tools.govcon.subcontractor_tracker.list_subcontractors",
            return_value=_SMALL_RESULT,
        ) as mock_list:
            with api_app.test_client() as c:
                _get(c, _CONTRACT_ID, business_size="small")
        _, kwargs = mock_list.call_args
        assert kwargs.get("business_size") == "small"
