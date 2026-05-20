# CUI // SP-CTI
"""Tests for contract health dimension weights summing to 1.0.

Verifies:
1. HEALTH_WEIGHTS contains exactly the 5 required dimensions.
2. evm weight is 0.30.
3. deliverables weight is 0.25.
4. cpars weight is 0.20.
5. negative_events weight is 0.15.
6. funding weight is 0.10.
7. All 5 weights sum to exactly 1.0.
"""

import pytest

from tools.govcon.portfolio_manager import HEALTH_WEIGHTS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPECTED_WEIGHTS = {
    "evm": 0.30,
    "deliverables": 0.25,
    "cpars": 0.20,
    "negative_events": 0.15,
    "funding": 0.10,
}

_REQUIRED_DIMENSIONS = {"evm", "deliverables", "cpars", "negative_events", "funding"}


# ---------------------------------------------------------------------------
# Tests: weight presence and values
# ---------------------------------------------------------------------------


class TestHealthWeightValues:
    """Each health dimension weight matches the D-CPMP-8 mandated value."""

    def test_health_weights_has_all_dimensions(self):
        assert _REQUIRED_DIMENSIONS == set(HEALTH_WEIGHTS.keys())

    def test_evm_weight_is_0_30(self):
        assert HEALTH_WEIGHTS["evm"] == pytest.approx(0.30)

    def test_deliverables_weight_is_0_25(self):
        assert HEALTH_WEIGHTS["deliverables"] == pytest.approx(0.25)

    def test_cpars_weight_is_0_20(self):
        assert HEALTH_WEIGHTS["cpars"] == pytest.approx(0.20)

    def test_negative_events_weight_is_0_15(self):
        assert HEALTH_WEIGHTS["negative_events"] == pytest.approx(0.15)

    def test_funding_weight_is_0_10(self):
        assert HEALTH_WEIGHTS["funding"] == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Tests: weights sum to 1.0
# ---------------------------------------------------------------------------


class TestHealthWeightsSum:
    """Contract health dimension weights must sum to exactly 1.0."""

    def test_weights_sum_to_one(self):
        total = sum(HEALTH_WEIGHTS.values())
        assert total == pytest.approx(1.0), (
            f"Health weights sum to {total}, expected 1.0 — "
            f"weights: {HEALTH_WEIGHTS}"
        )

    def test_no_extra_dimensions(self):
        assert len(HEALTH_WEIGHTS) == len(_REQUIRED_DIMENSIONS)
