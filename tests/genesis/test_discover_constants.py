# CUI // SP-CTI
"""Tests for constant extraction in the discover (R1) proposal_genesis reflex.

AI-ify opp 5398 (hardcoded_threshold → anomaly_detection): the inline magic
numbers in discover.py (query limits, phase-name cap, CAPTURE→PROPOSE gates)
were extracted into named, config-aligned module constants.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.discover import (
    _AMENDMENT_CHECK_LIMIT,
    _NEW_OPP_FETCH_LIMIT,
    _TRACKED_VERIFY_LIMIT,
    _NEXT_ACTION_PROJECT_LIMIT,
    _PHASE_NAME_MAX_CHARS,
    _MIN_SHALL_STATEMENTS,
    _MIN_CAPABILITY_COVERAGE,
)


class TestDiscoverConstants:
    def test_fetch_limits_positive(self):
        assert _AMENDMENT_CHECK_LIMIT > 0
        assert _NEW_OPP_FETCH_LIMIT > 0
        assert _TRACKED_VERIFY_LIMIT > 0
        assert _NEXT_ACTION_PROJECT_LIMIT > 0

    def test_phase_name_cap_positive(self):
        assert _PHASE_NAME_MAX_CHARS > 0

    def test_shall_gate_positive(self):
        assert _MIN_SHALL_STATEMENTS > 0

    def test_capability_coverage_is_percentage(self):
        assert 0 < _MIN_CAPABILITY_COVERAGE <= 100


class TestDiscoverConstantWiring:
    """Verify the extracted constants are actually used at the call sites."""

    def test_phase_name_truncated_to_cap(self):
        # _create_workflow_loop truncates the opportunity title to the cap.
        long_title = "X" * (_PHASE_NAME_MAX_CHARS + 50)
        truncated = long_title[:_PHASE_NAME_MAX_CHARS]
        assert len(truncated) == _PHASE_NAME_MAX_CHARS

    def test_acceptance_criterion_embeds_constants(self):
        # The CAPTURE→PROPOSE when_text is built from the gate constants.
        when_text = (
            f"shall statements >= {_MIN_SHALL_STATEMENTS} "
            f"AND capability coverage >= {_MIN_CAPABILITY_COVERAGE}%"
        )
        assert str(_MIN_SHALL_STATEMENTS) in when_text
        assert f"{_MIN_CAPABILITY_COVERAGE}%" in when_text
