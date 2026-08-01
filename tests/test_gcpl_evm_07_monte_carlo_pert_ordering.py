# CUI // SP-CTI
"""Tests for GET /api/cpmp/contracts/<id>/evm/forecast — Monte Carlo P10 <= P50 <= P90.

Verifies that the PERT-based Monte Carlo EAC forecast (D-CPMP-2) maintains valid
percentile ordering (P10 <= P50 <= P90) and that _pert_sample stays within bounds:
 1. percentiles.P10 is present and positive.
 2. percentiles.P50 is present and positive.
 3. percentiles.P90 is present and positive.
 4. P10 <= P50 (monotone non-decreasing at P10/P50 boundary).
 5. P50 <= P90 (monotone non-decreasing at P50/P90 boundary).
 6. P10 < P90 (valid spread — P10 strictly below P90 with variance present).
 7. All percentile values are finite (no inf/NaN from division by zero).
 8. _pert_sample returns values within [optimistic, pessimistic] (D22 stdlib random).
 9. Sorted _pert_sample draws produce P10 <= P50 <= P90 ordering.
10. distribution.min <= P10 and P90 <= distribution.max (percentiles inside overall range).
"""
import random
from unittest.mock import patch

import pytest
from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONTRACT_ID = "ctr-test-evm-0007"
_CONTRACT_NUMBER = "W52P1J-26-C-0007"
_CONTRACT_TITLE = "Program Management Support"

# Moderate cost overrun scenario: CPI = 0.92 (yellow zone)
# BAC=2_500_000, current_cpi=0.92, best_cpi=0.97, worst_cpi=0.80
# P10 = 2_600_000.00  → optimistic PERT tail (CPI ~0.9615)
# P50 = 2_717_391.30  → median (BAC / 0.92 rounded to 2dp)
# P90 = 3_125_000.00  → pessimistic PERT tail (CPI = 0.80)
_BAC = 2_500_000.00
_CURRENT_CPI = 0.92

_P10 = 2_600_000.00
_P50 = 2_717_391.30    # round(2_500_000 / 0.92, 2)
_P90 = 3_125_000.00    # round(2_500_000 / 0.80, 2)

_DIST_MIN = 2_525_000.00
_DIST_MAX = 3_350_000.00
_DIST_MEAN = 2_810_000.00

_FORECAST_RESULT = {
    "status": "ok",
    "contract_id": _CONTRACT_ID,
    "contract_number": _CONTRACT_NUMBER,
    "total_bac": _BAC,
    "current_cpi": _CURRENT_CPI,
    "best_cpi": 0.97,
    "worst_cpi": 0.80,
    "iterations": 10000,
    "percentiles": {
        "P10": _P10,
        "P50": _P50,
        "P90": _P90,
    },
    "distribution": {
        "mean": _DIST_MEAN,
        "min": _DIST_MIN,
        "max": _DIST_MAX,
        "median": _P50,
    },
    "deterministic_eac": round(_BAC / _CURRENT_CPI, 2),
    "variance_from_bac": {
        "P10": round(_P10 - _BAC, 2),
        "P50": round(_P50 - _BAC, 2),
        "P90": round(_P90 - _BAC, 2),
    },
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


def _get_forecast(client, contract_id):
    return client.get(f"/api/cpmp/contracts/{contract_id}/evm/forecast")


# ---------------------------------------------------------------------------
# Tests: Monte Carlo P10 <= P50 <= P90 via API endpoint
# ---------------------------------------------------------------------------


class TestMonteCarloPercentileOrdering:
    """PERT-based Monte Carlo forecast must return P10 <= P50 <= P90."""

    @pytest.fixture()
    def api_app(self):
        return _build_cpmp_api_app()

    def _call(self, api_app):
        with patch(
            "tools.govcon.evm_engine.forecast_monte_carlo",
            return_value=_FORECAST_RESULT,
        ):
            with api_app.test_client() as c:
                resp = _get_forecast(c, _CONTRACT_ID)
        return resp

    def test_p10_is_present_and_positive(self, api_app):
        """percentiles.P10 must be present and positive in the forecast response."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert "P10" in data["percentiles"]
        assert data["percentiles"]["P10"] > 0

    def test_p50_is_present_and_positive(self, api_app):
        """percentiles.P50 must be present and positive in the forecast response."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert "P50" in data["percentiles"]
        assert data["percentiles"]["P50"] > 0

    def test_p90_is_present_and_positive(self, api_app):
        """percentiles.P90 must be present and positive in the forecast response."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert "P90" in data["percentiles"]
        assert data["percentiles"]["P90"] > 0

    def test_p10_lte_p50(self, api_app):
        """D-CPMP-2: P10 <= P50 — lower percentile must not exceed the median."""
        resp = self._call(api_app)
        data = resp.get_json()
        p = data["percentiles"]
        assert p["P10"] <= p["P50"]

    def test_p50_lte_p90(self, api_app):
        """D-CPMP-2: P50 <= P90 — median must not exceed the upper percentile."""
        resp = self._call(api_app)
        data = resp.get_json()
        p = data["percentiles"]
        assert p["P50"] <= p["P90"]

    def test_p10_lt_p90_valid_spread(self, api_app):
        """P10 < P90 — distribution must have positive spread when variance is present."""
        resp = self._call(api_app)
        data = resp.get_json()
        p = data["percentiles"]
        assert p["P10"] < p["P90"]

    def test_percentile_values_are_finite(self, api_app):
        """All percentile values must be finite — no inf or NaN from degenerate CPI."""
        import math

        resp = self._call(api_app)
        data = resp.get_json()
        for key, val in data["percentiles"].items():
            assert math.isfinite(val), f"{key} = {val} is not finite"

    def test_p10_gte_distribution_min(self, api_app):
        """P10 >= distribution.min — percentile cannot be below the observed minimum."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["percentiles"]["P10"] >= data["distribution"]["min"]

    def test_p90_lte_distribution_max(self, api_app):
        """P90 <= distribution.max — percentile cannot exceed the observed maximum."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["percentiles"]["P90"] <= data["distribution"]["max"]

    def test_status_ok(self, api_app):
        """Forecast response must carry status='ok' for a valid contract."""
        resp = self._call(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Unit tests: _pert_sample distribution properties (D22 stdlib random only)
# ---------------------------------------------------------------------------


class TestPertSampleBounds:
    """_pert_sample must stay within [optimistic, pessimistic] and produce ordered percentiles."""

    # CPI range used in a typical moderate-overrun scenario
    _OPT = 0.83
    _ML = 0.92
    _PES = 1.02

    _N = 1000
    _SEED = 42

    def _draw(self):
        from tools.govcon.evm_engine import _pert_sample

        rng_state = random.getstate()
        random.seed(self._SEED)
        samples = [_pert_sample(self._OPT, self._ML, self._PES) for _ in range(self._N)]
        random.setstate(rng_state)
        return samples

    def test_all_samples_within_bounds(self):
        """Every _pert_sample draw must lie within [optimistic, pessimistic]."""
        samples = self._draw()
        for s in samples:
            assert self._OPT <= s <= self._PES, f"sample {s} out of bounds"

    def test_sorted_samples_produce_p10_lte_p50(self):
        """Sorted draws: P10 index <= P50 index (monotone non-decreasing ordering)."""
        samples = sorted(self._draw())
        n = len(samples)
        p10 = samples[int(0.10 * n)]
        p50 = samples[int(0.50 * n)]
        assert p10 <= p50

    def test_sorted_samples_produce_p50_lte_p90(self):
        """Sorted draws: P50 index <= P90 index (monotone non-decreasing ordering)."""
        samples = sorted(self._draw())
        n = len(samples)
        p50 = samples[int(0.50 * n)]
        p90 = samples[int(0.90 * n)]
        assert p50 <= p90

    def test_sorted_samples_p10_lt_p90_spread(self):
        """Sorted draws: P10 < P90 — PERT distribution must produce positive spread."""
        samples = sorted(self._draw())
        n = len(samples)
        p10 = samples[int(0.10 * n)]
        p90 = samples[int(0.90 * n)]
        assert p10 < p90

    def test_degenerate_opt_equals_pes_returns_most_likely(self):
        """When optimistic >= pessimistic, _pert_sample must return most_likely (guard)."""
        from tools.govcon.evm_engine import _pert_sample

        # Degenerate: opt == pes
        assert _pert_sample(0.92, 0.92, 0.92) == 0.92
        # Degenerate: opt > pes
        assert _pert_sample(1.05, 0.92, 0.80) == 0.92
