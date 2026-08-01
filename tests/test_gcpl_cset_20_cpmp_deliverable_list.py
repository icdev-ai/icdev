# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/deliverables — returns deliverable list array.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'deliverables' key.
5. 'deliverables' value is a list (array).
6. list_deliverables is called with the correct contract_id.
7. Response body contains 'total' key.
8. 'total' equals the length of 'deliverables'.
9. Contract with no deliverables returns 200 with empty 'deliverables' list.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"

_DELIVERABLE_1 = {
    "id": "dlv-test-0001",
    "contract_id": _CONTRACT_ID,
    "cdrl_number": "A001",
    "did_number": "DI-MGMT-81466",
    "title": "Contract Performance Report",
    "type": "cdrl",
    "status": "not_started",
    "due_date": "2026-12-31",
}
_DELIVERABLE_2 = {
    "id": "dlv-test-0002",
    "contract_id": _CONTRACT_ID,
    "cdrl_number": "A002",
    "did_number": "DI-MGMT-81650",
    "title": "Integrated Master Schedule",
    "type": "cdrl",
    "status": "in_progress",
    "due_date": "2027-03-31",
}

_OK_RESULT = {"status": "ok", "total": 2, "deliverables": [_DELIVERABLE_1, _DELIVERABLE_2]}
_EMPTY_RESULT = {"status": "ok", "total": 0, "deliverables": []}


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
    return client.get(f"/api/cpmp/contracts/{contract_id}/deliverables")


# ---------------------------------------------------------------------------
# Tests: successful deliverable list retrieval
# ---------------------------------------------------------------------------


class TestListDeliverablesReturnsDeliverableArray:
    """GET returns 200 with a JSON body containing a deliverables array."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.contract_manager.list_deliverables",
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

    def test_response_has_deliverables_key(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "deliverables" in data

    def test_deliverables_is_a_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["deliverables"], list)

    def test_list_deliverables_called_with_correct_contract_id(self, api_app):
        _, mock_list = self._call(api_app)
        args, _ = mock_list.call_args
        assert args[0] == _CONTRACT_ID

    def test_response_has_total(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_total_equals_deliverables_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["deliverables"])


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestListDeliverablesEdgeCases:
    """Edge cases: contract with no deliverables."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_empty_contract_returns_200_with_empty_list(self, api_app):
        """Contract with no deliverables must return 200 with an empty deliverables array."""
        with patch(
            "tools.govcon.contract_manager.list_deliverables",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deliverables"] == []
        assert data["total"] == 0
