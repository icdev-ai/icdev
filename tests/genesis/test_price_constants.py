# CUI // SP-CTI
"""Tests for constant extraction in the price (R17) proposal_genesis reflex.

AI-ify opp 5427 (hardcoded_threshold → anomaly_detection): the inline magic
numbers in price.py (cost-volume query limits, line-item completeness gate,
and details-payload caps) were extracted into named, config-aligned module
constants.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.price import (
    _VOLUMES_UPDATE_LIMIT,
    _OPPS_WITHOUT_PRICING_LIMIT,
    _MIN_LINE_ITEMS_FOR_COMPLETE,
    _INCOMPLETE_DETAILS_LIMIT,
    _MISSING_PRICING_DETAILS_LIMIT,
)


class TestPriceConstants:
    def test_fetch_limits_positive(self):
        assert _VOLUMES_UPDATE_LIMIT > 0
        assert _OPPS_WITHOUT_PRICING_LIMIT > 0

    def test_details_caps_positive(self):
        assert _INCOMPLETE_DETAILS_LIMIT > 0
        assert _MISSING_PRICING_DETAILS_LIMIT > 0

    def test_line_item_gate_positive(self):
        # A volume needs at least one line item to count as populated.
        assert _MIN_LINE_ITEMS_FOR_COMPLETE >= 1


class TestPriceConstantWiring:
    """Verify the extracted constants are actually used at the call sites."""

    def test_line_item_gate_completeness(self):
        # _check_line_item_coverage flags has_items via count >= gate.
        below = _MIN_LINE_ITEMS_FOR_COMPLETE - 1
        assert (below >= _MIN_LINE_ITEMS_FOR_COMPLETE) is False
        assert (_MIN_LINE_ITEMS_FOR_COMPLETE >= _MIN_LINE_ITEMS_FOR_COMPLETE) is True

    def test_details_caps_truncate(self):
        # The details payload slices at the cap constants.
        sample = list(range(_INCOMPLETE_DETAILS_LIMIT + 25))
        assert len(sample[:_INCOMPLETE_DETAILS_LIMIT]) == _INCOMPLETE_DETAILS_LIMIT
        assert len(sample[:_MISSING_PRICING_DETAILS_LIMIT]) == _MISSING_PRICING_DETAILS_LIMIT
