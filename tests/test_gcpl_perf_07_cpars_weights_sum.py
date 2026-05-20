# CUI // SP-CTI
"""Tests for CPARS dimension weights summing to 1.0.

Verifies:
1. PREDICTION_WEIGHTS contains exactly the 5 required dimensions.
2. quality weight is 0.25.
3. schedule weight is 0.25.
4. cost weight is 0.20.
5. management weight is 0.15.
6. small_business weight is 0.15.
7. All 5 weights sum to exactly 1.0.
"""

import pytest

from tools.govcon.cpars_predictor import PREDICTION_WEIGHTS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPECTED_WEIGHTS = {
    "quality": 0.25,
    "schedule": 0.25,
    "cost": 0.20,
    "management": 0.15,
    "small_business": 0.15,
}

_REQUIRED_DIMENSIONS = {"quality", "schedule", "cost", "management", "small_business"}


# ---------------------------------------------------------------------------
# Tests: weight presence and values
# ---------------------------------------------------------------------------


class TestCparsWeightValues:
    """Each CPARS dimension weight matches the FAR-mandated value."""

    def test_prediction_weights_has_all_dimensions(self):
        assert _REQUIRED_DIMENSIONS == set(PREDICTION_WEIGHTS.keys())

    def test_quality_weight_is_0_25(self):
        assert PREDICTION_WEIGHTS["quality"] == pytest.approx(0.25)

    def test_schedule_weight_is_0_25(self):
        assert PREDICTION_WEIGHTS["schedule"] == pytest.approx(0.25)

    def test_cost_weight_is_0_20(self):
        assert PREDICTION_WEIGHTS["cost"] == pytest.approx(0.20)

    def test_management_weight_is_0_15(self):
        assert PREDICTION_WEIGHTS["management"] == pytest.approx(0.15)

    def test_small_business_weight_is_0_15(self):
        assert PREDICTION_WEIGHTS["small_business"] == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# Tests: weights sum to 1.0
# ---------------------------------------------------------------------------


class TestCparsWeightsSum:
    """CPARS dimension weights must sum to exactly 1.0."""

    def test_weights_sum_to_one(self):
        total = sum(PREDICTION_WEIGHTS.values())
        assert total == pytest.approx(1.0), (
            f"CPARS weights sum to {total}, expected 1.0 — "
            f"weights: {PREDICTION_WEIGHTS}"
        )

    def test_no_extra_dimensions(self):
        assert len(PREDICTION_WEIGHTS) == len(_REQUIRED_DIMENSIONS)
