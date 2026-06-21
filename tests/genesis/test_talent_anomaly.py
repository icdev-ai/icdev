# CUI // SP-CTI
"""Tests for the hiring-surge anomaly-detection helper + threshold wiring in talent.py.

Covers the AI-ify modernization (aiify-rm-15851-phase-5447) that replaced the
hardcoded `threshold: int = 5` surge cut-off in the R20 Talent reflex with a
config-driven, adaptive z-score upper control limit over the competitor posting
distribution.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.talent import (  # noqa: E402
    _compute_surge_threshold,
    _count_by_competitor,
    _detect_surges,
    _DEFAULT_ZSCORE,
    _MIN_SURGE_SAMPLES,
    _SURGE_COUNT_THRESHOLD,
    run,
)


def _sigs(spec: dict) -> list:
    """Build a flat signal list from {competitor: count}."""
    out = []
    for name, n in spec.items():
        out.extend([{"competitor_name": name} for _ in range(n)])
    return out


# ─────────────────────────────────────────────────────────────────
# _count_by_competitor
# ─────────────────────────────────────────────────────────────────

class TestCountByCompetitor:

    def test_tallies_per_competitor(self):
        counts = _count_by_competitor(_sigs({"A": 3, "B": 1}))
        assert counts == {"A": 3, "B": 1}

    def test_missing_name_buckets_unknown(self):
        counts = _count_by_competitor([{}, {"competitor_name": "A"}])
        assert counts == {"unknown": 1, "A": 1}

    def test_empty(self):
        assert _count_by_competitor([]) == {}


# ─────────────────────────────────────────────────────────────────
# _compute_surge_threshold
# ─────────────────────────────────────────────────────────────────

class TestComputeSurgeThreshold:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_surge_count": 7}
        assert _compute_surge_threshold({"A": 100}, cfg) == 7.0

    def test_insufficient_samples_returns_fallback(self):
        # Only 2 competitors but min_samples=3 → static fallback.
        cfg = {"enabled": True, "min_samples": 3, "fallback_surge_count": 5}
        assert _compute_surge_threshold({"A": 10, "B": 1}, cfg) == 5.0

    def test_no_config_uses_module_defaults(self):
        # Empty cfg → enabled, zscore=2.0, min_samples=3, min_abs=5.
        # counts=[10,2,2,2]: mean=4, var=12, std≈3.4641 → 4+2*3.4641≈10.93
        result = _compute_surge_threshold({"A": 10, "B": 2, "C": 2, "D": 2}, {})
        assert result == pytest.approx(10.9, abs=0.1)

    def test_adaptive_upper_control_limit(self):
        # counts=[8,2,2,2,1]: mean=3, var=6.8, std≈2.6077 → 3 + 1*2.6077 ≈ 5.6
        cfg = {"enabled": True, "min_samples": 3, "velocity_threshold_zscore": 1.0,
               "min_absolute_surge": 1}
        result = _compute_surge_threshold({"A": 8, "B": 2, "C": 2, "D": 2, "E": 1}, cfg)
        assert result == pytest.approx(5.6, abs=0.1)

    def test_min_absolute_surge_floor_respected(self):
        # Flat low-volume field: counts all 1 → mean=1, std=0 → threshold=1,
        # but min_absolute_surge=5 floors it to 5.
        cfg = {"enabled": True, "min_samples": 3, "velocity_threshold_zscore": 2.0,
               "min_absolute_surge": 5}
        result = _compute_surge_threshold({"A": 1, "B": 1, "C": 1, "D": 1}, cfg)
        assert result == 5.0

    def test_zero_variance_equals_mean_when_floor_low(self):
        cfg = {"enabled": True, "min_samples": 3, "velocity_threshold_zscore": 2.0,
               "min_absolute_surge": 0}
        # all counts = 4 → mean=4, std=0 → threshold=4
        assert _compute_surge_threshold({"A": 4, "B": 4, "C": 4}, cfg) == 4.0

    def test_default_zscore_constant(self):
        assert _DEFAULT_ZSCORE == 2.0
        assert _MIN_SURGE_SAMPLES == 3
        assert _SURGE_COUNT_THRESHOLD == 5


# ─────────────────────────────────────────────────────────────────
# _detect_surges with float threshold
# ─────────────────────────────────────────────────────────────────

class TestDetectSurges:

    def test_flags_at_or_above_threshold(self):
        surges = _detect_surges(_sigs({"A": 6, "B": 4}), threshold=5)
        assert [s["competitor"] for s in surges] == ["A"]

    def test_float_threshold(self):
        # threshold 5.6 → only counts >= 5.6 (i.e. 6+) flagged
        surges = _detect_surges(_sigs({"A": 6, "B": 5}), threshold=5.6)
        assert [s["competitor"] for s in surges] == ["A"]

    def test_sorted_descending(self):
        surges = _detect_surges(_sigs({"A": 6, "B": 9}), threshold=5)
        assert [s["postings"] for s in surges] == [9, 6]

    def test_default_threshold_is_static_constant(self):
        surges = _detect_surges(_sigs({"A": 5, "B": 4}))
        assert [s["competitor"] for s in surges] == ["A"]


# ─────────────────────────────────────────────────────────────────
# run() wiring
# ─────────────────────────────────────────────────────────────────

class TestRunWiring:

    def _patched(self, signals, config):
        with patch("tools.proposal_genesis.reflexes.talent._get_recent_signals",
                   return_value=signals), \
             patch("tools.proposal_genesis.reflexes.talent.get_connection") as mock_gc:
            mock_gc.return_value = MagicMock()
            return run(config, trust=None)

    def test_run_uses_adaptive_threshold_and_reports_it(self):
        signals = _sigs({"A": 10, "B": 2, "C": 2, "D": 2})
        result = self._patched(signals, {"velocity_threshold_zscore": 2.0})
        assert result["success"] is True
        # threshold ≈ 10.9 (see test_no_config_uses_module_defaults) → only A's 10
        # is < 10.9, so NO surge — demonstrates adaptive behavior vs static 5.
        assert result["details"]["surge_threshold"] == pytest.approx(10.9, abs=0.1)
        assert result["details"]["hiring_surges"] == []

    def test_run_honors_top_level_zscore(self):
        # Lower zscore → lower threshold → A flagged.
        signals = _sigs({"A": 10, "B": 2, "C": 2, "D": 2})
        result = self._patched(signals, {"velocity_threshold_zscore": 0.5,
                                          "anomaly_detection": {"min_absolute_surge": 1}})
        names = [s["competitor"] for s in result["details"]["hiring_surges"]]
        assert "A" in names

    def test_run_backward_compat_static_fallback(self):
        # Detection disabled → fallback_surge_count from legacy talent_surge_threshold.
        signals = _sigs({"A": 6, "B": 1})
        result = self._patched(signals, {
            "talent_surge_threshold": 6,
            "anomaly_detection": {"enabled": False},
        })
        assert result["details"]["surge_threshold"] == 6.0
        assert [s["competitor"] for s in result["details"]["hiring_surges"]] == ["A"]

    def test_run_empty_signals(self):
        result = self._patched([], {})
        assert result["success"] is True
        assert result["details"]["signals_analyzed"] == 0
        assert result["details"]["hiring_surges"] == []
