# CUI // SP-CTI
"""Tests for anomaly-detection helpers in RAG ingestion_manager.

Covers the adaptive threshold calibration (_compute_ingestion_thresholds) that
replaces the previously-hardcoded embedding batch size, plus the module-level
fallback constants.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.rag.ingestion_manager import (
    _compute_ingestion_thresholds,
    _load_anomaly_cfg,
    _embed_chunks,
    _EMBED_BATCH_SIZE,
    _DB_BUSY_TIMEOUT_MS,
    _SKIP_RATE_ANOMALY,
)


def _mock_conn(row_dict):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row_dict
    return conn


# ─────────────────────────────────────────────────────────────────
# Module-level fallback constants
# ─────────────────────────────────────────────────────────────────

class TestIngestionConstants:
    def test_all_sane(self):
        assert _EMBED_BATCH_SIZE > 0
        assert _DB_BUSY_TIMEOUT_MS > 0
        assert 0.0 < _SKIP_RATE_ANOMALY <= 1.0


# ─────────────────────────────────────────────────────────────────
# _compute_ingestion_thresholds
# ─────────────────────────────────────────────────────────────────

class TestComputeIngestionThresholds:

    def test_disabled_returns_fallbacks(self):
        cfg = {"enabled": False,
               "fallback_embed_batch": 32,
               "fallback_skip_rate_anomaly": 0.88}
        result = _compute_ingestion_thresholds(cfg)
        assert result["embed_batch_size"] == 32
        assert result["skip_rate_anomaly"] == 0.88
        assert result["computed"] is False

    def test_none_cfg_returns_defaults(self):
        # No config + DB unavailable → module defaults, never raises.
        with patch("tools.rag.ingestion_manager.get_connection",
                   side_effect=Exception("no db")):
            result = _compute_ingestion_thresholds(None)
        assert result["embed_batch_size"] == _EMBED_BATCH_SIZE
        assert result["skip_rate_anomaly"] == _SKIP_RATE_ANOMALY
        assert result["computed"] is False

    def test_insufficient_history_returns_defaults(self):
        cfg = {"enabled": True, "min_samples": 30}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 50.0, "skip_rate": 0.4, "n": 5})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is False
        assert result["embed_batch_size"] == _EMBED_BATCH_SIZE

    def test_high_throughput_scales_batch_up(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"batch_floor": 5, "batch_ceil": 100}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 75.0, "skip_rate": 0.2, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert result["embed_batch_size"] == 75

    def test_low_throughput_clamped_to_floor(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"batch_floor": 5, "batch_ceil": 100}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 1.0, "skip_rate": 0.1, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert result["embed_batch_size"] == 5  # floor

    def test_batch_clamped_to_ceiling(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"batch_floor": 5, "batch_ceil": 50}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 5000.0, "skip_rate": 0.3, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert result["embed_batch_size"] == 50  # ceil

    def test_skip_rate_anomaly_bounds_respected(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"skip_floor": 0.50, "skip_ceil": 0.99}}
        with patch("tools.rag.ingestion_manager.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"avg_created": 20.0, "skip_rate": 0.99, "n": 200})
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is True
        assert 0.50 <= result["skip_rate_anomaly"] <= 0.99

    def test_db_error_returns_defaults(self):
        cfg = {"enabled": True, "min_samples": 10}
        with patch("tools.rag.ingestion_manager.get_connection",
                   side_effect=Exception("DB error")):
            result = _compute_ingestion_thresholds(cfg)
        assert result["computed"] is False
        assert result["embed_batch_size"] == _EMBED_BATCH_SIZE


# ─────────────────────────────────────────────────────────────────
# _load_anomaly_cfg
# ─────────────────────────────────────────────────────────────────

class TestLoadAnomalyCfg:
    def test_returns_dict(self):
        # Reads the real rag_config.yaml; section is present so should be a dict.
        cfg = _load_anomaly_cfg()
        assert isinstance(cfg, dict)


# ─────────────────────────────────────────────────────────────────
# _embed_chunks — honours adaptive batch size
# ─────────────────────────────────────────────────────────────────

class _StubChunk:
    def __init__(self, content):
        self.content = content
        self.embedding = None


class _StubProvider:
    def __init__(self):
        self.calls = 0

    def embed(self, content):
        self.calls += 1
        return [0.1, 0.2, 0.3]


class TestEmbedChunks:
    def test_no_provider_returns_zero(self):
        assert _embed_chunks([_StubChunk("x")], None) == 0

    def test_empty_chunks_returns_zero(self):
        assert _embed_chunks([], _StubProvider()) == 0

    def test_embeds_all_chunks(self):
        provider = _StubProvider()
        chunks = [_StubChunk(f"c{i}") for i in range(5)]
        embedded = _embed_chunks(chunks, provider, batch_size=2)
        assert embedded == 5
        assert all(c.embedding is not None for c in chunks)

    def test_default_batch_size_used_when_none(self):
        provider = _StubProvider()
        chunks = [_StubChunk("c")]
        # batch_size=None falls back to _EMBED_BATCH_SIZE, still embeds.
        assert _embed_chunks(chunks, provider, batch_size=None) == 1
