# CUI // SP-CTI
"""Tests for CPARS rating threshold values (NDAA-mandated).

Verifies:
1. RATING_THRESHOLDS contains exactly the 4 boundary keys.
2. exceptional threshold is 0.90.
3. very_good threshold is 0.80.
4. satisfactory threshold is 0.65.
5. marginal threshold is 0.40.
6. _score_to_rating() maps boundary scores to correct ratings.
7. _score_to_rating() maps below-marginal scores to 'unsatisfactory'.
"""

import pytest

from tools.govcon.cpars_predictor import RATING_THRESHOLDS, _score_to_rating

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPECTED_THRESHOLDS = {
    "exceptional": 0.90,
    "very_good": 0.80,
    "satisfactory": 0.65,
    "marginal": 0.40,
}

_REQUIRED_KEYS = {"exceptional", "very_good", "satisfactory", "marginal"}


# ---------------------------------------------------------------------------
# Tests: threshold key presence and values
# ---------------------------------------------------------------------------


class TestCparsRatingThresholdValues:
    """Each RATING_THRESHOLDS entry matches the NDAA-mandated boundary."""

    def test_rating_thresholds_has_required_keys(self):
        assert _REQUIRED_KEYS == set(RATING_THRESHOLDS.keys())

    def test_exceptional_threshold_is_0_90(self):
        assert RATING_THRESHOLDS["exceptional"] == pytest.approx(0.90)

    def test_very_good_threshold_is_0_80(self):
        assert RATING_THRESHOLDS["very_good"] == pytest.approx(0.80)

    def test_satisfactory_threshold_is_0_65(self):
        assert RATING_THRESHOLDS["satisfactory"] == pytest.approx(0.65)

    def test_marginal_threshold_is_0_40(self):
        assert RATING_THRESHOLDS["marginal"] == pytest.approx(0.40)

    def test_thresholds_are_strictly_decreasing(self):
        """Ensure thresholds form a valid descending ladder."""
        assert RATING_THRESHOLDS["exceptional"] > RATING_THRESHOLDS["very_good"]
        assert RATING_THRESHOLDS["very_good"] > RATING_THRESHOLDS["satisfactory"]
        assert RATING_THRESHOLDS["satisfactory"] > RATING_THRESHOLDS["marginal"]


# ---------------------------------------------------------------------------
# Tests: _score_to_rating() boundary mapping
# ---------------------------------------------------------------------------


class TestScoreToRatingBoundaries:
    """_score_to_rating() maps scores to the correct CPARS rating at boundaries."""

    def test_score_1_0_is_exceptional(self):
        assert _score_to_rating(1.0) == "exceptional"

    def test_score_at_exceptional_boundary_is_exceptional(self):
        assert _score_to_rating(0.90) == "exceptional"

    def test_score_just_below_exceptional_is_very_good(self):
        assert _score_to_rating(0.899) == "very_good"

    def test_score_at_very_good_boundary_is_very_good(self):
        assert _score_to_rating(0.80) == "very_good"

    def test_score_just_below_very_good_is_satisfactory(self):
        assert _score_to_rating(0.799) == "satisfactory"

    def test_score_at_satisfactory_boundary_is_satisfactory(self):
        assert _score_to_rating(0.65) == "satisfactory"

    def test_score_just_below_satisfactory_is_marginal(self):
        assert _score_to_rating(0.649) == "marginal"

    def test_score_at_marginal_boundary_is_marginal(self):
        assert _score_to_rating(0.40) == "marginal"

    def test_score_just_below_marginal_is_unsatisfactory(self):
        assert _score_to_rating(0.399) == "unsatisfactory"

    def test_score_0_0_is_unsatisfactory(self):
        assert _score_to_rating(0.0) == "unsatisfactory"
