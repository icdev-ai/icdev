# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/evm/scurve — ordered date/PV/EV/AC arrays.

Verifies:
 1. Successful call returns 200.
 2. Response is JSON.
 3. Response body contains status == 'ok'.
 4. Response body contains 'contract_id'.
 5. Response body contains 'contract_number'.
 6. Response body contains 'total_periods'.
 7. Response body contains 's_curve' list.
 8. Each s_curve entry contains 'period_date'.
 9. Each s_curve entry contains 'pv_cumulative'.
10. Each s_curve entry contains 'ev_cumulative'.
11. Each s_curve entry contains 'ac_cumulative'.
12. period_date values are in ascending (chronological) order.
13. total_periods matches len(s_curve).
14. generate_scurve_data is called with the correct contract_id.
15. Contract-not-found result returns 200 with status == 'error'.
16. Contract-not-found response contains 'message'.
17. Empty s_curve list when no EVM periods exist (total_periods == 0).
18. pv_cumulative values are non-negative for all entries.
19. ev_cumulative values are non-negative for all entries.
20. ac_cumulative values are non-negative for all entries.
21. All three periods are present in the s_curve response.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-evm-0008"
_CONTRACT_NUMBER = "W52P1J-26-C-0008"

# Three monthly periods in ascending chronological order:
# period 1: 2026-01  period 2: 2026-02  period 3: 2026-03
_SCURVE_PERIODS = [
    {"period_date": "2026-01", "pv_cumulative": 100_000.0, "ev_cumulative": 90_000.0, "ac_cumulative": 92_000.0},
    {"period_date": "2026-02", "pv_cumulative": 200_000.0, "ev_cumulative": 185_000.0, "ac_cumulative": 188_000.0},
    {"period_date": "2026-03", "pv_cumulative": 310_000.0, "ev_cumulative": 290_000.0, "ac_cumulative": 295_000.0},
]

_OK_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "total_periods": len(_SCURVE_PERIODS),
    "s_curve": _SCURVE_PERIODS,
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_CONTRACT_ID} not found",
}

_EMPTY_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "total_periods": 0,
    "s_curve": [],
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


def _get_scurve(client, contract_id):
    return client.get(f"/api/cpmp/contracts/{contract_id}/evm/scurve")


# ---------------------------------------------------------------------------
# Tests: successful S-curve with three ordered periods
# ---------------------------------------------------------------------------


class TestGetEvmScurveOrdering:
    """GET /api/cpmp/contracts/<id>/evm/scurve must return 200 with ordered date/PV/EV/AC arrays."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, mock_return=None):
        ret = mock_return if mock_return is not None else _OK_RESULT
        with patch(
            "tools.govcon.evm_engine.generate_scurve_data",
            return_value=ret,
        ) as mock_sc:
            with api_app.test_client() as c:
                resp = _get_scurve(c, _CONTRACT_ID)
        return resp, mock_sc

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

    def test_response_has_contract_number(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "contract_number" in data

    def test_response_has_total_periods(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_periods" in data

    def test_response_has_s_curve_list(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "s_curve" in data
        assert isinstance(data["s_curve"], list)

    def test_s_curve_entries_have_period_date(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["s_curve"]:
            assert "period_date" in entry

    def test_s_curve_entries_have_pv_cumulative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["s_curve"]:
            assert "pv_cumulative" in entry

    def test_s_curve_entries_have_ev_cumulative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["s_curve"]:
            assert "ev_cumulative" in entry

    def test_s_curve_entries_have_ac_cumulative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["s_curve"]:
            assert "ac_cumulative" in entry

    def test_period_dates_are_in_ascending_order(self, api_app):
        """period_date values must be sorted ASC — S-curve is a forward time series."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        dates = [e["period_date"] for e in data["s_curve"]]
        assert dates == sorted(dates)

    def test_total_periods_matches_s_curve_length(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["total_periods"] == len(data["s_curve"])

    def test_generate_scurve_called_with_correct_contract_id(self, api_app):
        _, mock_sc = self._call(api_app)
        args, _ = mock_sc.call_args
        assert args[0] == _CONTRACT_ID

    def test_pv_cumulative_values_are_non_negative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["s_curve"]:
            assert entry["pv_cumulative"] >= 0

    def test_ev_cumulative_values_are_non_negative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["s_curve"]:
            assert entry["ev_cumulative"] >= 0

    def test_ac_cumulative_values_are_non_negative(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        for entry in data["s_curve"]:
            assert entry["ac_cumulative"] >= 0

    def test_all_three_periods_present_in_response(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert len(data["s_curve"]) == 3


# ---------------------------------------------------------------------------
# Tests: contract not found
# ---------------------------------------------------------------------------


class TestGetEvmScurveContractNotFound:
    """Engine returns error status for unknown contract — endpoint passes it through as 200."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_not_found_returns_200(self, api_app):
        with patch(
            "tools.govcon.evm_engine.generate_scurve_data",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get_scurve(c, _CONTRACT_ID)
        assert resp.status_code == 200

    def test_contract_not_found_status_is_error(self, api_app):
        with patch(
            "tools.govcon.evm_engine.generate_scurve_data",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get_scurve(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["status"] == "error"

    def test_contract_not_found_response_has_message(self, api_app):
        with patch(
            "tools.govcon.evm_engine.generate_scurve_data",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get_scurve(c, _CONTRACT_ID)
        data = resp.get_json()
        assert "message" in data


# ---------------------------------------------------------------------------
# Tests: no EVM period data
# ---------------------------------------------------------------------------


class TestGetEvmScurveEmptyData:
    """Contract exists but has no EVM period records — returns empty s_curve list."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_empty_s_curve_returns_200(self, api_app):
        with patch(
            "tools.govcon.evm_engine.generate_scurve_data",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get_scurve(c, _CONTRACT_ID)
        assert resp.status_code == 200

    def test_empty_s_curve_total_periods_is_zero(self, api_app):
        with patch(
            "tools.govcon.evm_engine.generate_scurve_data",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get_scurve(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["total_periods"] == 0

    def test_empty_s_curve_list_is_empty(self, api_app):
        with patch(
            "tools.govcon.evm_engine.generate_scurve_data",
            return_value=_EMPTY_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get_scurve(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["s_curve"] == []
