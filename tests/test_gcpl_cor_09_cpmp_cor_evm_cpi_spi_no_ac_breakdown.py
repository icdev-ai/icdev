# CUI // SP-CTI
"""Tests for GET /api/cpmp/cor/contracts/<id>/evm — returns CPI/SPI without AC breakdown.

Verifies:
 1. Successful GET returns 200.
 2. Response is JSON.
 3. Response contains 'cpi' field.
 4. 'cpi' value is numeric (float or int).
 5. Response contains 'spi' field.
 6. 'spi' value is numeric (float or int).
 7. Response contains 'bac' field.
 8. Response contains 'pv' field.
 9. Response contains 'ev' field.
10. Response contains 'ac' field.
11. Response envelope 'status' value is 'ok'.
12. Response contains 'contract_id' field.
13. 'subcontractor_cost' is absent from response.
14. 'ac_cumulative' is absent from response.
15. 'internal_cost_details' is absent from response.
16. Raw JSON string does not contain the key '"subcontractor_cost"'.
17. Raw JSON string does not contain the key '"ac_cumulative"'.
18. Raw JSON string does not contain the key '"internal_cost_details"'.
19. Nested dict within EVM result also has hidden fields stripped.
20. COR not assigned to contract returns 403.
21. 403 response includes a 'message' field.
22. Authenticated user with no email returns 400.
"""
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-cor-test-0009"
_COR_EMAIL = "cor.officer@agency.mil"

_EVM_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": "FA8626-26-C-0009",
    "total_bac": 2_500_000.00,
    "pv": 1_200_000.00,
    "ev": 1_100_000.00,
    "ac": 1_180_000.00,
    "cpi": round(1_100_000.00 / 1_180_000.00, 4),
    "spi": round(1_100_000.00 / 1_200_000.00, 4),
    "cost_variance": -80_000.00,
    "schedule_variance": -100_000.00,
    "eac": round(2_500_000.00 / (1_100_000.00 / 1_180_000.00), 2),
    "etc": round(2_500_000.00 / (1_100_000.00 / 1_180_000.00) - 1_180_000.00, 2),
    "vac": round(2_500_000.00 - 2_500_000.00 / (1_100_000.00 / 1_180_000.00), 2),
    "tcpi": round((2_500_000.00 - 1_100_000.00) / (2_500_000.00 - 1_180_000.00), 4),
    "percent_complete_schedule": round(1_100_000.00 / 2_500_000.00, 4),
    "wbs_count": 5,
    # sensitive fields — must be stripped by _sanitize_for_cor
    "subcontractor_cost": 320_000.00,
    "ac_cumulative": 1_180_000.00,
    "internal_cost_details": {"labor": 860_000.00, "overhead": 320_000.00},
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

        g.current_user = {"email": cor_email, "role": role, "id": "user-cor-test-0009"}

    return flask_app


def _mock_db():
    conn = MagicMock()
    conn.execute.return_value = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(client, contract_id=_CONTRACT_ID):
    return client.get(f"/api/cpmp/cor/contracts/{contract_id}/evm")


def _call_success(api_app, evm_result=None):
    """Issue GET with mocks patched for a successful COR EVM access."""
    result = evm_result if evm_result is not None else _EVM_RESULT
    with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
        with patch("tools.govcon.evm_engine.aggregate_contract_evm", return_value=result):
            with patch("tools.dashboard.api.cpmp._get_db", return_value=_mock_db()):
                with api_app.test_client() as c:
                    resp = _get(c)
    return resp


# ---------------------------------------------------------------------------
# Tests: CPI and SPI are present
# ---------------------------------------------------------------------------


class TestCOREVMReturnsCPISPI:
    """GET /api/cpmp/cor/contracts/<id>/evm must expose CPI and SPI metrics."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_returns_200(self, api_app):
        resp = _call_success(api_app)
        assert resp.status_code == 200

    def test_response_is_json(self, api_app):
        resp = _call_success(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_contains_cpi(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "cpi" in data, "EVM response must include 'cpi'"

    def test_cpi_value_is_numeric(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert isinstance(data["cpi"], (int, float)), "'cpi' must be numeric"

    def test_response_contains_spi(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "spi" in data, "EVM response must include 'spi'"

    def test_spi_value_is_numeric(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert isinstance(data["spi"], (int, float)), "'spi' must be numeric"

    def test_response_contains_bac(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "total_bac" in data, "EVM response must include 'total_bac'"

    def test_response_contains_pv(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "pv" in data, "EVM response must include 'pv'"

    def test_response_contains_ev(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "ev" in data, "EVM response must include 'ev'"

    def test_response_contains_ac(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "ac" in data, "EVM response must include 'ac' (total actual cost)"


# ---------------------------------------------------------------------------
# Tests: response envelope shape
# ---------------------------------------------------------------------------


class TestCOREVMResponseEnvelope:
    """Response envelope must include status=ok and contract identifier."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_envelope_status_is_ok(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert data.get("status") == "ok"

    def test_envelope_has_contract_id(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "contract_id" in data, "EVM envelope must include 'contract_id'"

    def test_contract_id_matches_request(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert data["contract_id"] == _CONTRACT_ID


# ---------------------------------------------------------------------------
# Tests: sanitization of hidden fields
# ---------------------------------------------------------------------------


class TestCOREVMSanitization:
    """_sanitize_for_cor must strip COR_HIDDEN_FIELDS from EVM data."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_subcontractor_cost_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "subcontractor_cost" not in data, (
            "'subcontractor_cost' must be stripped from COR EVM response"
        )

    def test_ac_cumulative_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "ac_cumulative" not in data, (
            "'ac_cumulative' must be stripped from COR EVM response"
        )

    def test_internal_cost_details_absent(self, api_app):
        resp = _call_success(api_app)
        data = resp.get_json()
        assert "internal_cost_details" not in data, (
            "'internal_cost_details' must be stripped from COR EVM response"
        )

    def test_raw_json_lacks_subcontractor_cost_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"subcontractor_cost"' not in raw, (
            "Raw JSON must not contain the key '\"subcontractor_cost\"'"
        )

    def test_raw_json_lacks_ac_cumulative_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"ac_cumulative"' not in raw, (
            "Raw JSON must not contain the key '\"ac_cumulative\"'"
        )

    def test_raw_json_lacks_internal_cost_details_key(self, api_app):
        resp = _call_success(api_app)
        raw = resp.get_data(as_text=True)
        assert '"internal_cost_details"' not in raw, (
            "Raw JSON must not contain the key '\"internal_cost_details\"'"
        )

    def test_nested_hidden_fields_stripped(self, api_app):
        """_sanitize_for_cor must recursively strip hidden fields from nested dicts."""
        nested_result = dict(_EVM_RESULT)
        nested_result["wbs_breakdown"] = [
            {"wbs_id": "WBS-001", "ac_cumulative": 500_000.00, "subcontractor_cost": 100_000.00}
        ]
        resp = _call_success(api_app, evm_result=nested_result)
        data = resp.get_json()
        if "wbs_breakdown" in data:
            for wbs in data["wbs_breakdown"]:
                assert "ac_cumulative" not in wbs, (
                    "'ac_cumulative' must be stripped from nested wbs_breakdown items"
                )
                assert "subcontractor_cost" not in wbs, (
                    "'subcontractor_cost' must be stripped from nested wbs_breakdown items"
                )


# ---------------------------------------------------------------------------
# Tests: access control
# ---------------------------------------------------------------------------


class TestCOREVMAccessControl:
    """Access-control gates must fire before EVM data is fetched."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_unassigned_cor_returns_403(self, api_app):
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
        app_no_email = _build_cpmp_api_app(cor_email="")
        with patch("tools.dashboard.api.cpmp._get_cor_contracts", return_value=[{"id": _CONTRACT_ID}]):
            with app_no_email.test_client() as c:
                resp = _get(c)
        assert resp.status_code == 400
