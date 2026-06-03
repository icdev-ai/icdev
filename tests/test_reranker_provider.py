#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG reranker anomaly detection + extracted thresholds (D-RAG-20).

Covers the aiify modernization (aiify-rm-6efad-phase-5513): hardcoded thresholds
in tools/rag/reranker_provider.py extracted to named, config-overridable
constants and an adaptive anomaly-detection relevance floor derived from
historical reranked retrievals in rag_retrieval_log.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.reranker_provider import (  # noqa: E402
    _ANOMALY_STDDEV_K,
    _BGE_AVAIL_TIMEOUT,
    _BGE_DOC_CHARS,
    _BGE_EMBED_TIMEOUT,
    _DEFAULT_TOP_K,
    _LLM_MAX_TOKENS,
    _LLM_PREVIEW_CHARS,
    _LLM_TEMPERATURE,
    _RERANK_SCORE_FLOOR,
    BGERerankerProvider,
    LLMRerankerProvider,
    _compute_rerank_anomaly_thresholds,
    _load_rerank_anomaly_config,
    get_reranker_provider,
)


# ---------------------------------------------------------------------------
# Extracted constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_constants_have_sane_defaults(self) -> None:
        assert _DEFAULT_TOP_K == 5
        assert _BGE_DOC_CHARS == 800
        assert _BGE_EMBED_TIMEOUT == 30
        assert _BGE_AVAIL_TIMEOUT == 5
        assert _LLM_PREVIEW_CHARS == 400
        assert _LLM_MAX_TOKENS == 512
        assert _LLM_TEMPERATURE == 0.1

    def test_anomaly_floor_in_unit_range(self) -> None:
        assert 0.0 <= _RERANK_SCORE_FLOOR <= 1.0
        assert _ANOMALY_STDDEV_K > 0.0


# ---------------------------------------------------------------------------
# Config-driven provider overrides
# ---------------------------------------------------------------------------


class TestConfigOverrides:
    def test_bge_reads_tunables_from_config(self) -> None:
        provider = BGERerankerProvider(
            config={"bge_doc_chars": 1234, "bge_embed_timeout": 7, "bge_avail_timeout": 2}
        )
        assert provider._doc_chars == 1234
        assert provider._embed_timeout == 7
        assert provider._avail_timeout == 2

    def test_bge_defaults_when_config_empty(self) -> None:
        provider = BGERerankerProvider()
        assert provider._doc_chars == _BGE_DOC_CHARS
        assert provider._embed_timeout == _BGE_EMBED_TIMEOUT
        assert provider._avail_timeout == _BGE_AVAIL_TIMEOUT

    def test_llm_reads_tunables_from_config(self) -> None:
        provider = LLMRerankerProvider(
            config={
                "max_chunk_preview_chars": 200,
                "llm_max_tokens": 256,
                "llm_temperature": 0.0,
            }
        )
        assert provider._max_preview_chars == 200
        assert provider._max_tokens == 256
        assert provider._temperature == 0.0

    def test_llm_defaults_when_config_empty(self) -> None:
        provider = LLMRerankerProvider()
        assert provider._max_preview_chars == _LLM_PREVIEW_CHARS
        assert provider._max_tokens == _LLM_MAX_TOKENS
        assert provider._temperature == _LLM_TEMPERATURE


# ---------------------------------------------------------------------------
# _load_rerank_anomaly_config
# ---------------------------------------------------------------------------


class TestLoadAnomalyConfig:
    def test_inline_anomaly_block_wins(self) -> None:
        block = {"enabled": False, "min_samples": 99}
        assert _load_rerank_anomaly_config({"anomaly_detection": block}) == block

    def test_inline_none_block_returns_empty(self) -> None:
        assert _load_rerank_anomaly_config({"anomaly_detection": None}) == {}

    def test_falls_back_to_yaml(self) -> None:
        # The shipped rag_config.yaml carries reranker.anomaly_detection.
        cfg = _load_rerank_anomaly_config(None)
        assert isinstance(cfg, dict)
        # enabled key is present in the shipped config
        assert cfg.get("enabled") is True


# ---------------------------------------------------------------------------
# _compute_rerank_anomaly_thresholds
# ---------------------------------------------------------------------------


class TestComputeThresholds:
    def test_disabled_uses_fallback_floor(self) -> None:
        th = _compute_rerank_anomaly_thresholds({"enabled": False})
        assert th["computed"] is False
        assert th["score_floor"] == _RERANK_SCORE_FLOOR

    def test_custom_fallback_floor_honored(self) -> None:
        th = _compute_rerank_anomaly_thresholds(
            {"enabled": False, "fallback_score_floor": 0.42}
        )
        assert th["score_floor"] == 0.42

    def test_none_config_uses_module_default(self) -> None:
        # Fresh test DB has no reranked history → fallback to module floor.
        th = _compute_rerank_anomaly_thresholds(None)
        assert th["score_floor"] == _RERANK_SCORE_FLOOR

    def test_high_min_samples_forces_fallback(self) -> None:
        th = _compute_rerank_anomaly_thresholds(
            {"enabled": True, "min_samples": 10**9}
        )
        assert th["computed"] is False
        assert th["score_floor"] == _RERANK_SCORE_FLOOR


# ---------------------------------------------------------------------------
# flag_anomaly
# ---------------------------------------------------------------------------


@pytest.fixture
def provider() -> LLMRerankerProvider:
    # Disable adaptive calibration so the floor is deterministic at 0.30.
    p = LLMRerankerProvider()
    p.configure_anomaly_detection({"anomaly_detection": {"enabled": False}})
    return p


class TestFlagAnomaly:
    def test_low_top_score_is_flagged(self, provider: LLMRerankerProvider) -> None:
        result = provider.flag_anomaly([(2, 0.10), (0, 0.05)])
        assert result["anomalous"] is True
        assert len(result["reasons"]) == 1
        assert result["score_floor"] == _RERANK_SCORE_FLOOR

    def test_high_top_score_is_clean(self, provider: LLMRerankerProvider) -> None:
        result = provider.flag_anomaly([(2, 0.90), (0, 0.10)])
        assert result["anomalous"] is False
        assert result["reasons"] == []

    def test_empty_ranking_is_clean(self, provider: LLMRerankerProvider) -> None:
        result = provider.flag_anomaly([])
        assert result["anomalous"] is False
        assert result["reasons"] == []

    def test_exact_floor_not_flagged(self, provider: LLMRerankerProvider) -> None:
        # Strict "<" comparison: equal-to-floor top score passes.
        result = provider.flag_anomaly([(0, _RERANK_SCORE_FLOOR)])
        assert result["anomalous"] is False

    def test_lazy_configuration_on_first_call(self) -> None:
        # A provider with no explicit configure call still flags via lazy init.
        p = LLMRerankerProvider()
        result = p.flag_anomaly([(0, 0.99)])
        assert "score_floor" in result
        assert result["anomalous"] is False


# ---------------------------------------------------------------------------
# Factory wires anomaly detection
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_returns_provider_with_thresholds(self) -> None:
        # BGE almost never available in CI → falls through to LLM reranker.
        provider = get_reranker_provider({"method": "bge"})
        assert provider._anomaly_thresholds is not None
        assert "score_floor" in provider._anomaly_thresholds

    def test_factory_llm_method(self) -> None:
        provider = get_reranker_provider({"method": "llm"})
        assert provider.provider_name == "llm"
        assert provider._anomaly_thresholds is not None
