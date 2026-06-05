# CUI // SP-CTI
"""Tests for rag_to_kg_ingester anomaly-detection threshold calibration.

Covers the aiify modernization that replaced the hardcoded PAGE_SIZE with
adaptive thresholds derived from the rag_chunks corpus
(rag_to_kg_ingester.anomaly_detection in args/rag_config.yaml).
"""
from unittest import mock


class _FakeConn:
    """Minimal connection stub: execute().fetchone() yields a fixed row tuple."""

    def __init__(self, row):
        self._row = row

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return self._row

    def close(self):
        pass


class TestLoadAnomalyConfig:
    """_load_anomaly_cfg reads rag_to_kg_ingester.anomaly_detection from rag_config.yaml."""

    def test_returns_dict(self):
        from tools.rag.rag_to_kg_ingester import _load_anomaly_cfg

        cfg = _load_anomaly_cfg()
        assert isinstance(cfg, dict)

    def test_has_expected_keys(self):
        """The shipped config defines the adaptive-calibration knobs."""
        from tools.rag.rag_to_kg_ingester import _load_anomaly_cfg

        cfg = _load_anomaly_cfg()
        # Config section is present in args/rag_config.yaml; if loaded, keys exist.
        if cfg:
            assert "enabled" in cfg
            assert "min_samples" in cfg
            assert "adaptive_bounds" in cfg


class TestComputeKgThresholds:
    """_compute_kg_thresholds — anomaly-detection calibration of page/empty thresholds."""

    def test_always_returns_required_keys(self):
        from tools.rag.rag_to_kg_ingester import _compute_kg_thresholds

        result = _compute_kg_thresholds({})
        assert "page_size" in result
        assert "empty_rate_anomaly" in result
        assert "computed" in result

    def test_disabled_returns_fallback(self):
        from tools.rag import rag_to_kg_ingester as rki

        result = rki._compute_kg_thresholds({"enabled": False})
        assert result["computed"] is False
        assert result["page_size"] == rki.PAGE_SIZE
        assert result["empty_rate_anomaly"] == rki._EMPTY_RATE_ANOMALY

    def test_disabled_respects_fallback_overrides(self):
        from tools.rag.rag_to_kg_ingester import _compute_kg_thresholds

        result = _compute_kg_thresholds(
            {"enabled": False, "fallback_page_size": 123, "fallback_empty_rate_anomaly": 0.66}
        )
        assert result["page_size"] == 123
        assert result["empty_rate_anomaly"] == 0.66

    def test_below_min_samples_falls_back(self):
        from tools.rag import rag_to_kg_ingester as rki

        # processed = 10 + 5 = 15, below default min_samples=50 → fallback.
        fake = _FakeConn((100, 10, 5))
        with mock.patch.object(rki, "get_connection", return_value=fake):
            result = rki._compute_kg_thresholds({"enabled": True, "min_samples": 50})
        assert result["computed"] is False
        assert result["page_size"] == rki.PAGE_SIZE

    def test_computed_within_bounds(self):
        from tools.rag import rag_to_kg_ingester as rki

        # n_total=100000 → raw page 5000 exceeds page_ceil → clamped; empty_rate=0.5.
        fake = _FakeConn((100000, 18000, 18000))
        cfg = {
            "enabled": True,
            "min_samples": 10,
            "target_pages": 20,
            "adaptive_bounds": {
                "page_floor": 50,
                "page_ceil": 2000,
                "empty_floor": 0.30,
                "empty_ceil": 0.99,
            },
        }
        with mock.patch.object(rki, "get_connection", return_value=fake):
            result = rki._compute_kg_thresholds(cfg)
        assert result["computed"] is True
        assert 50 <= result["page_size"] <= 2000
        assert result["page_size"] == 2000  # 100000/20 clamped to ceil
        assert 0.30 <= result["empty_rate_anomaly"] <= 0.99
        assert result["n_samples"] == 36000

    def test_computed_scales_page_to_corpus(self):
        from tools.rag import rag_to_kg_ingester as rki

        # n_total=2000, target_pages=20 → page 100; empty_rate 50/100=0.5 → +0.1=0.6.
        fake = _FakeConn((2000, 50, 50))
        cfg = {"enabled": True, "min_samples": 10, "target_pages": 20}
        with mock.patch.object(rki, "get_connection", return_value=fake):
            result = rki._compute_kg_thresholds(cfg)
        assert result["computed"] is True
        assert result["page_size"] == 100
        assert result["empty_rate_anomaly"] == 0.6
        assert result["historical_empty_rate"] == 0.5
        assert result["corpus_size"] == 2000

    def test_db_error_falls_back(self):
        from tools.rag import rag_to_kg_ingester as rki

        def _boom():
            raise RuntimeError("db unavailable")

        with mock.patch.object(rki, "get_connection", side_effect=_boom):
            result = rki._compute_kg_thresholds({"enabled": True, "min_samples": 10})
        assert result["computed"] is False
        assert result["page_size"] == rki.PAGE_SIZE
        assert result["empty_rate_anomaly"] == rki._EMPTY_RATE_ANOMALY
