# CUI // SP-CTI
"""Tests for constant extraction in stagnation_detector.py and migration_canvas.py."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.stagnation_detector import (
    _OSCILLATION_TOLERANCE, _LLM_KEYWORD_CONTEXT_CHARS, _LLM_ALT_CONTEXT_CHARS,
    _LLM_ALT_MAX_TOKENS, _LLM_ALT_TEMPERATURE, _SCORE_DENOMINATOR,
    _RECENT_METRICS_LIMIT, StagnationDetector,
)
from tools.genesis.reflexes.migration_canvas import (
    _STALE_SESSION_DAYS, _EOL_URGENCY_DAYS, _STALE_PLAN_DAYS,
    _STALE_SESSION_BASE_CONF, _STALE_SESSION_CONF_PER_DAY, _STALE_SESSION_MAX_CONF,
    _EOL_CONF_BASE, _EOL_CONF_MULTIPLIER, _EOL_CONF_MAX,
    _STALE_PLAN_CONFIDENCE, _PROMOTION_THRESHOLD_DEFAULT, _HIGH_PRIORITY_THRESHOLD,
)


class TestStagnationConstants:
    def test_oscillation_tolerance_small(self):
        assert 0.0 < _OSCILLATION_TOLERANCE < 0.1

    def test_context_char_hierarchy(self):
        # Alternative-generation context is larger than keyword-extraction context
        assert _LLM_ALT_CONTEXT_CHARS >= _LLM_KEYWORD_CONTEXT_CHARS > 0

    def test_llm_params_sane(self):
        assert _LLM_ALT_MAX_TOKENS > 0
        assert 0.0 <= _LLM_ALT_TEMPERATURE <= 1.0

    def test_score_denominator_positive(self):
        assert _SCORE_DENOMINATOR > 0

    def test_recent_metrics_limit_positive(self):
        assert _RECENT_METRICS_LIMIT > 0

    def test_oscillation_detector_uses_constant(self):
        # Build a detector with config and verify period-2 detection still works
        det = StagnationDetector(config={"detection": {"oscillation_window": 4}})
        # A,B,A,B period-2 cycle
        result = det._detect_oscillation([1.0, 2.0, 1.0, 2.0])
        assert result is not None
        assert result["pattern"] == "period_2_cycle"

    def test_oscillation_detector_no_false_positive(self):
        det = StagnationDetector(config={"detection": {"oscillation_window": 4}})
        # Monotonically increasing — not a cycle
        result = det._detect_oscillation([1.0, 2.0, 3.0, 4.0])
        assert result is None

    def test_score_alternative_normalizes(self):
        det = StagnationDetector(config={"scoring": {"feasibility_weight": 0.6, "novelty_weight": 0.4}})
        score = det._score_alternative({"description": "reuse existing simple proven approach"})
        assert 0.0 <= score <= 1.0


class TestMigrationCanvasConstants:
    def test_day_thresholds_positive(self):
        assert _STALE_SESSION_DAYS > 0
        assert _EOL_URGENCY_DAYS > 0
        assert _STALE_PLAN_DAYS > 0

    def test_confidence_ranges_valid(self):
        assert 0.0 < _STALE_SESSION_BASE_CONF < _STALE_SESSION_MAX_CONF <= 1.0
        assert 0.0 < _EOL_CONF_BASE < _EOL_CONF_MAX <= 1.0
        assert 0.0 < _STALE_PLAN_CONFIDENCE <= 1.0

    def test_increment_values_small(self):
        assert 0.0 < _STALE_SESSION_CONF_PER_DAY < 0.1
        assert 0.0 < _EOL_CONF_MULTIPLIER < 0.1

    def test_promotion_and_priority_thresholds(self):
        assert 0.0 < _PROMOTION_THRESHOLD_DEFAULT <= 1.0
        assert 0.0 < _HIGH_PRIORITY_THRESHOLD <= 1.0
        # High-priority threshold should be at or above promotion threshold
        assert _HIGH_PRIORITY_THRESHOLD >= _PROMOTION_THRESHOLD_DEFAULT

    def test_stale_session_confidence_growth(self):
        # Confidence grows with age but is capped
        age_small = 8
        age_large = 100
        conf_small = min(_STALE_SESSION_BASE_CONF + age_small * _STALE_SESSION_CONF_PER_DAY, _STALE_SESSION_MAX_CONF)
        conf_large = min(_STALE_SESSION_BASE_CONF + age_large * _STALE_SESSION_CONF_PER_DAY, _STALE_SESSION_MAX_CONF)
        assert conf_small <= conf_large
        assert conf_large <= _STALE_SESSION_MAX_CONF

    def test_eol_urgency_growth(self):
        # Closer to EOL → higher urgency, capped at max
        days_far = 80
        days_close = 5
        urg_far = max(_EOL_CONF_BASE, min(_EOL_CONF_MAX, _EOL_CONF_BASE + (_EOL_URGENCY_DAYS - days_far) * _EOL_CONF_MULTIPLIER))
        urg_close = max(_EOL_CONF_BASE, min(_EOL_CONF_MAX, _EOL_CONF_BASE + (_EOL_URGENCY_DAYS - days_close) * _EOL_CONF_MULTIPLIER))
        assert urg_close >= urg_far
        assert urg_close <= _EOL_CONF_MAX
