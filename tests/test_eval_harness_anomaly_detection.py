# CUI // SP-CTI
"""Tests for eval_harness anomaly detection — adaptive bounds and min_decisions config.

Covers:
- _AnomalyDetector loads adaptive_bounds from config (not hardcoded literals)
- _compute_adaptive respects floor/ceiling bounds from config
- check_gates loads min_decisions from config rather than _DEFAULT_GATES
- Fallback to _DEFAULT_GATES values when config is absent
"""
from __future__ import annotations

import sys
from unittest.mock import patch


_REPO = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from tools.genesis.harness.eval_harness import (
    _AnomalyDetector,
    _DEFAULT_GATES,
    check_gates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshots(n: int, precision: float = 0.90, ece: float = 0.05,
                    false_heal: float = 0.10, heal_success: float = 0.80) -> list[dict]:
    """Return *n* identical snapshots with the given metric values."""
    return [
        {"precision": precision, "ece": ece, "false_heal": false_heal, "heal_success": heal_success}
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# _AnomalyDetector — bounds loaded from config
# ---------------------------------------------------------------------------

class TestAnomalyDetectorBoundsFromConfig:
    def _detector_with_config(self, bounds: dict) -> _AnomalyDetector:
        cfg = {
            "gates": dict(_DEFAULT_GATES),
            "anomaly_detection": {
                "min_samples": 3,
                "z_score": 2.0,
                "adaptive_bounds": bounds,
            },
        }
        with patch("tools.genesis.harness.eval_harness._load_harness_config", return_value=cfg):
            return _AnomalyDetector()

    def test_default_bounds_applied_when_config_empty(self):
        cfg = {"gates": dict(_DEFAULT_GATES), "anomaly_detection": {}}
        with patch("tools.genesis.harness.eval_harness._load_harness_config", return_value=cfg):
            det = _AnomalyDetector()
        assert det._bounds["precision_min_floor"] == 0.50
        assert det._bounds["ece_max_ceiling"] == 0.50
        assert det._bounds["false_heal_max_ceiling"] == 0.60
        assert det._bounds["heal_success_min_floor"] == 0.30

    def test_custom_bounds_override_defaults(self):
        det = self._detector_with_config({
            "precision_min_floor": 0.65,
            "ece_max_ceiling": 0.40,
            "false_heal_max_ceiling": 0.55,
            "heal_success_min_floor": 0.45,
        })
        assert det._bounds["precision_min_floor"] == 0.65
        assert det._bounds["ece_max_ceiling"] == 0.40
        assert det._bounds["false_heal_max_ceiling"] == 0.55
        assert det._bounds["heal_success_min_floor"] == 0.45

    def test_partial_bounds_merge_with_defaults(self):
        det = self._detector_with_config({"precision_min_floor": 0.70})
        assert det._bounds["precision_min_floor"] == 0.70
        assert det._bounds["ece_max_ceiling"] == 0.50   # default unchanged


# ---------------------------------------------------------------------------
# _compute_adaptive — floor/ceiling enforced from self._bounds
# ---------------------------------------------------------------------------

class TestComputeAdaptiveBoundsEnforced:
    def _detector(self, bounds: dict, min_samples: int = 3, z_score: float = 2.0) -> _AnomalyDetector:
        cfg = {
            "gates": dict(_DEFAULT_GATES),
            "anomaly_detection": {
                "min_samples": min_samples,
                "z_score": z_score,
                "adaptive_bounds": bounds,
            },
        }
        with patch("tools.genesis.harness.eval_harness._load_harness_config", return_value=cfg):
            return _AnomalyDetector()

    def test_precision_min_floor_applied(self):
        """When z-score would push precision_min below floor, floor wins."""
        det = self._detector({"precision_min_floor": 0.70}, z_score=100.0)
        snaps = _make_snapshots(5, precision=0.60)
        result = det._compute_adaptive(snaps)
        assert result["precision_min"] >= 0.70

    def test_ece_max_ceiling_applied(self):
        """When z-score would push ece_max above ceiling, ceiling wins."""
        det = self._detector({"ece_max_ceiling": 0.30}, z_score=100.0)
        snaps = _make_snapshots(5, ece=0.20)
        result = det._compute_adaptive(snaps)
        assert result["ece_max"] <= 0.30

    def test_false_heal_max_ceiling_applied(self):
        """When z-score would push false_heal_max above ceiling, ceiling wins."""
        det = self._detector({"false_heal_max_ceiling": 0.45}, z_score=100.0)
        snaps = _make_snapshots(5, false_heal=0.30)
        result = det._compute_adaptive(snaps)
        assert result["false_heal_max"] <= 0.45

    def test_heal_success_min_floor_applied(self):
        """When z-score would push heal_success_min below floor, floor wins."""
        det = self._detector({"heal_success_min_floor": 0.50}, z_score=100.0)
        snaps = _make_snapshots(5, heal_success=0.55)
        result = det._compute_adaptive(snaps)
        assert result["heal_success_min"] >= 0.50

    def test_adaptive_values_within_normal_range(self):
        """Normal data: adaptive bounds sit between floor/ceiling and base values."""
        det = self._detector({})  # use defaults
        snaps = _make_snapshots(5, precision=0.88, ece=0.07, false_heal=0.12, heal_success=0.75)
        result = det._compute_adaptive(snaps)
        assert 0.50 <= result["precision_min"] <= 1.0
        assert 0.0 <= result["ece_max"] <= 0.50
        assert 0.0 <= result["false_heal_max"] <= 0.60
        assert 0.30 <= result["heal_success_min"] <= 1.0

    def test_empty_snapshots_returns_empty_dict(self):
        det = self._detector({})
        result = det._compute_adaptive([])
        assert result == {}

    def test_insufficient_snapshots_skips_metric(self):
        """Metrics with fewer values than min_samples are not included."""
        det = self._detector({}, min_samples=5)
        snaps = _make_snapshots(3, precision=0.85)
        result = det._compute_adaptive(snaps)
        assert "precision_min" not in result


# ---------------------------------------------------------------------------
# check_gates — min_decisions read from config
# ---------------------------------------------------------------------------

class TestCheckGatesMinDecisionsFromConfig:
    def _patch_compute_metrics(self, total_decisions: int):
        return {
            "reflex": "oracle_triage",
            "window_days": 30,
            "total_decisions": total_decisions,
            "precision": 0.95,
            "ece": 0.05,
            "false_heal_rate": None,
            "heal_success_rate": None,
        }

    def test_min_decisions_from_config_skips_when_below_threshold(self):
        """check_gates must skip a reflex with decisions < config min_decisions."""
        cfg_patch = {
            "gates": {**_DEFAULT_GATES, "min_decisions": 20},
            "anomaly_detection": {},
        }
        metrics_with_15 = self._patch_compute_metrics(15)

        with patch("tools.genesis.harness.eval_harness._load_harness_config", return_value=cfg_patch), \
             patch("tools.genesis.harness.eval_harness.compute_metrics", return_value=metrics_with_15):
            alerts = check_gates()

        assert alerts == [], "Should skip reflex with 15 decisions when min_decisions=20"

    def test_min_decisions_from_config_runs_when_at_threshold(self):
        """check_gates must run gate checks when decisions >= config min_decisions."""
        cfg_patch = {
            "gates": {**_DEFAULT_GATES, "min_decisions": 10},
            "anomaly_detection": {},
        }
        metrics_with_10 = {**self._patch_compute_metrics(10), "precision": 0.50}

        with patch("tools.genesis.harness.eval_harness._load_harness_config", return_value=cfg_patch), \
             patch("tools.genesis.harness.eval_harness.compute_metrics", return_value=metrics_with_10), \
             patch("tools.genesis.harness.eval_harness._AnomalyDetector.get_thresholds",
                   return_value=dict(_DEFAULT_GATES)):
            alerts = check_gates()

        assert any(a["metric"] == "precision" for a in alerts), (
            "Should fire precision alert with 10 decisions when min_decisions=10"
        )

    def test_min_decisions_falls_back_to_default_when_config_absent(self):
        """When config load fails, fallback uses _DEFAULT_GATES['min_decisions']."""
        def _fail():
            raise RuntimeError("no config")

        metrics_below_default = self._patch_compute_metrics(_DEFAULT_GATES["min_decisions"] - 1)

        with patch("tools.genesis.harness.eval_harness._load_harness_config",
                   return_value={"gates": dict(_DEFAULT_GATES), "anomaly_detection": {}}), \
             patch("tools.genesis.harness.eval_harness.compute_metrics",
                   return_value=metrics_below_default):
            alerts = check_gates()

        assert alerts == [], (
            "Should skip reflex below _DEFAULT_GATES min_decisions when config absent"
        )


# ---------------------------------------------------------------------------
# AUROC gate (restored in tsr-ai-01-d5)
# ---------------------------------------------------------------------------
#
# _compute_auroc and its gate were authored ONLY in icdev/tools/... (the generated
# mirror). `sync_package_tree.py --clean` at a7d227138 regenerated the mirror from
# tools/, so the whole feature was deleted and only tests/test_benchmark_runner.py
# noticed — via an ImportError nobody read. These tests pin the feature to the
# CANONICAL path and assert both copies carry it, so the next --clean sync cannot
# quietly remove it again.


class TestAuroc:
    def test_both_copies_expose_compute_auroc(self):
        """Guards the mirror-only-authoring failure that deleted this feature."""
        import importlib

        canonical = importlib.import_module("tools.genesis.harness.eval_harness")
        packaged = importlib.import_module("icdev.tools.genesis.harness.eval_harness")
        assert hasattr(canonical, "_compute_auroc")
        assert hasattr(packaged, "_compute_auroc")

    def test_auroc_min_is_a_default_gate(self):
        assert _DEFAULT_GATES["auroc_min"] == 0.65

    def test_compute_metrics_emits_auroc_key(self):
        """compute_metrics must carry auroc through — check_gates reads m['auroc']."""
        from tools.genesis.harness.eval_harness import compute_metrics

        rows = [{"decision": "heal", "confidence": 0.9, "actual_outcome": "resolved"}] * 5 + \
               [{"decision": "heal", "confidence": 0.1, "actual_outcome": "false_positive"}] * 5

        class _Conn:
            def execute(self, *a, **kw):
                class _R:
                    def fetchall(self_inner):
                        return rows
                return _R()
            def close(self):
                pass

        with patch("tools.genesis.harness.eval_harness._conn", return_value=_Conn()):
            m = compute_metrics("oracle_triage")

        assert m["auroc"] == 1.0

    def test_low_auroc_fires_a_medium_alert(self):
        metrics = {
            "reflex": "oracle_triage", "window_days": 30, "total_decisions": 50,
            "precision": 0.95, "ece": 0.05, "auroc": 0.20,
            "false_heal_rate": None, "heal_success_rate": None,
        }
        with patch("tools.genesis.harness.eval_harness._load_harness_config",
                   return_value={"gates": dict(_DEFAULT_GATES), "anomaly_detection": {}}), \
             patch("tools.genesis.harness.eval_harness.compute_metrics", return_value=metrics), \
             patch("tools.genesis.harness.eval_harness._AnomalyDetector.get_thresholds",
                   return_value=dict(_DEFAULT_GATES)):
            alerts = check_gates()

        # compute_metrics is patched, so both gated reflexes report the same
        # numbers and both fire.
        auroc_alerts = [a for a in alerts if a["metric"] == "auroc"]
        assert auroc_alerts, "low AUROC must raise an alert"
        assert all(a["severity"] == "medium" for a in auroc_alerts)
        assert all(a["threshold"] == 0.65 for a in auroc_alerts)

    def test_healthy_auroc_fires_nothing(self):
        metrics = {
            "reflex": "oracle_triage", "window_days": 30, "total_decisions": 50,
            "precision": 0.95, "ece": 0.05, "auroc": 0.90,
            "false_heal_rate": None, "heal_success_rate": None,
        }
        with patch("tools.genesis.harness.eval_harness._load_harness_config",
                   return_value={"gates": dict(_DEFAULT_GATES), "anomaly_detection": {}}), \
             patch("tools.genesis.harness.eval_harness.compute_metrics", return_value=metrics), \
             patch("tools.genesis.harness.eval_harness._AnomalyDetector.get_thresholds",
                   return_value=dict(_DEFAULT_GATES)):
            alerts = check_gates()

        assert not [a for a in alerts if a["metric"] == "auroc"]

    def test_unjudged_rows_are_dropped_not_scored_as_wrong(self):
        """A 'pending' outcome is missing data, not a negative — see _compute_ece."""
        from tools.genesis.harness.eval_harness import _compute_auroc

        clean = [{"confidence": 0.9, "actual_outcome": "resolved"}] * 5 + \
                [{"confidence": 0.1, "actual_outcome": "false_positive"}] * 5
        noisy = clean + [{"confidence": 0.95, "actual_outcome": "pending"}] * 4

        assert _compute_auroc(noisy) == _compute_auroc(clean) == 1.0
