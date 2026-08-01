# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/evm — aggregate ANSI/EIA-748 EVM metrics.

Verifies:
 1. Successful aggregation returns 200.
 2. Response is JSON.
 3. Response body contains status == 'ok'.
 4. Response body contains 'contract_id'.
 5. Response body contains 'total_bac'.
 6. Response body contains 'total_pv'.
 7. Response body contains 'total_ev'.
 8. Response body contains 'total_ac'.
 9. Response body contains 'percent_complete'.
10. Response body contains 'indicators' dict.
11. indicators.cpi reflects EV/AC ratio (380000/395000 ≈ 0.962).
12. indicators.spi reflects EV/PV ratio (380000/400000 = 0.95).
13. indicators.cost_variance equals EV - AC = -15000.0.
14. indicators.schedule_variance equals EV - PV = -20000.0.
15. Response contains 'cpi_status'.
16. Response contains 'spi_status'.
17. cpi_status is 'green' when CPI >= 0.95 threshold.
18. spi_status is 'green' when SPI >= 0.95 threshold.
19. Response contains 'wbs_count'.
20. aggregate_contract_evm is called with the correct contract_id.
21. Contract-not-found result returns 200 with status == 'error'.
22. Contract-not-found response contains 'message'.
23. No EVM data result returns 200 with message 'No EVM period data found'.
24. Yellow CPI (0.85 <= CPI < 0.95) returns cpi_status == 'yellow'.
25. Red SPI (SPI < 0.85) returns spi_status == 'red'.
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-evm-0001"
_CONTRACT_NUMBER = "W52P1J-26-C-0001"
_CONTRACT_TITLE = "Logistics Support Services"

_TOTAL_BAC = 500_000.0
_TOTAL_PV   = 400_000.0
_TOTAL_EV   = 380_000.0
_TOTAL_AC   = 395_000.0

# Derived indicators for BAC=500K, PV=400K, EV=380K, AC=395K
_INDICATORS = {
    "cpi": 0.962,           # round(380000/395000, 4)
    "spi": 0.95,            # round(380000/400000, 4)
    "cost_variance": -15_000.0,      # EV - AC
    "schedule_variance": -20_000.0,  # EV - PV
    "eac": 519_736.84,      # BAC / CPI = 500000*395000/380000
    "etc": 124_736.84,      # EAC - AC
    "vac": -19_736.84,      # BAC - EAC
    "tcpi": 1.1429,         # (BAC-EV)/(BAC-AC) = 120000/105000
}

_OK_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "title": _CONTRACT_TITLE,
    "total_bac": _TOTAL_BAC,
    "total_pv": _TOTAL_PV,
    "total_ev": _TOTAL_EV,
    "total_ac": _TOTAL_AC,
    "percent_complete": 76.0,   # 380000/500000 * 100
    "indicators": _INDICATORS,
    "cpi_status": "green",      # CPI 0.962 >= 0.95 threshold
    "spi_status": "green",      # SPI 0.95 >= 0.95 threshold
    "wbs_count": 2,
}

_NOT_FOUND_RESULT = {
    "status": "error",
    "message": f"Contract {_CONTRACT_ID} not found",
}

_NO_DATA_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "message": "No EVM period data found",
    "total_bac": _TOTAL_BAC,
    "wbs_count": 0,
}

# Yellow CPI scenario: CPI=0.90 (0.85 <= 0.90 < 0.95 -> yellow)
_YELLOW_CPI_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "title": _CONTRACT_TITLE,
    "total_bac": 1_000_000.0,
    "total_pv": 1_000_000.0,
    "total_ev": 900_000.0,
    "total_ac": 1_000_000.0,
    "percent_complete": 90.0,
    "indicators": {
        "cpi": 0.9, "spi": 0.9,
        "cost_variance": -100_000.0, "schedule_variance": -100_000.0,
        "eac": None, "etc": None, "vac": None, "tcpi": None,
    },
    "cpi_status": "yellow",
    "spi_status": "yellow",
    "wbs_count": 1,
}

# Red SPI scenario: SPI=0.80 (< 0.85 -> red)
_RED_SPI_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "title": _CONTRACT_TITLE,
    "total_bac": 1_000_000.0,
    "total_pv": 1_000_000.0,
    "total_ev": 800_000.0,
    "total_ac": 900_000.0,
    "percent_complete": 80.0,
    "indicators": {
        "cpi": 0.8889, "spi": 0.8,
        "cost_variance": -100_000.0, "schedule_variance": -200_000.0,
        "eac": None, "etc": None, "vac": None, "tcpi": None,
    },
    "cpi_status": "yellow",
    "spi_status": "red",
    "wbs_count": 1,
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
    return client.get(f"/api/cpmp/contracts/{contract_id}/evm")


# ---------------------------------------------------------------------------
# Tests: successful aggregation with BAC=500K, PV=400K, EV=380K, AC=395K
# ---------------------------------------------------------------------------


class TestGetEvmAggregateMetrics:
    """GET /api/cpmp/contracts/<id>/evm must return 200 with aggregated ANSI/EIA-748 metrics."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app, mock_return=None):
        ret = mock_return if mock_return is not None else _OK_RESULT
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=ret,
        ) as mock_agg:
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        return resp, mock_agg

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

    def test_response_has_total_bac(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_bac" in data

    def test_response_has_total_pv(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_pv" in data

    def test_response_has_total_ev(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_ev" in data

    def test_response_has_total_ac(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "total_ac" in data

    def test_response_has_percent_complete(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "percent_complete" in data

    def test_response_has_indicators(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "indicators" in data
        assert isinstance(data["indicators"], dict)

    def test_cpi_reflects_ev_over_ac(self, api_app):
        """CPI = EV/AC = 380000/395000 ≈ 0.962."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["cpi"] == pytest.approx(0.962, abs=1e-4)

    def test_spi_reflects_ev_over_pv(self, api_app):
        """SPI = EV/PV = 380000/400000 = 0.95."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["spi"] == pytest.approx(0.95, abs=1e-4)

    def test_cost_variance_is_ev_minus_ac(self, api_app):
        """CV = EV - AC = 380000 - 395000 = -15000.0."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["cost_variance"] == pytest.approx(-15_000.0)

    def test_schedule_variance_is_ev_minus_pv(self, api_app):
        """SV = EV - PV = 380000 - 400000 = -20000.0."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["schedule_variance"] == pytest.approx(-20_000.0)

    def test_response_has_cpi_status(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "cpi_status" in data

    def test_response_has_spi_status(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "spi_status" in data

    def test_cpi_status_is_green(self, api_app):
        """CPI 0.962 >= 0.95 yellow threshold — must be green."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["cpi_status"] == "green"

    def test_spi_status_is_green(self, api_app):
        """SPI 0.95 == yellow threshold — still green (>= comparison)."""
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert data["spi_status"] == "green"

    def test_response_has_wbs_count(self, api_app):
        resp, _ = self._call(api_app)
        data = resp.get_json()
        assert "wbs_count" in data

    def test_aggregate_called_with_correct_contract_id(self, api_app):
        _, mock_agg = self._call(api_app)
        args, _ = mock_agg.call_args
        assert args[0] == _CONTRACT_ID


# ---------------------------------------------------------------------------
# Tests: contract not found
# ---------------------------------------------------------------------------


class TestGetEvmContractNotFound:
    """Engine returns error status for unknown contract — endpoint passes it through as 200."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_contract_not_found_returns_200(self, api_app):
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 200

    def test_contract_not_found_status_is_error(self, api_app):
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["status"] == "error"

    def test_contract_not_found_response_has_message(self, api_app):
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_NOT_FOUND_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert "message" in data


# ---------------------------------------------------------------------------
# Tests: no EVM period data
# ---------------------------------------------------------------------------


class TestGetEvmNoData:
    """Contract exists but has no EVM period records — returns informational message."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_no_evm_data_returns_200(self, api_app):
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_NO_DATA_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        assert resp.status_code == 200

    def test_no_evm_data_returns_message(self, api_app):
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_NO_DATA_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data.get("message") == "No EVM period data found"


# ---------------------------------------------------------------------------
# Tests: status color thresholds
# ---------------------------------------------------------------------------


class TestGetEvmStatusColors:
    """EVM performance thresholds drive cpi_status and spi_status color values."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def test_yellow_cpi_when_between_thresholds(self, api_app):
        """CPI 0.90 is 0.85 <= CPI < 0.95 — must return cpi_status == 'yellow'."""
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_YELLOW_CPI_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["cpi_status"] == "yellow"

    def test_red_spi_when_below_red_threshold(self, api_app):
        """SPI 0.80 is < 0.85 red threshold — must return spi_status == 'red'."""
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_RED_SPI_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        data = resp.get_json()
        assert data["spi_status"] == "red"
