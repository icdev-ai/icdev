# CUI // SP-CTI
"""Tests for constant extraction in the engage (R4) proposal_genesis reflex.

AI-ify opp 5402 (hardcoded_threshold -> anomaly_detection): the inline magic
numbers in engage.py (query limits, audit lookback window, engagement-score
saturation points, component weights, and the recency decay window) were
extracted into named, config-aligned module constants.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.engage import (
    _OPPS_WITHOUT_ACCOUNTS_LIMIT,
    _AUDIT_INTERACTION_LOOKBACK_DAYS,
    _AUDIT_INTERACTION_LIMIT,
    _FREQUENCY_SATURATION_COUNT,
    _PIPELINE_SATURATION_COUNT,
    _ENGAGEMENT_WEIGHT_RECENCY,
    _ENGAGEMENT_WEIGHT_FREQUENCY,
    _ENGAGEMENT_WEIGHT_PIPELINE,
    _ENGAGEMENT_WEIGHT_WIN_RATE,
    _RECENCY_DECAY_DAYS,
    _score_recency,
)


class TestEngageConstants:
    def test_query_limits_positive(self):
        assert _OPPS_WITHOUT_ACCOUNTS_LIMIT > 0
        assert _AUDIT_INTERACTION_LIMIT > 0

    def test_lookback_window_positive(self):
        assert _AUDIT_INTERACTION_LOOKBACK_DAYS > 0

    def test_saturation_points_positive(self):
        assert _FREQUENCY_SATURATION_COUNT > 0
        assert _PIPELINE_SATURATION_COUNT > 0

    def test_recency_decay_window_positive(self):
        assert _RECENCY_DECAY_DAYS > 0


class TestEngagementWeights:
    """The four engagement-score component weights must form a convex combination."""

    def test_weights_sum_to_one(self):
        total = (
            _ENGAGEMENT_WEIGHT_RECENCY
            + _ENGAGEMENT_WEIGHT_FREQUENCY
            + _ENGAGEMENT_WEIGHT_PIPELINE
            + _ENGAGEMENT_WEIGHT_WIN_RATE
        )
        assert abs(total - 1.0) < 1e-9

    def test_weights_non_negative(self):
        for w in (
            _ENGAGEMENT_WEIGHT_RECENCY,
            _ENGAGEMENT_WEIGHT_FREQUENCY,
            _ENGAGEMENT_WEIGHT_PIPELINE,
            _ENGAGEMENT_WEIGHT_WIN_RATE,
        ):
            assert 0.0 <= w <= 1.0


class TestRecencyDecayWiring:
    """Verify _score_recency uses the extracted decay-window constant."""

    def test_no_interaction_scores_zero(self):
        assert _score_recency(None) == 0.0

    def test_beyond_decay_window_scores_zero(self):
        from datetime import datetime, timedelta, timezone

        old = (datetime.now(timezone.utc) - timedelta(days=_RECENCY_DECAY_DAYS + 10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        assert _score_recency(old) == 0.0

    def test_within_window_scores_between_zero_and_one(self):
        from datetime import datetime, timedelta, timezone

        recent = (
            datetime.now(timezone.utc) - timedelta(days=_RECENCY_DECAY_DAYS / 2.0)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        score = _score_recency(recent)
        assert 0.0 < score < 1.0
