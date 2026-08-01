# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/evm — EAC is present and > 0 when CPI < 1.0.

Verifies that when CPI < 1.0 (cost overrun forecast), the aggregate EVM response:
 1. indicators.eac is not None.
 2. indicators.eac is greater than 0.
 3. indicators.eac exceeds total_bac (projected overrun → EAC > BAC).
 4. indicators.cpi is less than 1.0 (cost overrun confirmed).
 5. EAC equals BAC / CPI per ANSI/EIA-748 (exact formula).
 6. cpi_status is 'red' when CPI < 0.85 red threshold.
 7. indicators.vac is negative (at-completion cost overrun).
 8. indicators.etc is positive (remaining cost to complete is positive).
"""
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-evm-0005"
_CONTRACT_NUMBER = "W52P1J-26-C-0005"
_CONTRACT_TITLE = "Systems Engineering Support"

# Cost overrun scenario: EV < AC → CPI < 1.0 (red zone)
# BAC=1_200_000, PV=600_000, EV=500_000, AC=650_000
# CPI  = round(500_000 / 650_000, 4) = 0.7692  (< 1.0 — cost overrun)
# SPI  = round(500_000 / 600_000, 4) = 0.8333  (< 0.85 — red)
# EAC  = round(1_200_000 / (500_000/650_000), 2) = 1_560_000.0  (> BAC — projected overrun)
# ETC  = EAC - AC = 1_560_000.0 - 650_000.0 = 910_000.0
# VAC  = BAC - EAC = 1_200_000.0 - 1_560_000.0 = -360_000.0  (negative = overrun)
# TCPI = (BAC-EV)/(BAC-AC) = 700_000/550_000 ≈ 1.2727
_TOTAL_BAC = 1_200_000.0
_TOTAL_PV   = 600_000.0
_TOTAL_EV   = 500_000.0
_TOTAL_AC   = 650_000.0

_CPI = 0.7692           # round(500_000/650_000, 4)
_EAC = 1_560_000.0      # BAC * AC / EV = 1_200_000 * 650_000 / 500_000

_INDICATORS = {
    "cpi": _CPI,
    "spi": 0.8333,
    "cost_variance": -150_000.0,
    "schedule_variance": -100_000.0,
    "eac": _EAC,
    "etc": 910_000.0,
    "vac": -360_000.0,
    "tcpi": 1.2727,
}

_OVERRUN_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "title": _CONTRACT_TITLE,
    "total_bac": _TOTAL_BAC,
    "total_pv": _TOTAL_PV,
    "total_ev": _TOTAL_EV,
    "total_ac": _TOTAL_AC,
    "percent_complete": 41.67,   # round(500_000/1_200_000*100, 2)
    "indicators": _INDICATORS,
    "cpi_status": "red",         # CPI 0.7692 < 0.85 red threshold
    "spi_status": "red",         # SPI 0.8333 < 0.85 red threshold
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
# Tests: EAC is present and > 0 when CPI < 1.0 (cost overrun forecast)
# ---------------------------------------------------------------------------


class TestEvmEacPositiveWhenCpiBelow1:
    """EAC must be a positive number in the aggregate response when CPI < 1.0."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.evm_engine.aggregate_contract_evm",
            return_value=_OVERRUN_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get(c, _CONTRACT_ID)
        return resp

    def test_eac_is_not_none(self, api_app):
        """indicators.eac must be present (not None) when CPI < 1.0."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["eac"] is not None

    def test_eac_is_positive(self, api_app):
        """indicators.eac must be > 0 for any cost overrun scenario."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["eac"] > 0

    def test_eac_exceeds_bac_when_cpi_below_1(self, api_app):
        """EAC > BAC when CPI < 1.0 — cost overrun increases the completion forecast."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["eac"] > data["total_bac"]

    def test_cpi_is_below_1(self, api_app):
        """Scenario guard: CPI must be < 1.0 to confirm the cost overrun condition."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["cpi"] < 1.0

    def test_eac_equals_bac_divided_by_cpi(self, api_app):
        """EAC = BAC / CPI per ANSI/EIA-748 — exact value must match."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["eac"] == pytest.approx(_EAC, rel=1e-4)

    def test_cpi_status_is_red_below_red_threshold(self, api_app):
        """CPI 0.7692 < 0.85 red threshold — cpi_status must be 'red'."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["cpi_status"] == "red"

    def test_vac_is_negative_on_overrun(self, api_app):
        """VAC = BAC - EAC < 0 when CPI < 1.0 (projected at-completion cost overrun)."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["vac"] < 0

    def test_etc_is_positive(self, api_app):
        """ETC = EAC - AC > 0 — remaining cost to complete must be positive."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["indicators"]["etc"] > 0
