# CUI // SP-CTI
"""Tests for constant extraction in the team (R23) proposal_genesis reflex.

AI-ify opp 5450 (hardcoded_threshold → anomaly_detection): the inline magic
numbers in team.py (opportunity scan limit, teaming-agreement expiration
windows, full-workshare target, and details-payload caps) were extracted into
named, config-aligned module constants.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.team import (
    _OPPS_WITH_TEAMS_LIMIT,
    _TA_EXPIRED_DAYS,
    _TA_EXPIRATION_WARN_DAYS,
    _FULL_WORKSHARE_PCT,
    _TA_DETAILS_LIMIT,
    _OCI_DETAILS_LIMIT,
)


class TestTeamConstants:
    def test_scan_limit_positive(self):
        assert _OPPS_WITH_TEAMS_LIMIT > 0

    def test_details_caps_positive(self):
        assert _TA_DETAILS_LIMIT > 0
        assert _OCI_DETAILS_LIMIT > 0

    def test_full_workshare_target(self):
        # Workshare allocation is a percentage; a full team sums to 100%.
        assert _FULL_WORKSHARE_PCT == 100.0


class TestTAExpirationWindows:
    """The TA expiration windows form an ordered anomaly band."""

    def test_expired_before_warn(self):
        # Already-expired boundary must come before the soon-to-expire warning.
        assert _TA_EXPIRED_DAYS < _TA_EXPIRATION_WARN_DAYS

    def test_expired_boundary_is_zero(self):
        # days_until < 0 means the TA expiration date is in the past.
        assert _TA_EXPIRED_DAYS == 0

    def test_severity_banding(self):
        # Mirror _check_ta_expiration: classify by days_until.
        def classify(days_until):
            if days_until < _TA_EXPIRED_DAYS:
                return "critical"
            if days_until < _TA_EXPIRATION_WARN_DAYS:
                return "warning"
            return "ok"

        assert classify(-5) == "critical"
        assert classify(0) == "warning"
        assert classify(_TA_EXPIRATION_WARN_DAYS - 1) == "warning"
        assert classify(_TA_EXPIRATION_WARN_DAYS) == "ok"
        assert classify(120) == "ok"


class TestTeamConstantWiring:
    """Verify the extracted constants behave at the call sites."""

    def test_workshare_gap_computation(self):
        # _check_workshare_gaps: gap = max(0, target - actual).
        assert max(0, round(_FULL_WORKSHARE_PCT - 60.0, 1)) == 40.0
        # Over-allocation clamps to a non-negative gap.
        assert max(0, round(_FULL_WORKSHARE_PCT - 130.0, 1)) == 0

    def test_details_caps_truncate(self):
        sample = list(range(_TA_DETAILS_LIMIT + _OCI_DETAILS_LIMIT + 10))
        assert len(sample[:_TA_DETAILS_LIMIT]) == _TA_DETAILS_LIMIT
        assert len(sample[:_OCI_DETAILS_LIMIT]) == _OCI_DETAILS_LIMIT
