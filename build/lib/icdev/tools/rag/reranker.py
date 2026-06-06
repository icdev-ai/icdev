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
from tools.logging.icdev_logger import get_logger

from typing import List, Optional

from tools.rag.vector_store_provider import SearchResult

logger = get_logger("icdev.rag.reranker")


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
            result.final_score = (1.0 - rerank_weight) * result.final_score + rerank_weight * result.rerank_score
            reranked.append(result)

        # D-BT-6: Teaching-dimension diversity boost
        # Penalise redundant chunks that don't eliminate alternative interpretations
        teaching_boost = cfg.get("teaching_boost_weight", 0.0)
        if teaching_boost > 0 and len(reranked) > 1:
            reranked = _apply_teaching_diversity(reranked, teaching_boost)

        logger.debug(
            "Re-ranked %d→%d results via %s (weight=%.2f, bt=%.2f)",
            len(results),
            len(reranked),
            provider.provider_name,
            rerank_weight,
            teaching_boost,
        )
        return reranked

    except Exception as exc:
        logger.debug("Reranker failed, returning top-k by score: %s", exc)
        return results[:top_k]


def _apply_teaching_diversity(
    results: List[SearchResult],
    boost_weight: float = 0.15,
) -> List[SearchResult]:
    """Apply Bayesian Teaching diversity boost (D-BT-6).

    Penalises chunks that are highly similar to already-ranked chunks,
    following the teaching-dimension principle: each example should
    eliminate at least one alternative interpretation.

    Uses word-overlap as a lightweight similarity proxy (air-gap safe).
    """
    if not results or boost_weight <= 0:
        return results

    def _word_set(text: str) -> set:
        return {w.lower().strip(".,;:!?()[]{}\"'") for w in (text or "").split() if len(w) > 3}

    selected_words: list = []
    for result in results:
        words = _word_set(result.content)
        if not selected_words:
            # First item — no penalty
            selected_words.append(words)
            continue

        # Compute max similarity to any already-selected result
        max_overlap = 0.0
        for prev_words in selected_words:
            if not words or not prev_words:
                continue
            intersection = len(words & prev_words)
            union = len(words | prev_words)
            if union > 0:
                jaccard = intersection / union
                max_overlap = max(max_overlap, jaccard)

        # Diversity score: 1.0 = unique, 0.0 = identical to existing
        diversity = 1.0 - max_overlap
        # Blend: reduce score of redundant chunks
        result.final_score = (1.0 - boost_weight) * result.final_score + boost_weight * diversity
        selected_words.append(words)

    # Re-sort after diversity adjustment
    results.sort(key=lambda r: r.final_score, reverse=True)
    return results
