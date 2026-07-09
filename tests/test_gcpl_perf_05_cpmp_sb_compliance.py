# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/sb-compliance — FAR 52.219-9 compliance status.

Verifies:
1. Successful GET returns 200.
2. Response is JSON.
3. Response body contains status == 'ok'.
4. Response body contains 'contract_id' key.
5. Response body contains 'overall_compliant' key.
6. Response body contains 'categories' key.
7. Response body contains 'total_subcontract_dollars' key.
8. compute_sb_compliance is called with the correct contract_id.
9. No active subcontractors — returns 200 with overall_compliant == False.
10. No active subcontractors — response contains 'message' key.
11. Backend exception — returns 500 with error status.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-0001"
_EMPTY_CONTRACT_ID = "ctr-no-subs"

_COMPLIANCE_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "total_subcontract_dollars": 1_000_000.0,
    "categories": {
        "sb": {"goal_pct": 23.0, "actual_pct": 25.0, "actual_dollars": 250_000.0, "met": True, "gap_pct": 0.0},
        "sdb": {"goal_pct": 5.0, "actual_pct": 6.0, "actual_dollars": 60_000.0, "met": True, "gap_pct": 0.0},
        "wosb": {"goal_pct": 5.0, "actual_pct": 5.0, "actual_dollars": 50_000.0, "met": True, "gap_pct": 0.0},
        "hubzone": {"goal_pct": 3.0, "actual_pct": 3.5, "actual_dollars": 35_000.0, "met": True, "gap_pct": 0.0},
        "sdvosb": {"goal_pct": 3.0, "actual_pct": 3.0, "actual_dollars": 30_000.0, "met": True, "gap_pct": 0.0},
    },
    "overall_compliant": True,
    "has_goals": True,
}

_EMPTY_RESULT = {
    "status": "ok",
    "contract_id": _EMPTY_CONTRACT_ID,
    "total_subcontract_dollars": 0.0,
    "categories": {},
    "overall_compliant": False,
    "message": "No active subcontractors found",
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


def _get(client, contract_id):
    return client.get(f"/api/cpmp/contracts/{contract_id}/sb-compliance")


# ---------------------------------------------------------------------------
# Tests: successful compliance check
# ---------------------------------------------------------------------------


class TestGetSbComplianceSuccess:
    """GET with a valid contract_id returns 200 with FAR 52.219-9 compliance data."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, contract_id=_CONTRACT_ID):
        with patch(
            "tools.govcon.subcontractor_tracker.compute_sb_compliance",
            return_value=_COMPLIANCE_RESULT,
        ) as mock_compute:
            with api_app.test_client() as c:
                resp = _get(c, contract_id)
        return resp, mock_compute

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

    def test_response_has_contract_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "contract_id" in data

    def test_response_has_overall_compliant(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "overall_compliant" in data

    def test_response_has_categories(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "categories" in data

    def test_response_has_total_subcontract_dollars(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_subcontract_dollars" in data

    def test_compute_called_with_correct_contract_id(self, api_app):
        _, mock_compute = self._call(api_app)
        args, _ = mock_compute.call_args
        assert args[0] == _CONTRACT_ID


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------


class TestGetSbComplianceEdgeCases:
    """Edge cases: no active subcontractors and backend exceptions."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_no_active_subcontractors_returns_200(self, api_app):
        """Contract with no active subcontractors must still return 200."""
        with patch(
            "tools.govcon.subcontractor_tracker.compute_sb_compliance",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _EMPTY_CONTRACT_ID)
        assert resp.status_code == 200

    def test_no_active_subcontractors_overall_compliant_false(self, api_app):
        """No subcontractors means overall_compliant must be False."""
        with patch(
            "tools.govcon.subcontractor_tracker.compute_sb_compliance",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _EMPTY_CONTRACT_ID)
        data = resp.get_json()
        assert data["overall_compliant"] is False

    def test_no_active_subcontractors_has_message(self, api_app):
        """Empty-contract response must include a 'message' field."""
        with patch(
            "tools.govcon.subcontractor_tracker.compute_sb_compliance",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _EMPTY_CONTRACT_ID)
        data = resp.get_json()
        assert "message" in data

    def test_backend_exception_returns_500(self, api_app):
        """Unhandled backend exception must return 500 with error status."""
        with patch(
            "tools.govcon.subcontractor_tracker.compute_sb_compliance",
            side_effect=RuntimeError("DB unavailable"),
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data.get("status") == "error"
