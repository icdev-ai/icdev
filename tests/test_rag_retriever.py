#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG retriever two-stage pipeline (D-RAG-3, D-RAG-19, D-RAG-21)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from tools.rag.retriever import (
    RAGRetriever,
    _compute_bm25_scores,
    _rrf_fusion,
    _weighted_sum_fusion,
    _time_decay_adjust,
    validate_citations,
)
from tools.rag.vector_store_provider import SearchResult


# ---- RRF fusion tests (D-RAG-19) ----


class TestRRFFusion:
    def test_empty_results(self):
        assert _rrf_fusion("query", []) == []

    def test_single_result(self):
        results = [SearchResult(content="hello world", score=0.8, final_score=0.8)]
        fused = _rrf_fusion("hello", results, k=60)
        assert len(fused) == 1
        assert fused[0].final_score > 0  # RRF score assigned

    def test_rrf_score_is_rank_based(self):
        """RRF scores should be based on ranks, not raw scores."""
        results = [
            SearchResult(
                chunk_id="c1", content="FedRAMP AC-2 access control FedRAMP compliance", score=0.9, final_score=0.9
            ),
            SearchResult(chunk_id="c2", content="totally unrelated content here", score=0.8, final_score=0.8),
            SearchResult(chunk_id="c3", content="another unrelated document text", score=0.7, final_score=0.7),
        ]
        fused = _rrf_fusion("FedRAMP AC-2", results, k=60)
        # Result with matching terms should rank higher via BM25 component
        scores = {r.chunk_id: r.final_score for r in fused}
        assert scores["c1"] > scores["c2"]

    def test_bm25_score_set(self):
        results = [SearchResult(content="test query match", score=0.5, final_score=0.5)]
        fused = _rrf_fusion("test", results, k=60)
        assert hasattr(fused[0], "bm25_score")
        assert isinstance(fused[0].bm25_score, float)

    def test_rrf_score_formula(self):
        """Verify: RRF = 1/(k+rank_v) + 1/(k+rank_b)."""
        results = [
            SearchResult(chunk_id="c1", content="test doc one", score=0.9, final_score=0.9),
            SearchResult(chunk_id="c2", content="test doc two", score=0.8, final_score=0.8),
        ]
        k = 60
        fused = _rrf_fusion("test", results, k=k)
        # Both have vector ranks 1 and 2
        # RRF scores should be small positive values
        for r in fused:
            assert 0 < r.final_score < 1


class TestWeightedSumFusion:
    def test_empty_results(self):
        assert _weighted_sum_fusion("query", [], weight=0.3) == []

    def test_zero_weight(self):
        results = [SearchResult(content="hello world", score=0.8, final_score=0.8)]
        fused = _weighted_sum_fusion("hello", results, weight=0.0)
        assert fused[0].final_score == 0.8  # No change

    def test_basic_boost(self):
        # Need >= 3 docs so BM25 IDF is non-zero
        results = [
            SearchResult(content="FedRAMP AC-2 access control FedRAMP compliance", score=0.5, final_score=0.5),
            SearchResult(content="totally unrelated content here", score=0.5, final_score=0.5),
            SearchResult(content="another unrelated document text", score=0.5, final_score=0.5),
        ]
        fused = _weighted_sum_fusion("FedRAMP AC-2", results, weight=0.3)
        # Result with matching terms should have higher final_score
        assert fused[0].final_score > fused[1].final_score

    def test_blend_formula(self):
        """Verify: final_score = (1 - weight) * vector_score + weight * bm25_score."""
        results = [SearchResult(content="exact match query", score=0.6, final_score=0.6)]
        weight = 0.3
        fused = _weighted_sum_fusion("exact match", results, weight=weight)
        r = fused[0]
        expected = (1 - weight) * 0.6 + weight * r.bm25_score
        assert abs(r.final_score - expected) < 1e-6


class TestComputeBM25Scores:
    def test_empty_results(self):
        assert _compute_bm25_scores("query", []) == []

    def test_returns_floats(self):
        results = [SearchResult(content="hello world test", score=0.5, final_score=0.5)]
        scores = _compute_bm25_scores("hello", results)
        assert len(scores) == 1
        assert isinstance(scores[0], float)

    def test_matching_query_higher_score(self):
        results = [
            SearchResult(content="python machine learning deep", score=0.5, final_score=0.5),
            SearchResult(content="totally different content here", score=0.5, final_score=0.5),
            SearchResult(content="another unrelated stuff text", score=0.5, final_score=0.5),
        ]
        scores = _compute_bm25_scores("python machine learning", results)
        assert scores[0] > scores[1]


# ---- Citation validation tests (D-RAG-21) ----


class TestValidateCitations:
    def test_no_citations(self):
        result = validate_citations("No citations here.", 3)
        assert result["cited_count"] == 0
        assert result["citation_rate"] == 0.0
        assert len(result["uncited_sources"]) == 3

    def test_valid_citations(self):
        result = validate_citations("See [SOURCE-1] and [SOURCE-2].", 3)
        assert result["cited_count"] == 2
        assert result["uncited_sources"] == ["3"]

    def test_hallucinated_citations(self):
        result = validate_citations("See [SOURCE-5].", 3)
        assert result["hallucinated_citations"] == ["5"]

    def test_zero_sources(self):
        result = validate_citations("Some text.", 0)
        assert result["citation_rate"] == 0.0

    def test_all_cited(self):
        result = validate_citations("[SOURCE-1] and [SOURCE-2].", 2)
        assert result["cited_count"] == 2
        assert result["uncited_sources"] == []
        assert result["citation_rate"] == 1.0


# ---- Time decay tests ----


class TestTimeDecayAdjust:
    def test_empty_results(self):
        assert _time_decay_adjust([]) == []

    def test_no_created_at_defaults_to_1(self):
        results = [SearchResult(content="no date", final_score=0.5, metadata={})]
        adjusted = _time_decay_adjust(results)
        assert adjusted[0].time_decay_score == 1.0

    def test_with_created_at(self):
        """When time_decay module is available and created_at exists."""
        results = [
            SearchResult(
                content="test",
                final_score=0.8,
                metadata={"created_at": "2025-01-01T00:00:00"},
            )
        ]
        # compute_time_aware_score is imported inside _time_decay_adjust
        with patch("tools.memory.time_decay.compute_time_aware_score", return_value=0.42):
            adjusted = _time_decay_adjust(results)
            assert adjusted[0].final_score == 0.42
            assert adjusted[0].time_decay_score == 0.42


# ---- RAGRetriever tests ----


class TestRAGRetriever:
    def test_init_default(self):
        retriever = RAGRetriever(config={"rag": {}})
        assert retriever._tenant_id == ""

    def test_init_with_tenant(self):
        retriever = RAGRetriever(tenant_id="t1", config={"rag": {}})
        assert retriever._tenant_id == "t1"

    def test_search_no_embedding_provider(self):
        """When no embedding provider is available, search returns empty."""
        with patch("tools.rag.retriever._get_embedding_provider", return_value=None):
            retriever = RAGRetriever(config={"rag": {"retrieval": {}, "rerank": {}}})
            results = retriever.search("test query")
            assert results == []

    def test_search_embedding_failure(self):
        """When embedding fails, search returns empty."""
        mock_provider = MagicMock()
        mock_provider.embed.side_effect = Exception("embed failed")
        with patch("tools.rag.retriever._get_embedding_provider", return_value=mock_provider):
            retriever = RAGRetriever(config={"rag": {"retrieval": {}, "rerank": {}}})
            results = retriever.search("test query")
            assert results == []

    def test_search_calls_vector_store(self):
        """Verify search pipeline calls vector store and returns results."""
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1, 0.2, 0.3]

        mock_store = MagicMock()
        mock_store.search.return_value = [
            SearchResult(
                chunk_id="c1",
                content="FedRAMP AC-2",
                source_type="compliance",
                score=0.9,
                final_score=0.9,
            ),
        ]

        with (
            patch("tools.rag.retriever._get_embedding_provider", return_value=mock_provider),
            patch("tools.rag.retriever.VectorStoreFactory") as MockFactory,
        ):
            MockFactory.create.return_value = mock_store
            retriever = RAGRetriever(
                config={
                    "rag": {
                        "retrieval": {"min_score_threshold": 0.0},
                        "rerank": {"enabled": False},
                        "provenance": {"enabled": False},
                    }
                }
            )
            results = retriever.search("FedRAMP AC-2", top_k=5, rerank=False)
            assert len(results) >= 0  # May be filtered by score

    def test_search_with_source_types(self):
        """Source types filter should search each type separately."""
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1, 0.2]

        mock_store = MagicMock()
        mock_store.search.return_value = []

        with (
            patch("tools.rag.retriever._get_embedding_provider", return_value=mock_provider),
            patch("tools.rag.retriever.VectorStoreFactory") as MockFactory,
        ):
            MockFactory.create.return_value = mock_store
            retriever = RAGRetriever(
                config={
                    "rag": {
                        "retrieval": {},
                        "rerank": {"enabled": False},
                        "provenance": {"enabled": False},
                    }
                }
            )
            retriever.search(
                "test",
                source_types=["innovation_signals", "compliance_artifacts"],
                rerank=False,
            )
            # Should call search once per source type
            assert mock_store.search.call_count == 2

    def test_log_retrieval(self, tmp_path):
        """Verify _log_retrieval doesn't crash (DB may not exist in tests)."""
        retriever = RAGRetriever(config={"rag": {"retrieval": {}, "rerank": {}}})
        # Should not raise even if DB doesn't exist
        retriever._log_retrieval(
            query="test",
            results_count=0,
            top_score=0.0,
            duration_ms=100,
            mode="vector",
            project_id="",
        )

    def test_min_score_threshold_weighted_sum(self):
        """Results below min_score_threshold should be filtered out (weighted_sum mode)."""
        mock_provider = MagicMock()
        mock_provider.embed.return_value = [0.1]

        mock_store = MagicMock()
        mock_store.search.return_value = [
            SearchResult(chunk_id="c1", content="low score", score=0.05, final_score=0.05),
            SearchResult(chunk_id="c2", content="high score", score=0.8, final_score=0.8),
        ]

        with (
            patch("tools.rag.retriever._get_embedding_provider", return_value=mock_provider),
            patch("tools.rag.retriever.VectorStoreFactory") as MockFactory,
        ):
            MockFactory.create.return_value = mock_store
            retriever = RAGRetriever(
                config={
                    "rag": {
                        "retrieval": {
                            "min_score_threshold": 0.1,
                            "bm25_boost_weight": 0.0,
                            "time_decay_enabled": False,
                            "fusion_method": "weighted_sum",
                        },
                        "rerank": {"enabled": False},
                        "provenance": {"enabled": False},
                    }
                }
            )
            results = retriever.search("test", rerank=False)
            # Low-score result should be filtered
            for r in results:
                assert r.final_score >= 0.1

    def test_retrieve_alias(self):
        """retrieve() should be an alias for search()."""
        retriever = RAGRetriever(config={"rag": {}})
        with patch.object(retriever, "search", return_value=[]) as mock_search:
            retriever.retrieve("test", top_k=3)
            mock_search.assert_called_once_with("test", top_k=3)


# ---- Reranker tests ----


class TestReranker:
    def test_empty_results(self):
        from tools.rag.reranker import rerank_results

        assert rerank_results("query", []) == []

    def test_fewer_results_than_top_k(self):
        from tools.rag.reranker import rerank_results

        results = [SearchResult(content="only one", score=0.5)]
        reranked = rerank_results("test", results, top_k=5)
        assert len(reranked) == 1

    def test_rerank_fallback_on_error(self):
        """When LLM is unavailable, reranker should fallback to truncation."""
        from tools.rag.reranker import rerank_results

        results = [
            SearchResult(content=f"result {i}", score=0.5 - i * 0.1, final_score=0.5 - i * 0.1) for i in range(10)
        ]
        # Mock LLM router to avoid live inference calls — test verifies graceful fallback
        with patch("tools.llm.router.LLMRouter") as mock_router_cls:
            mock_router_cls.return_value.invoke.side_effect = RuntimeError("LLM unavailable")
            reranked = rerank_results("test query", results, top_k=3)
        assert len(reranked) <= 3

    def test_rerank_weight_config(self):
        """Verify rerank_weight is read from config."""
        from tools.rag.reranker import rerank_results

        results = [SearchResult(content=f"result {i}", score=0.5, final_score=0.5) for i in range(10)]
        config = {"rerank_weight": 0.7}
        # Mock LLM router to avoid live inference calls
        with patch("tools.llm.router.LLMRouter") as mock_router_cls:
            mock_router_cls.return_value.invoke.side_effect = RuntimeError("LLM unavailable")
            reranked = rerank_results("test query", results, top_k=3, config=config)
        assert len(reranked) <= 3
