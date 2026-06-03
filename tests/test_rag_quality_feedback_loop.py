#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for quality_feedback_loop anomaly detection + extracted thresholds.

Covers the aiify modernization (aiify-rm-6efad-phase-5497): hardcoded
thresholds in tools/rag/quality_feedback_loop.py extracted to named,
config-overridable constants and an adaptive anomaly-detection floor derived
from historical ft_quality_snapshots (mean − k·stddev), with safe fallback to
module floors when history is sparse.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag import quality_feedback_loop as qfl  # noqa: E402


# ---------------------------------------------------------------------------
# Extracted constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_constants_have_sane_defaults(self) -> None:
        assert qfl._MAX_AUTO_PAIRS_PER_CYCLE == 50
        assert qfl._MIN_PAIRS_PER_SOURCE == 5
        assert qfl._ID_HEX_LEN == 12
        assert qfl._ANOMALY_STDDEV_K > 0

    def test_default_config_uses_constant(self) -> None:
        assert qfl.DEFAULT_CONFIG["max_auto_pairs_per_cycle"] == qfl._MAX_AUTO_PAIRS_PER_CYCLE

    def test_metric_floors_in_unit_range(self) -> None:
        for metric, floor in qfl._METRIC_ANOMALY_FLOORS.items():
            assert 0.0 <= floor <= 1.0, metric

    def test_gen_id_hex_length(self) -> None:
        gid = qfl._gen_id("cycle")
        # "cycle-" + _ID_HEX_LEN hex chars
        assert gid.startswith("cycle-")
        assert len(gid.split("-", 1)[1]) == qfl._ID_HEX_LEN


# ---------------------------------------------------------------------------
# Adaptive threshold computation (fallback paths — no DB rows required)
# ---------------------------------------------------------------------------


class TestComputeThresholds:
    def test_disabled_returns_fallback_floors(self) -> None:
        th = qfl._compute_feedback_anomaly_thresholds({"enabled": False})
        assert th["computed"] is False
        assert th["floors"]["ndcg"] == qfl._METRIC_ANOMALY_FLOORS["ndcg"]

    def test_custom_fallback_floors_override_defaults(self) -> None:
        th = qfl._compute_feedback_anomaly_thresholds(
            {"enabled": False, "fallback_floors": {"ndcg": 0.45}}
        )
        assert th["floors"]["ndcg"] == 0.45
        # untouched metrics keep module defaults
        assert th["floors"]["mrr"] == qfl._METRIC_ANOMALY_FLOORS["mrr"]

    def test_high_min_samples_falls_back(self) -> None:
        # min_samples far above any test history → no adaptive computation
        th = qfl._compute_feedback_anomaly_thresholds({"min_samples": 10_000_000})
        assert th["computed"] is False
        assert set(th["floors"]) == set(qfl._METRIC_ANOMALY_FLOORS)

    def test_floors_present_for_all_metrics(self) -> None:
        th = qfl._compute_feedback_anomaly_thresholds({})
        for metric in qfl._METRIC_ANOMALY_FLOORS:
            assert metric in th["floors"]


# ---------------------------------------------------------------------------
# flag_quality_anomaly
# ---------------------------------------------------------------------------


class TestFlagAnomaly:
    _THRESHOLDS = {"floors": {"ndcg": 0.30, "mrr": 0.30, "avg_retrieval_score": 0.30}}

    def test_clean_when_above_floor(self) -> None:
        res = qfl.flag_quality_anomaly({"ndcg": 0.8, "mrr": 0.7}, self._THRESHOLDS)
        assert res["anomalous"] is False
        assert res["reasons"] == []

    def test_flags_metric_below_floor(self) -> None:
        res = qfl.flag_quality_anomaly({"ndcg": 0.1, "mrr": 0.7}, self._THRESHOLDS)
        assert res["anomalous"] is True
        assert any("ndcg" in r for r in res["reasons"])

    def test_boundary_equal_floor_not_anomalous(self) -> None:
        res = qfl.flag_quality_anomaly({"ndcg": 0.30}, self._THRESHOLDS)
        assert res["anomalous"] is False

    def test_missing_metric_ignored(self) -> None:
        res = qfl.flag_quality_anomaly({"unrelated": 0.0}, self._THRESHOLDS)
        assert res["anomalous"] is False

    def test_non_numeric_metric_ignored(self) -> None:
        res = qfl.flag_quality_anomaly({"ndcg": None, "mrr": "n/a"}, self._THRESHOLDS)
        assert res["anomalous"] is False

    def test_multiple_metrics_below_floor(self) -> None:
        res = qfl.flag_quality_anomaly(
            {"ndcg": 0.1, "mrr": 0.1, "avg_retrieval_score": 0.1}, self._THRESHOLDS
        )
        assert res["anomalous"] is True
        assert len(res["reasons"]) == 3

    def test_default_thresholds_loaded_when_none(self) -> None:
        # No thresholds passed → loads from config/fallback; should not raise.
        res = qfl.flag_quality_anomaly({"ndcg": 0.9})
        assert "anomalous" in res
        assert "floors" in res
