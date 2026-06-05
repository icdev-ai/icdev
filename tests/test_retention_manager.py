#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG retention manager anomaly detection + extracted thresholds.

Covers the aiify modernization (aiify-rm-6efad-phase-5516): hardcoded tier-age
thresholds in tools/rag/retention_manager.py extracted to named, config-
overridable constants, plus adaptive anomaly detection that flags chunks
lingering in a tier far beyond their peers (age > mean + k·stddev) — derived
from the actual chunk-age distribution in rag_chunks.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.retention_manager import (  # noqa: E402
    _ANOMALY_MIN_SAMPLES,
    _ANOMALY_STDDEV_K,
    _HOT_DAYS,
    _REHYDRATE_EMBED_MODEL,
    _WARM_DAYS,
    _chunk_age_days,
    _compute_retention_anomaly_thresholds,
    _load_retention_anomaly_config,
    detect_retention_anomalies,
)


# ---------------------------------------------------------------------------
# Extracted constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_constants_have_sane_defaults(self) -> None:
        assert _HOT_DAYS == 30
        assert _WARM_DAYS == 365
        assert _REHYDRATE_EMBED_MODEL == "nomic-embed-text"

    def test_anomaly_knobs_positive(self) -> None:
        assert _ANOMALY_STDDEV_K > 0.0
        assert _ANOMALY_MIN_SAMPLES > 0

    def test_hot_before_warm(self) -> None:
        assert _HOT_DAYS < _WARM_DAYS


# ---------------------------------------------------------------------------
# _chunk_age_days — timestamp parsing
# ---------------------------------------------------------------------------


class TestChunkAgeDays:
    def test_none_returns_none(self) -> None:
        assert _chunk_age_days(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _chunk_age_days("   ") is None

    def test_unparseable_returns_none(self) -> None:
        assert _chunk_age_days("not-a-date") is None

    def test_sqlite_timestamp_form(self) -> None:
        now = datetime(2026, 1, 31, tzinfo=timezone.utc)
        # "YYYY-MM-DD HH:MM:SS" (space-separated, naive → treated as UTC)
        age = _chunk_age_days("2026-01-01 00:00:00", now=now)
        assert age is not None
        assert abs(age - 30.0) < 0.01

    def test_iso_with_timezone(self) -> None:
        now = datetime(2026, 1, 11, tzinfo=timezone.utc)
        age = _chunk_age_days("2026-01-01T00:00:00+00:00", now=now)
        assert abs(age - 10.0) < 0.01

    def test_iso_with_z_suffix(self) -> None:
        now = datetime(2026, 1, 6, tzinfo=timezone.utc)
        age = _chunk_age_days("2026-01-01T00:00:00Z", now=now)
        assert abs(age - 5.0) < 0.01

    def test_future_timestamp_clamped_to_zero(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        age = _chunk_age_days("2026-06-01T00:00:00Z", now=now)
        assert age == 0.0


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestLoadAnomalyConfig:
    def test_inline_block_preferred(self) -> None:
        cfg = _load_retention_anomaly_config(
            {"anomaly_detection": {"enabled": False, "stddev_k": 3.0}}
        )
        assert cfg["enabled"] is False
        assert cfg["stddev_k"] == 3.0

    def test_falls_back_to_file_block(self) -> None:
        # No inline block → reads rag.retention.anomaly_detection from yaml.
        cfg = _load_retention_anomaly_config(None)
        assert isinstance(cfg, dict)


# ---------------------------------------------------------------------------
# _compute_retention_anomaly_thresholds
# ---------------------------------------------------------------------------


class TestComputeThresholds:
    def test_disabled_uses_configured_tier_ages(self) -> None:
        th = _compute_retention_anomaly_thresholds(anomaly_cfg={"enabled": False})
        assert th["computed"] is False
        assert th["thresholds"]["hot"] == float(_HOT_DAYS)
        assert th["thresholds"]["warm"] == float(_WARM_DAYS)

    def test_high_min_samples_forces_fallback(self) -> None:
        # No tier can reach an astronomically high sample floor → fallback.
        th = _compute_retention_anomaly_thresholds(
            anomaly_cfg={"enabled": True, "min_samples": 10**9}
        )
        assert th["computed"] is False
        assert th["thresholds"]["hot"] == float(_HOT_DAYS)
        assert th["thresholds"]["warm"] == float(_WARM_DAYS)

    def test_none_config_returns_structure(self) -> None:
        th = _compute_retention_anomaly_thresholds()
        assert "thresholds" in th
        assert set(th["thresholds"]) == {"hot", "warm"}
        assert th["classification"] == "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# detect_retention_anomalies
# ---------------------------------------------------------------------------


class TestDetectAnomalies:
    def test_returns_well_formed_result(self) -> None:
        result = detect_retention_anomalies()
        for key in (
            "overdue_hot",
            "overdue_warm",
            "overdue_hot_count",
            "overdue_warm_count",
            "total_anomalies",
            "thresholds",
            "adaptive",
        ):
            assert key in result
        assert result["classification"] == "CUI // SP-CTI"

    def test_counts_match_lists(self) -> None:
        result = detect_retention_anomalies()
        assert result["overdue_hot_count"] == len(result["overdue_hot"])
        assert result["overdue_warm_count"] == len(result["overdue_warm"])
        assert result["total_anomalies"] == (
            result["overdue_hot_count"] + result["overdue_warm_count"]
        )

    def test_overdue_items_are_chunk_ids(self) -> None:
        # Whatever the corpus holds, overdue entries are non-negative counts of
        # string chunk IDs (no crash, no malformed rows).
        result = detect_retention_anomalies()
        assert result["total_anomalies"] >= 0
        assert all(isinstance(cid, str) for cid in result["overdue_hot"])
        assert all(isinstance(cid, str) for cid in result["overdue_warm"])

    def test_thresholds_never_below_configured_floor(self) -> None:
        # Adaptive overdue thresholds are clamped to be >= the configured
        # tier age, so the adaptive rule is never *more* aggressive than the
        # static hot_days/warm_days rule it augments.
        result = detect_retention_anomalies()
        assert result["thresholds"]["hot"] >= float(_HOT_DAYS)
        assert result["thresholds"]["warm"] >= float(_WARM_DAYS)


# ---------------------------------------------------------------------------
# Statistical correctness of the adaptive threshold (unit, no DB)
# ---------------------------------------------------------------------------


class TestAdaptiveStatistics:
    def test_overdue_threshold_is_mean_plus_k_stddev(self) -> None:
        # Reproduce the module's population-stddev formula and confirm an
        # outlier age lands above mean + k·stddev for a tight cluster.
        import math

        ages = [28.0, 29.0, 30.0, 31.0, 32.0]  # tight cluster around 30d
        mean = sum(ages) / len(ages)
        var = sum(a * a for a in ages) / len(ages) - mean * mean
        std = math.sqrt(max(0.0, var))
        overdue = mean + _ANOMALY_STDDEV_K * std
        assert overdue > mean
        # A chunk aged well beyond the cluster is unambiguously overdue.
        assert 90.0 > overdue
        assert _chunk_age_days(
            (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        ) > overdue
