# CUI // SP-CTI
"""RAG re-ranking — BGE cross-encoder + LLM fallback (D-RAG-3, D-RAG-20).

Two-backend re-ranking via RerankerProvider ABC:
- BGE-reranker-v2-m3 via Ollama (fast, 50-100ms) — default
- qwen3 LLM re-ranking via scanner_function (fallback, 2-5s)

Score blend uses configurable rerank_weight (default 0.6) instead of
the naive (final + rerank) / 2 average from the original implementation.

Uses LLM router with function='rag_rerank' (scanner_function category).
"""

from __future__ import annotations

import logging
from typing import List, Optional

from tools.rag.vector_store_provider import SearchResult

logger = logging.getLogger("icdev.rag.reranker")


def rerank_results(
    query: str,
    results: List[SearchResult],
    top_k: int = 5,
    config: Optional[dict] = None,
) -> List[SearchResult]:
    """Re-rank results using best available backend (D-RAG-20).

    Tries BGE cross-encoder first (fast), falls back to qwen3 LLM.
    Score blend: (1 - rerank_weight) * final_score + rerank_weight * rerank_score

    Args:
        query: Original search query.
        results: Candidate results from vector + fusion stage.
        top_k: How many results to return after re-ranking.
        config: Rerank config section from rag_config.yaml.

    Returns:
        Re-ranked top-k results with rerank_score set.
    """
    if not results:
        return []
    if len(results) <= top_k:
        return results

    cfg = config or {}
    rerank_weight = cfg.get("rerank_weight", 0.6)

    try:
        from tools.rag.reranker_provider import get_reranker_provider

        provider = get_reranker_provider(cfg)
        documents = [r.content for r in results]
        ranked_pairs = provider.rerank(query, documents, top_k=top_k)

        if not ranked_pairs:
            return results[:top_k]

        # Normalize rerank scores to 0-1 range
        max_rerank = max(score for _, score in ranked_pairs) if ranked_pairs else 1.0
        if max_rerank <= 0:
            max_rerank = 1.0

        # Build re-ranked results with blended scores
        reranked: List[SearchResult] = []
        for idx, raw_score in ranked_pairs:
            if idx < 0 or idx >= len(results):
                continue
            result = results[idx]
            result.rerank_score = raw_score / max_rerank
            # Weighted blend (D-RAG-20): configurable instead of naive average
            result.final_score = (
                (1.0 - rerank_weight) * result.final_score
                + rerank_weight * result.rerank_score
            )
            reranked.append(result)

        logger.debug(
            "Re-ranked %d→%d results via %s (weight=%.2f)",
            len(results), len(reranked), provider.provider_name, rerank_weight,
        )
        return reranked

    except Exception as exc:
        logger.debug("Reranker failed, returning top-k by score: %s", exc)
        return results[:top_k]
