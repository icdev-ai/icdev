#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG ingestion manager and source registry (D-RAG-9)."""

from __future__ import annotations


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


class TestAnomalyDetectionThresholds:
    """Adaptive ingestion thresholds (anomaly_detection) — replaces hardcoded constants."""

    def test_load_anomaly_cfg_returns_dict(self):
        from tools.rag.ingestion_manager import _load_anomaly_cfg

        cfg = _load_anomaly_cfg()
        assert isinstance(cfg, dict)

    def test_disabled_returns_fallbacks(self):
        from tools.rag.ingestion_manager import _compute_ingestion_thresholds

        result = _compute_ingestion_thresholds({"enabled": False})
        assert result["computed"] is False
        assert result["embed_batch_size"] == 20
        assert result["skip_rate_anomaly"] == 0.95

    def test_disabled_respects_custom_fallbacks(self):
        from tools.rag.ingestion_manager import _compute_ingestion_thresholds

        result = _compute_ingestion_thresholds(
            {"enabled": False, "fallback_embed_batch": 8, "fallback_skip_rate_anomaly": 0.80}
        )
        assert result["embed_batch_size"] == 8
        assert result["skip_rate_anomaly"] == 0.80

    def test_high_min_samples_falls_back(self):
        """With an unreachable min_samples, the historical query can't qualify -> fallback."""
        from tools.rag.ingestion_manager import _compute_ingestion_thresholds

        result = _compute_ingestion_thresholds({"enabled": True, "min_samples": 10**9})
        assert result["computed"] is False
        assert result["embed_batch_size"] == 20
        assert result["skip_rate_anomaly"] == 0.95

    def test_result_shape_is_complete(self):
        from tools.rag.ingestion_manager import _compute_ingestion_thresholds

        result = _compute_ingestion_thresholds({"enabled": False})
        for key in ("embed_batch_size", "skip_rate_anomaly", "computed"):
            assert key in result
        assert isinstance(result["embed_batch_size"], int)
        assert isinstance(result["skip_rate_anomaly"], float)

    def test_none_cfg_uses_module_defaults(self):
        from tools.rag.ingestion_manager import _compute_ingestion_thresholds

        result = _compute_ingestion_thresholds(None)
        # min_samples default (30) rarely met in a fresh test DB -> fallback path
        assert result["embed_batch_size"] >= 5
        assert 0.0 < result["skip_rate_anomaly"] <= 0.99

    def test_embed_chunks_accepts_batch_size(self):
        """_embed_chunks must honour the adaptive batch_size param (no provider = 0)."""
        from tools.rag.ingestion_manager import _embed_chunks

        assert _embed_chunks([], None, batch_size=7) == 0


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
