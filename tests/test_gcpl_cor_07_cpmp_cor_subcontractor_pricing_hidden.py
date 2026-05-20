# CUI // SP-CTI
"""Tests for GET /api/cpmp/cor/contracts/<id> — COR API hides subcontractor_pricing/rate/cost.

Verifies:
 1. Successful GET returns 200.
 2. Response is JSON.
 3. subcontractor_pricing is absent from the response body.
 4. subcontractor_rate is absent from the response body.
 5. subcontractor_cost is absent from the response body.
 6. Nested subcontractor_pricing inside a sub-dict is also stripped.
 7. subcontractor_rate inside a list item is also stripped.
 8. subcontractor_cost inside a list item is also stripped.
 9. Raw JSON string does not contain the key "subcontractor_pricing".
10. Raw JSON string does not contain the key "subcontractor_rate".
11. Raw JSON string does not contain the key "subcontractor_cost".
12. id field is present in the response (not stripped by sanitizer).
13. contract_number is present in the response.
14. title is present in the response.
15. agency is present in the response.
16. total_value is present in the response.
17. COR_HIDDEN_FIELDS constant includes subcontractor_pricing.
18. COR_HIDDEN_FIELDS constant includes subcontractor_rate.
19. COR_HIDDEN_FIELDS constant includes subcontractor_cost.
20. COR not assigned to contract returns 403.
21. Authenticated user with no email returns 400.
22. Contract not found from engine returns 404.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-cor-test-0007"
_COR_EMAIL = "cor.officer@agency.mil"

_CONTRACT_FULL = {
    "status": "ok",
    "id": _CONTRACT_ID,
    "contract_number": "USAF-2026-COR-007",
    "title": "COR Subcontractor Security Test Contract",
    "agency": "USAF",
    "contract_status": "active",
    "total_value": 3_750_000.0,
    "pop_end": "2027-12-31",
    # --- subcontractor-sensitive fields that must be stripped ---
    "subcontractor_pricing": {"sub_a": 120_000.0, "sub_b": 300_000.0},
    "subcontractor_rate": {"sub_a_hourly": 85.0, "sub_b_daily": 680.0},
    "subcontractor_cost": {"sub_a_ytd": 60_000.0, "sub_b_ytd": 150_000.0},
    # --- other internal fields also stripped (from COR_HIDDEN_FIELDS) ---
    "internal_cost_details": {"labor_rate": 95.0, "overhead_multiplier": 0.35},
    "internal_notes": "PM eyes-only: vendor performance concerns Q3",
}

# Contract where subcontractor fields appear nested and in lists
_CONTRACT_NESTED = {
    "status": "ok",
    "id": _CONTRACT_ID,
    "contract_number": "USAF-2026-COR-007",
    "title": "Nested Subcontractor Test",
    "agency": "USAF",
    "total_value": 2_000_000.0,
    "cost_breakdown": {
        "subcontractor_pricing": {"sub_a": 80_000.0},
        "approved": 1_920_000.0,
    },
    "clins": [
        {
            "clin_id": "clin-001",
            "description": "Base CLIN",
            "subcontractor_rate": {"hourly": 90.0},
            "subcontractor_cost": {"ytd": 45_000.0},
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

        g.current_user = {"email": cor_email, "role": role, "id": "user-cor-test-0007"}

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
# Tests: subcontractor fields stripped from COR response
# ---------------------------------------------------------------------------


class TestCORSubcontractorFieldsHidden:
    """subcontractor_pricing, subcontractor_rate, and subcontractor_cost must be
    absent from GET /api/cpmp/cor/contracts/<id> response."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_returns_200(self, api_app):
        resp = _call_success(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = _call_success(api_app)
        assert resp.content_type.startswith("application/json")

    def test_subcontractor_pricing_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "subcontractor_pricing" not in data, (
            "subcontractor_pricing must be stripped from the COR API response"
        )

    def test_subcontractor_rate_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "subcontractor_rate" not in data, (
            "subcontractor_rate must be stripped from the COR API response"
        )

    def test_subcontractor_cost_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "subcontractor_cost" not in data, (
            "subcontractor_cost must be stripped from the COR API response"
        )


# ---------------------------------------------------------------------------
# Tests: permitted fields remain in COR response
# ---------------------------------------------------------------------------


class TestCORSubcontractorAllowedFieldsIntact:
    """Non-sensitive fields must survive _sanitize_for_cor and appear in response."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_id_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "id" in data
        assert data["id"] == _CONTRACT_ID

    def test_contract_number_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "contract_number" in data
        assert data["contract_number"] == "USAF-2026-COR-007"

    def test_title_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "title" in data

    def test_agency_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "agency" in data
        assert data["agency"] == "USAF"

    def test_total_value_present(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "total_value" in data
        assert data["total_value"] == pytest.approx(3_750_000.0)


# ---------------------------------------------------------------------------
# Tests: nested and list sanitization of subcontractor fields
# ---------------------------------------------------------------------------


class TestCORSubcontractorNestedSanitization:
    """_sanitize_for_cor must recursively strip subcontractor fields from
    nested dicts and list items."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_nested_dict_subcontractor_pricing_absent(self, api_app):
        resp = _call_success(api_app, contract_data=_CONTRACT_NESTED)
        data = resp.get_json()
        cost_breakdown = data.get("cost_breakdown", {})
        assert "subcontractor_pricing" not in cost_breakdown, (
            "subcontractor_pricing nested inside cost_breakdown must be stripped"
        )

    def test_list_item_subcontractor_rate_absent(self, api_app):
        resp = _call_success(api_app, contract_data=_CONTRACT_NESTED)
        data = resp.get_json()
        clins = data.get("clins", [])
        assert len(clins) == 1
        assert "subcontractor_rate" not in clins[0], (
            "subcontractor_rate inside a list item must be stripped"
        )

    def test_list_item_subcontractor_cost_absent(self, api_app):
        resp = _call_success(api_app, contract_data=_CONTRACT_NESTED)
        data = resp.get_json()
        clins = data.get("clins", [])
        assert len(clins) == 1
        assert "subcontractor_cost" not in clins[0], (
            "subcontractor_cost inside a list item must be stripped"
        )

    def test_raw_json_lacks_subcontractor_pricing_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"subcontractor_pricing"' not in raw, (
            "The raw JSON response string must not contain the key 'subcontractor_pricing'"
        )

    def test_raw_json_lacks_subcontractor_rate_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"subcontractor_rate"' not in raw, (
            "The raw JSON response string must not contain the key 'subcontractor_rate'"
        )

    def test_raw_json_lacks_subcontractor_cost_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"subcontractor_cost"' not in raw, (
            "The raw JSON response string must not contain the key 'subcontractor_cost'"
        )


# ---------------------------------------------------------------------------
# Tests: COR_HIDDEN_FIELDS constant completeness
# ---------------------------------------------------------------------------


class TestCORHiddenFieldsConstant:
    """COR_HIDDEN_FIELDS must enumerate all three subcontractor field names."""

    def test_hidden_fields_includes_subcontractor_pricing(self):
        from tools.dashboard.api.cpmp import COR_HIDDEN_FIELDS

        assert "subcontractor_pricing" in COR_HIDDEN_FIELDS, (
            "COR_HIDDEN_FIELDS must include 'subcontractor_pricing'"
        )

    def test_hidden_fields_includes_subcontractor_rate(self):
        from tools.dashboard.api.cpmp import COR_HIDDEN_FIELDS

        assert "subcontractor_rate" in COR_HIDDEN_FIELDS, (
            "COR_HIDDEN_FIELDS must include 'subcontractor_rate'"
        )

    def test_hidden_fields_includes_subcontractor_cost(self):
        from tools.dashboard.api.cpmp import COR_HIDDEN_FIELDS

        assert "subcontractor_cost" in COR_HIDDEN_FIELDS, (
            "COR_HIDDEN_FIELDS must include 'subcontractor_cost'"
        )


# ---------------------------------------------------------------------------
# Tests: access control
# ---------------------------------------------------------------------------


class TestCORSubcontractorAccessControl:
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
