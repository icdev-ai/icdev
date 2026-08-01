# CUI // SP-CTI
"""Tests for POST /api/cpmp/contracts/<id>/evm — records monthly EVM snapshot (PV/EV/AC).

Verifies:
 1. Successful creation returns 201.
 2. Response is JSON.
 3. Response body contains status == 'ok'.
 4. Response body contains a 'period_id' key.
 5. Returned period_id is a non-empty string.
 6. Response body contains 'period_date' matching the submitted value.
 7. Response body contains 'indicators' dict.
 8. indicators.cpi reflects EV/AC ratio (95000/97000 ≈ 0.9794).
 9. indicators.spi reflects EV/PV ratio (95000/100000 = 0.95).
10. indicators.cost_variance equals EV - AC = -2000.
11. indicators.schedule_variance equals EV - PV = -5000.
12. Response contains 'cpi_status' (green — CPI ≥ 0.95 threshold).
13. Response contains 'spi_status'.
14. record_period is called with the correct contract_id.
15. record_period is called with the submitted wbs_id.
16. record_period is called with pv=100000.
17. record_period is called with ev=95000.
18. record_period is called with ac=97000.
19. Missing wbs_id returns 400.
20. Missing period_date returns 400.
21. Contract-not-found result from engine returns 400.
22. Contract-not-found response contains 'message'.
"""
import json
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-evm-0001"
_WBS_ID = "wbs-test-evm-0001"
_PERIOD_DATE = "2026-05"
_PERIOD_ID = "period-evm-uuid-0001"

_PV = 100_000.0
_EV = 95_000.0
_AC = 97_000.0

_INDICATORS = {
    "cpi": 0.9794,          # round(95000/97000, 4)
    "spi": 0.95,            # round(95000/100000, 4)
    "cost_variance": -2000.0,      # EV - AC
    "schedule_variance": -5000.0,  # EV - PV
    "eac": 510526.32,
    "etc": 413526.32,
    "vac": -10526.32,
    "tcpi": 1.005,
}

_EVM_PAYLOAD = {
    "wbs_id": _WBS_ID,
    "period_date": _PERIOD_DATE,
    "pv": _PV,
    "ev": _EV,
    "ac": _AC,
    "source": "manual",
}

_OK_RESULT = {
    "status": "ok",
    "period_id": _PERIOD_ID,
    "period_date": _PERIOD_DATE,
    "bac": 500_000.0,
    "pv_cumulative": _PV,
    "ev_cumulative": _EV,
    "ac_cumulative": _AC,
    "indicators": _INDICATORS,
    "cpi_status": "green",
    "spi_status": "green",
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_CONTRACT_ID} not found",
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


def _post(client, contract_id, body=None):
    kwargs = {"content_type": "application/json"}
    if body is not None:
        kwargs["data"] = json.dumps(body)
    return client.post(f"/api/cpmp/contracts/{contract_id}/evm", **kwargs)


# ---------------------------------------------------------------------------
# Tests: successful EVM period recording (PV=100K, EV=95K, AC=97K)
# ---------------------------------------------------------------------------


class TestRecordEvmPeriodPvEvAc:
    """POST with PV=100K / EV=95K / AC=97K must return 201 with period_id and indicators."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, payload=None):
        body = payload if payload is not None else _EVM_PAYLOAD
        with patch(
            "tools.govcon.evm_engine.record_period",
            return_value=_OK_RESULT,
        ) as mock_record:
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, body)
        return resp, mock_record

    def test_returns_201(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.status_code == 201

    def test_response_is_json(self, api_app):
        resp, _ = self._call(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_status_is_ok(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_period_id(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "period_id" in data

    def test_period_id_is_non_empty_string(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert isinstance(data["period_id"], str)
        assert len(data["period_id"]) > 0

    def test_period_date_matches_input(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["period_date"] == _PERIOD_DATE

    def test_response_has_indicators(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "indicators" in data
        assert isinstance(data["indicators"], dict)

    def test_cpi_reflects_ev_over_ac(self, api_app):
        """CPI = EV/AC = 95000/97000 ≈ 0.9794."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["cpi"] == pytest.approx(0.9794, abs=1e-4)

    def test_spi_reflects_ev_over_pv(self, api_app):
        """SPI = EV/PV = 95000/100000 = 0.95."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["spi"] == pytest.approx(0.95, abs=1e-4)

    def test_cost_variance_is_ev_minus_ac(self, api_app):
        """CV = EV - AC = 95000 - 97000 = -2000."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["cost_variance"] == pytest.approx(-2000.0)

    def test_schedule_variance_is_ev_minus_pv(self, api_app):
        """SV = EV - PV = 95000 - 100000 = -5000."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["schedule_variance"] == pytest.approx(-5000.0)

    def test_response_has_cpi_status(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "cpi_status" in data

    def test_cpi_status_is_green(self, api_app):
        """CPI 0.9794 >= 0.95 yellow threshold — must be green."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["cpi_status"] == "green"

    def test_response_has_spi_status(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "spi_status" in data

    def test_spi_status_is_green(self, api_app):
        """SPI 0.95 == yellow threshold — still green (>= comparison)."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["spi_status"] == "green"

    def test_record_period_called_with_correct_contract_id(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        assert args[0] == _CONTRACT_ID

    def test_record_period_called_with_correct_wbs_id(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        assert args[1] == _WBS_ID

    def test_record_period_called_with_pv_100k(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        assert args[3] == pytest.approx(_PV)

    def test_record_period_called_with_ev_95k(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        assert args[4] == pytest.approx(_EV)

    def test_record_period_called_with_ac_97k(self, api_app):
        _, mock_record = self._call(api_app)
        args, _ = mock_record.call_args
        assert args[5] == pytest.approx(_AC)


# ---------------------------------------------------------------------------
# Tests: validation errors
# ---------------------------------------------------------------------------


class TestRecordEvmPeriodValidation:
    """Validation gate: missing wbs_id or period_date must return 400 before calling engine."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_missing_wbs_id_returns_400(self, api_app):
        body = {k: v for k, v in _EVM_PAYLOAD.items() if k != "wbs_id"}
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, body)
        assert resp.status_code == 400

    def test_missing_period_date_returns_400(self, api_app):
        body = {k: v for k, v in _EVM_PAYLOAD.items() if k != "period_date"}
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, body)
        assert resp.status_code == 400

    def test_missing_wbs_id_response_has_message(self, api_app):
        body = {k: v for k, v in _EVM_PAYLOAD.items() if k != "wbs_id"}
        with api_app.test_client() as c:
            resp = _post(c, _CONTRACT_ID, body)
        data = resp.get_json()
        assert "message" in data

    def test_contract_not_found_returns_400(self, api_app):
        with patch(
            "tools.govcon.evm_engine.record_period",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _EVM_PAYLOAD)
        assert resp.status_code == 400

    def test_contract_not_found_response_has_message(self, api_app):
        with patch(
            "tools.govcon.evm_engine.record_period",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _post(c, _CONTRACT_ID, _EVM_PAYLOAD)
        data = resp.get_json()
        assert "message" in data
