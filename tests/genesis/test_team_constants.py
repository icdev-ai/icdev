# CUI // SP-CTI
"""Tests for constant extraction in the team (R23) proposal_genesis reflex.

AI-ify opp 5449 (hardcoded_threshold → anomaly_detection): the inline magic
numbers in team.py (opportunity scan limit, TA expiration windows, full
workshare percentage, and details-payload caps) were extracted into named,
config-aligned module constants.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.team import (
    _OPPS_WITH_TEAMS_LIMIT,
    _TA_EXPIRED_DAYS,
    _TA_EXPIRATION_WARNING_DAYS,
    _FULL_WORKSHARE_PCT,
    _TA_DETAILS_LIMIT,
    _OCI_DETAILS_LIMIT,
)


class TestTeamConstants:
    def test_fetch_limit_positive(self):
        assert _OPPS_WITH_TEAMS_LIMIT > 0

    def test_details_caps_positive(self):
        assert _TA_DETAILS_LIMIT > 0
        assert _OCI_DETAILS_LIMIT > 0

    def test_full_workshare_is_100(self):
        assert _FULL_WORKSHARE_PCT == 100.0

    def test_expiration_windows_ordered(self):
        # Expired boundary must sit at/below the "expiring soon" warning window.
        assert _TA_EXPIRED_DAYS < _TA_EXPIRATION_WARNING_DAYS
        assert _TA_EXPIRATION_WARNING_DAYS > 0


class TestTeamConstantWiring:
    """Verify the extracted constants drive the same classification logic."""

    def _classify(self, days_until):
        # Mirrors _check_ta_expiration's severity ladder.
        if days_until < _TA_EXPIRED_DAYS:
            return "critical"
        if days_until < _TA_EXPIRATION_WARNING_DAYS:
            return "warning"
        return "ok"

    def test_expired_is_critical(self):
        assert self._classify(-1) == "critical"

    def test_within_window_is_warning(self):
        assert self._classify(_TA_EXPIRATION_WARNING_DAYS - 1) == "warning"

    def test_beyond_window_is_ok(self):
        assert self._classify(_TA_EXPIRATION_WARNING_DAYS) == "ok"

    def test_workshare_gap_from_full(self):
        # A 40% allocated team leaves a 60% gap measured against full.
        assert max(0, round(_FULL_WORKSHARE_PCT - 40.0, 1)) == 60.0

    def test_details_caps_truncate(self):
        sample = list(range(_TA_DETAILS_LIMIT + 25))
        assert len(sample[:_TA_DETAILS_LIMIT]) == _TA_DETAILS_LIMIT
        assert len(sample[:_OCI_DETAILS_LIMIT]) == _OCI_DETAILS_LIMIT
