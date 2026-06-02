# CUI // SP-CTI
"""Tests for tools.genesis.harness.meta_harness — adaptive threshold resolution."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _propose_heuristic_retirements — adaptive default
# ---------------------------------------------------------------------------

class TestProposeHeuristicRetirements:
    def _run(self, precision, hits, **kwargs):
        from tools.genesis.harness.meta_harness import _propose_heuristic_retirements
        return _propose_heuristic_retirements(precision, hits, **kwargs)

    def test_no_proposals_when_precision_above_floor(self):
        """Good precision → empty list regardless of hit counts."""
        proposals = self._run(0.95, {"heuristic_a": 5}, precision_floor=0.70)
        assert proposals == []

    def test_proposals_when_precision_below_floor(self):
        """Low precision + 2+ hits → retirement proposed."""
        proposals = self._run(0.60, {"heuristic_a": 3, "heuristic_b": 1}, precision_floor=0.70)
        names = {p["heuristic_name"] for p in proposals}
        assert "heuristic_a" in names  # 3 hits
        assert "heuristic_b" not in names  # only 1 hit

    def test_sorted_by_hit_count_descending(self):
        hits = {"low": 2, "high": 5, "mid": 3}
        proposals = self._run(0.50, hits, precision_floor=0.70)
        counts = [p["error_case_hits"] for p in proposals]
        assert counts == sorted(counts, reverse=True)

    def test_none_default_resolves_via_adaptive_thresholds(self):
        """When precision_floor is omitted, _get_adaptive_thresholds() is called."""
        with patch(
            "tools.genesis.harness.meta_harness._get_adaptive_thresholds",
            return_value=(0.70, 0.25),
        ) as mock_thresh:
            # precision=0.60 < 0.70 adaptive floor → proposals generated
            proposals = self._run(0.60, {"h": 3})
            mock_thresh.assert_called_once()
            assert any(p["heuristic_name"] == "h" for p in proposals)

    def test_none_default_uses_adaptive_floor_not_hardcoded(self):
        """Adaptive floor of 0.50 means precision=0.55 should not trigger proposals."""
        with patch(
            "tools.genesis.harness.meta_harness._get_adaptive_thresholds",
            return_value=(0.50, 0.25),
        ):
            proposals = self._run(0.55, {"h": 5})
            # 0.55 >= 0.50 adaptive floor → no proposals
            assert proposals == []


# ---------------------------------------------------------------------------
# _propose_constitution_tightening — adaptive default
# ---------------------------------------------------------------------------

class TestProposeConstitutionTightening:
    def _run(self, false_heal_rate, constitution, error_types, **kwargs):
        from tools.genesis.harness.meta_harness import _propose_constitution_tightening
        return _propose_constitution_tightening(false_heal_rate, constitution, error_types, **kwargs)

    def test_no_proposals_when_rate_below_ceiling(self):
        proposals = self._run(0.10, {}, {"restart": 5}, false_heal_ceiling=0.25)
        assert proposals == []

    def test_proposals_when_rate_above_ceiling(self):
        proposals = self._run(0.40, {}, {"restart": 3}, false_heal_ceiling=0.25)
        assert len(proposals) == 1
        assert proposals[0]["resolution_type"] == "restart"

    def test_none_default_resolves_via_adaptive_thresholds(self):
        """When false_heal_ceiling is omitted, _get_adaptive_thresholds() is called."""
        with patch(
            "tools.genesis.harness.meta_harness._get_adaptive_thresholds",
            return_value=(0.70, 0.25),
        ) as mock_thresh:
            # false_heal_rate=0.40 > 0.25 ceiling → proposals generated
            proposals = self._run(0.40, {}, {"restart": 3})
            mock_thresh.assert_called_once()
            assert len(proposals) == 1

    def test_none_default_uses_adaptive_ceiling_not_hardcoded(self):
        """Adaptive ceiling of 0.50 means false_heal_rate=0.40 should not trigger."""
        with patch(
            "tools.genesis.harness.meta_harness._get_adaptive_thresholds",
            return_value=(0.70, 0.50),
        ):
            proposals = self._run(0.40, {}, {"restart": 5})
            assert proposals == []

    def test_proposed_floor_capped_at_0_95(self):
        """min_confidence increment stops at 0.95."""
        constitution = {"rules": [{"applies_to_types": ["restart"], "min_confidence": 0.93}]}
        proposals = self._run(0.40, constitution, {"restart": 5}, false_heal_ceiling=0.25)
        assert proposals[0]["proposed_min_confidence"] == 0.95

    def test_no_proposal_when_fail_count_below_2(self):
        proposals = self._run(0.40, {}, {"restart": 1}, false_heal_ceiling=0.25)
        assert proposals == []


# ---------------------------------------------------------------------------
# _get_adaptive_thresholds — fallback on exception
# ---------------------------------------------------------------------------

class TestGetAdaptiveThresholds:
    def test_returns_static_fallback_when_import_fails(self):
        from tools.genesis.harness.meta_harness import (
            PRECISION_HARD_FLOOR,
            FALSE_HEAL_CEILING,
            _get_adaptive_thresholds,
        )
        with patch.dict("sys.modules", {"tools.genesis.harness.eval_harness": None}):
            pf, fc = _get_adaptive_thresholds()
        assert pf == PRECISION_HARD_FLOOR
        assert fc == FALSE_HEAL_CEILING

    def test_returns_adaptive_values_when_detector_available(self):
        from tools.genesis.harness.meta_harness import _get_adaptive_thresholds

        mock_detector = MagicMock()
        mock_detector.get_thresholds.side_effect = lambda reflex: {
            "oracle_triage": {"precision_min": 0.65},
            "heal": {"false_heal_max": 0.30},
        }[reflex]

        mock_module = MagicMock()
        mock_module._AnomalyDetector = lambda: mock_detector

        with patch.dict("sys.modules", {"tools.genesis.harness.eval_harness": mock_module}):
            pf, fc = _get_adaptive_thresholds()

        assert pf == 0.65
        assert fc == 0.30
