"""Unit tests for option-period go/no-go recommendation health-label handling.

Regression guard: a NULL numeric ``health_score`` must not be coerced to 0.0
(which the deterministic rules read as RED). When no score has been computed,
the recommendation falls back to the qualitative ``health`` label so an
unscored GREEN contract is not misreported as RED.
"""

from tools.govcon.option_period_tracker import (
    _HEALTH_LABEL_SCORE,
    _deterministic_recommendation,
)


def test_green_label_score_is_go():
    rec = _deterministic_recommendation(
        _HEALTH_LABEL_SCORE["green"], None, None, None, {}
    )
    assert "GREEN" in rec
    assert "recommend exercising" in rec


def test_red_label_score_is_caution():
    rec = _deterministic_recommendation(
        _HEALTH_LABEL_SCORE["red"], None, None, None, {}
    )
    assert "RED" in rec


def test_null_score_reports_insufficient_data_not_red():
    rec = _deterministic_recommendation(None, None, None, None, {})
    assert "RED" not in rec
    assert "not been scored" in rec or "insufficient data" in rec


def test_health_label_scores_map_to_expected_branches():
    assert _HEALTH_LABEL_SCORE["green"] >= 0.75
    assert 0.50 <= _HEALTH_LABEL_SCORE["yellow"] < 0.75
    assert _HEALTH_LABEL_SCORE["red"] < 0.50
