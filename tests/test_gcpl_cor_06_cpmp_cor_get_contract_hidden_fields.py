# CUI // SP-CTI
"""Tests for GET /api/cpmp/cor/contracts/<id> — COR API hides internal_cost_details.

Verifies:
 1. Successful GET returns 200.
 2. Response is JSON.
 3. internal_cost_details is absent from the response body.
 4. subcontractor_pricing is absent from the response body.
 5. internal_notes is absent from the response body.
 6. corrective_action_details is absent from the response body.
 7. billed_value is absent from the response body.
 8. ac_cumulative is absent from the response body.
 9. id field is present in the response (not stripped by sanitizer).
10. contract_number is present in the response.
11. title is present in the response.
12. agency is present in the response.
13. total_value is present in the response.
14. Nested internal_cost_details inside a sub-dict is also stripped.
15. internal_cost_details inside a list item is also stripped.
16. COR not assigned to contract returns 403.
17. Authenticated user with no email returns 400.
18. Contract not found from engine returns 404.
19. Response body JSON string does not contain the key "internal_cost_details".
20. Response body JSON string does not contain the key "subcontractor_pricing".
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-cor-test-0006"
_COR_EMAIL = "cor.officer@agency.mil"

_CONTRACT_FULL = {
    "status": "ok",
    "id": _CONTRACT_ID,
    "contract_number": "USAF-2026-COR-006",
    "title": "COR Security Test Contract",
    "agency": "USAF",
    "contract_status": "active",
    "total_value": 2_500_000.0,
    "pop_end": "2027-09-30",
    # --- sensitive fields that must be stripped ---
    "internal_cost_details": {"labor_rate": 95.0, "overhead_multiplier": 0.35},
    "subcontractor_pricing": {"sub_a": 100_000.0, "sub_b": 250_000.0},
    "internal_notes": "PM eyes-only: vendor performance concerns Q3",
    "corrective_action_details": "CAR-2026-001 open, due 2026-08-01",
    "billed_value": 750_000.0,
    "ac_cumulative": 800_000.0,
}

# Contract where internal_cost_details appears nested and in a list
_CONTRACT_NESTED = {
    "status": "ok",
    "id": _CONTRACT_ID,
    "contract_number": "USAF-2026-COR-006",
    "title": "Nested Test",
    "agency": "USAF",
    "total_value": 1_000_000.0,
    "cost_breakdown": {
        "internal_cost_details": {"labor": 400_000.0},
        "approved": 600_000.0,
    },
    "clins": [
        {
            "clin_id": "clin-001",
            "description": "Base CLIN",
            "internal_cost_details": {"rate": 120.0},
            "funded_value": 500_000.0,
        }
    ],
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_CONTRACT_ID} not found",
}


# ---------------------------------------------------------------------------
# Flask test app
# ---------------------------------------------------------------------------


def _build_cpmp_api_app(cor_email=_COR_EMAIL, role="cor") -> Flask:
    from tools.dashboard.api.cpmp import cpmp_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(cpmp_api)

    @flask_app.before_request
    def _inject_user():
        from flask import g

        g.current_user = {"email": cor_email, "role": role, "id": "user-cor-test-0006"}

    return flask_app


def _mock_db():
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get(client, contract_id=_CONTRACT_ID):
    return client.get(f"/api/cpmp/cor/contracts/{contract_id}")


def _call_success(api_app, contract_data=None):
    """Issue GET with mocks patched for a successful COR access."""
    data = contract_data if contract_data is not None else _CONTRACT_FULL
    with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
        with patch("tools.govcon.contract_manager.get_contract", return_value=data):
            with patch("tools.dashboard.api.cpmp._get_db", return_value=_mock_db()):
                with api_app.test_client() as c:
                    resp = _get(c)
    return resp


# ---------------------------------------------------------------------------
# Tests: hidden fields are stripped from COR response
# ---------------------------------------------------------------------------


class TestCORGetContractHiddenFields:
    """COR_HIDDEN_FIELDS must be absent from GET /api/cpmp/cor/contracts/<id> response."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_returns_200(self, api_app):
        resp = _call_success(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = _call_success(api_app)
        assert resp.content_type.startswith("application/json")

    def test_internal_cost_details_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "internal_cost_details" not in data, (
            "internal_cost_details must be stripped from the COR API response"
        )

    def test_subcontractor_pricing_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "subcontractor_pricing" not in data, (
            "subcontractor_pricing must be stripped from the COR API response"
        )

    def test_internal_notes_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "internal_notes" not in data, (
            "internal_notes must be stripped from the COR API response"
        )

    def test_corrective_action_details_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "corrective_action_details" not in data, (
            "corrective_action_details must be stripped from the COR API response"
        )

    def test_billed_value_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "billed_value" not in data, (
            "billed_value must be stripped from the COR API response"
        )

    def test_ac_cumulative_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "ac_cumulative" not in data, (
            "ac_cumulative must be stripped from the COR API response"
        )


# ---------------------------------------------------------------------------
# Tests: permitted fields remain in COR response
# ---------------------------------------------------------------------------


class TestCORGetContractAllowedFields:
    """Non-sensitive fields must survive _sanitize_for_cor and appear in response."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_id_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "id" in data, "id must remain in the COR API response"
        assert data["id"] == _CONTRACT_ID

    def test_contract_number_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "contract_number" in data, "contract_number must remain in the COR API response"
        assert data["contract_number"] == "USAF-2026-COR-006"

    def test_title_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "title" in data, "title must remain in the COR API response"

    def test_agency_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "agency" in data, "agency must remain in the COR API response"
        assert data["agency"] == "USAF"

    def test_total_value_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "total_value" in data, "total_value must remain in the COR API response"
        assert data["total_value"] == pytest.approx(2_500_000.0)


# ---------------------------------------------------------------------------
# Tests: nested and list sanitization
# ---------------------------------------------------------------------------


class TestCORGetContractNestedSanitization:
    """_sanitize_for_cor must recursively strip hidden fields from nested dicts and lists."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_nested_dict_internal_cost_details_absent(self, api_app):
        resp = _call_success(api_app, contract_data=_CONTRACT_NESTED)
        data = resp.get_json()
        cost_breakdown = data.get("cost_breakdown", {})
        assert "internal_cost_details" not in cost_breakdown, (
            "internal_cost_details nested inside cost_breakdown must be stripped"
        )

    def test_list_item_internal_cost_details_absent(self, api_app):
        resp = _call_success(api_app, contract_data=_CONTRACT_NESTED)
        data = resp.get_json()
        clins = data.get("clins", [])
        assert len(clins) == 1
        assert "internal_cost_details" not in clins[0], (
            "internal_cost_details inside a list item must be stripped"
        )

    def test_response_json_string_lacks_internal_cost_details_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"internal_cost_details"' not in raw, (
            "The raw JSON response string must not contain the key 'internal_cost_details'"
        )

    def test_response_json_string_lacks_subcontractor_pricing_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"subcontractor_pricing"' not in raw, (
            "The raw JSON response string must not contain the key 'subcontractor_pricing'"
        )


# ---------------------------------------------------------------------------
# Tests: access control
# ---------------------------------------------------------------------------


class TestCORGetContractAccessControl:
    """Access-control gates must fire before _sanitize_for_cor is reached."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_unassigned_cor_returns_403(self, api_app):
        """COR whose email is not on the contract must get 403 Forbidden."""
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[]):
            with api_app.test_client() as c:
                resp = _get(c)
        assert resp.status_code == 403

    def test_unassigned_cor_response_has_message(self, api_app):
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[]):
            with api_app.test_client() as c:
                resp = _get(c)
        data = resp.get_json()
        assert "message" in data

    def test_no_email_returns_400(self):
        """Authenticated user with empty email must get 400."""
        app_no_email = _build_cpmp_api_app(cor_email="")
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
            with app_no_email.test_client() as c:
                resp = _get(c)
        assert resp.status_code == 400

    def test_contract_not_found_returns_404(self, api_app):
        """Engine returning status=error must propagate as 404."""
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
            with patch("tools.govcon.contract_manager.get_contract", return_value=_NOT_FOUND_RESULT):
                with patch("tools.dashboard.api.cpmp._get_db", return_value=_mock_db()):
                    with api_app.test_client() as c:
                        resp = _get(c)
        assert resp.status_code == 404

    def test_contract_not_found_response_has_message(self, api_app):
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
            with patch("tools.govcon.contract_manager.get_contract", return_value=_NOT_FOUND_RESULT):
                with patch("tools.dashboard.api.cpmp._get_db", return_value=_mock_db()):
                    with api_app.test_client() as c:
                        resp = _get(c)
        data = resp.get_json()
        assert "message" in data
