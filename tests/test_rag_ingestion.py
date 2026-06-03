#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG ingestion manager and source registry (D-RAG-9)."""

from __future__ import annotations

from unittest import mock

from tools.rag.source_registry import SOURCE_REGISTRY, get_source_config
from tools.rag.chunker import chunk_content


class TestSourceRegistry:
    def test_registry_not_empty(self):
        assert len(SOURCE_REGISTRY) > 0

    def test_innovation_signals_registered(self):
        cfg = get_source_config("innovation_signals")
        assert cfg  # Non-empty dict
        assert "table" in cfg
        assert "content_cols" in cfg

    def test_compliance_artifacts_registered(self):
        cfg = get_source_config("compliance_artifacts")
        assert cfg

    def test_memory_entries_registered(self):
        cfg = get_source_config("memory_entries")
        assert cfg

    def test_unknown_source_returns_empty(self):
        cfg = get_source_config("nonexistent_source_type")
        assert cfg == {}

    def test_all_sources_have_table(self):
        for name, cfg in SOURCE_REGISTRY.items():
            assert "table" in cfg, f"Source {name} missing 'table' field"

    def test_all_sources_have_content_cols(self):
        for name, cfg in SOURCE_REGISTRY.items():
            assert "content_cols" in cfg, f"Source {name} missing 'content_cols' field"
            assert isinstance(cfg["content_cols"], list), f"Source {name} content_cols not a list"
            assert len(cfg["content_cols"]) > 0, f"Source {name} has empty content_cols"

    def test_source_types_match_dashboard(self):
        """Key source types used in dashboard should exist."""
        expected = [
            "innovation_signals",
            "creative_pain_points",
            "creative_feature_gaps",
            "compliance_artifacts",
            "memory_entries",
            "research_challenges",
        ]
        for src in expected:
            assert src in SOURCE_REGISTRY, f"Missing source type: {src}"

    def test_all_sources_have_mode(self):
        for name, cfg in SOURCE_REGISTRY.items():
            assert "mode" in cfg, f"Source {name} missing 'mode'"
            assert cfg["mode"] in ("realtime", "batch"), f"Source {name} has invalid mode: {cfg['mode']}"


class TestIngestionManager:
    def test_import(self):
        """Ingestion manager should be importable."""
        from tools.rag import ingestion_manager

        assert ingestion_manager is not None

    def test_get_status(self):
        from tools.rag.ingestion_manager import get_status

        status = get_status()
        assert isinstance(status, dict)
        assert "total_chunks" in status
        assert "registered_sources" in status
        assert "vector_store_backend" in status

    def test_get_realtime_sources(self):
        from tools.rag.ingestion_manager import get_realtime_sources

        sources = get_realtime_sources()
        assert isinstance(sources, list)
        assert len(sources) > 0

    def test_get_batch_sources(self):
        from tools.rag.ingestion_manager import get_batch_sources

        sources = get_batch_sources()
        assert isinstance(sources, list)


class TestChunkerIntegration:
    """Test chunking with realistic source content."""

    def test_chunk_innovation_signal(self):
        content = "New zero-trust framework detected in NIST 800-207 supplement. " * 10
        chunks = chunk_content(
            content,
            source_type="innovation_signals",
            source_id="42",
            source_table="innovation_signals",
        )
        assert len(chunks) >= 1
        assert chunks[0].source_type == "innovation_signals"

    def test_chunk_compliance_artifact(self):
        content = "AC-2 Account Management: The organization manages accounts in accordance with the policy."
        chunks = chunk_content(
            content,
            source_type="compliance_artifacts",
            source_id="AC-2",
        )
        assert len(chunks) == 1
        assert "AC-2" in chunks[0].content

    def test_chunk_very_long_content(self):
        """Stress test with very long content."""
        content = "Word " * 10000  # ~50000 chars, ~12500 tokens
        chunks = chunk_content(
            content,
            source_type="test",
            chunk_config={"chunk_size_tokens": 500, "overlap_pct": 0.10},
        )
        assert len(chunks) > 5
        # All chunks should have same total_chunks
        assert all(c.total_chunks == len(chunks) for c in chunks)
        # All chunk indices should be sequential
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))


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
    """_load_anomaly_cfg reads ingestion_manager.anomaly_detection from rag_config.yaml."""

    def test_returns_dict(self):
        from tools.rag.ingestion_manager import _load_anomaly_cfg

        cfg = _load_anomaly_cfg()
        assert isinstance(cfg, dict)

    def test_has_expected_keys(self):
        """The shipped config defines the adaptive-calibration knobs."""
        from tools.rag.ingestion_manager import _load_anomaly_cfg

        cfg = _load_anomaly_cfg()
        # Config section is present in args/rag_config.yaml; if loaded, keys exist.
        if cfg:
            assert "enabled" in cfg
            assert "min_samples" in cfg
            assert "adaptive_bounds" in cfg


class TestComputeIngestionThresholds:
    """_compute_ingestion_thresholds — anomaly-detection calibration of batch/skip thresholds."""

    def test_always_returns_required_keys(self):
        from tools.rag.ingestion_manager import _compute_ingestion_thresholds

        result = _compute_ingestion_thresholds({})
        assert "embed_batch_size" in result
        assert "skip_rate_anomaly" in result
        assert "computed" in result

    def test_disabled_returns_fallback(self):
        from tools.rag import ingestion_manager as im

        result = im._compute_ingestion_thresholds({"enabled": False})
        assert result["computed"] is False
        assert result["embed_batch_size"] == im._EMBED_BATCH_SIZE
        assert result["skip_rate_anomaly"] == im._SKIP_RATE_ANOMALY

    def test_disabled_respects_fallback_overrides(self):
        from tools.rag.ingestion_manager import _compute_ingestion_thresholds

        result = _compute_ingestion_thresholds(
            {"enabled": False, "fallback_embed_batch": 7, "fallback_skip_rate_anomaly": 0.88}
        )
        assert result["embed_batch_size"] == 7
        assert result["skip_rate_anomaly"] == 0.88

    def test_below_min_samples_falls_back(self):
        from tools.rag import ingestion_manager as im

        # Only 3 historical rows, below default min_samples=30 → fallback.
        fake = _FakeConn((50.0, 0.40, 3))
        with mock.patch.object(im, "get_connection", return_value=fake):
            result = im._compute_ingestion_thresholds({"enabled": True, "min_samples": 30})
        assert result["computed"] is False
        assert result["embed_batch_size"] == im._EMBED_BATCH_SIZE

    def test_computed_within_bounds(self):
        from tools.rag import ingestion_manager as im

        # avg_created=200 exceeds batch_ceil; skip_rate=0.99 exceeds skip_ceil → both clamped.
        fake = _FakeConn((200.0, 0.99, 100))
        cfg = {
            "enabled": True,
            "min_samples": 10,
            "adaptive_bounds": {
                "batch_floor": 5,
                "batch_ceil": 100,
                "skip_floor": 0.50,
                "skip_ceil": 0.99,
            },
        }
        with mock.patch.object(im, "get_connection", return_value=fake):
            result = im._compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert 5 <= result["embed_batch_size"] <= 100
        assert 0.50 <= result["skip_rate_anomaly"] <= 0.99
        assert result["n_samples"] == 100

    def test_computed_scales_batch_to_throughput(self):
        from tools.rag import ingestion_manager as im

        # avg_created=42 → batch sizes toward observed throughput, within bounds.
        fake = _FakeConn((42.0, 0.30, 60))
        cfg = {"enabled": True, "min_samples": 10}
        with mock.patch.object(im, "get_connection", return_value=fake):
            result = im._compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert result["embed_batch_size"] == 42
        assert result["avg_chunks_created"] == 42.0

    def test_db_error_falls_back(self):
        from tools.rag import ingestion_manager as im

        def _boom():
            raise RuntimeError("db unavailable")

        with mock.patch.object(im, "get_connection", side_effect=_boom):
            result = im._compute_ingestion_thresholds({"enabled": True, "min_samples": 10})
        assert result["computed"] is False
        assert result["embed_batch_size"] == im._EMBED_BATCH_SIZE
