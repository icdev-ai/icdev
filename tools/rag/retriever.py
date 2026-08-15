#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG Retriever — two-stage pipeline (D-RAG-3, D-RAG-19, D-RAG-21).

Pipeline:
    1. Embed query (Ollama nomic-embed-text via get_embedding_provider())
    2. Vector similarity top-50 (cosine, via VectorStoreProvider.search())
    3. RRF hybrid fusion — vector + BM25 rank fusion (D-RAG-19)
       (Backward compat: weighted_sum mode via fusion_method config)
    4. Time-decay adjustment (reuse tools/memory/time_decay.py)
    5. BGE cross-encoder re-rank top-50 → top-5 (D-RAG-20, fallback qwen3)
    6. Record provenance (PROV-AGENT)
    7. Citation validation (D-RAG-21)

Usage:
    python tools/rag/retriever.py --query "FedRAMP AC-2" --json
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import re
import time
from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.rag.vector_store_factory import VectorStoreFactory  # noqa: E402
from tools.rag.vector_store_provider import SearchResult  # noqa: E402

logger = get_logger("icdev.rag.retriever")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under rag.retrieval.  Change config, not code.
# ---------------------------------------------------------------------------
_RRF_K_DEFAULT            = 60     # RRF paper default k (score denominator)
_VECTOR_TOP_K_DEFAULT     = 50     # initial candidate pool from vector search
_FINAL_TOP_K_DEFAULT      = 5      # final results returned to caller
_BM25_WEIGHT_DEFAULT      = 0.30   # BM25 boost weight for weighted-sum fusion
_MIN_SCORE_THRESHOLD      = 0.10   # minimum score to include in results
_RESULT_PREVIEW_CHARS     = 120    # max chars in result preview/logging

#: Every value ``search()`` can write to ``rag_retrieval_log.retrieval_mode``.
#: The CHECK constraint is derived from THIS list (CLAUDE.md: constraints come
#: from a Python constant, never a hardcoded SQL list) — in
#: ``tools/db/init_icdev_db.py`` for a fresh database, in
#: ``tools/db/schema/pg_consolidated.sql``, and in migration 20260815002727 for
#: an existing one. They must agree, because ``_log_retrieval``'s INSERT is
#: best-effort inside a try/except: a mode outside the constraint does not
#: raise, it silently drops the row. That is how every reflectively reranked
#: retrieval went unlogged after oss-meas-01 added a value no DDL allowed.
#: ``tests/rag/test_reflective_surface_scoping.py`` asserts the four agree.
RETRIEVAL_MODES = (
    "vector",
    "bm25",
    "hybrid",
    "rrf_hybrid",
    "reranked",
    "reflective_reranked",
    "reflective_degraded",
)


#: Parsed RAG config keyed by path -> (mtime, config). See _load_rag_config().
_rag_config_cache: Dict[str, tuple] = {}


def reset_rag_config_cache() -> None:
    """Drop the parsed-config memo. For tests that rewrite the config file."""
    _rag_config_cache.clear()


def _load_rag_config(refresh: bool = False) -> dict:
    """Load RAG config, memoized per path+mtime.

    Path comes from :func:`tools.rag.config_path.rag_config_path` so a
    measurement run can point ``ICDEV_RAG_CONFIG`` at a temp config with exactly
    one toggle flipped, instead of rewriting the shared committed file.

    Cached the same way ``tools/cortex/config.py::load_cortex_config`` caches
    its own file (ctx-perf-04). ``RAGRetriever.__init__`` calls this, and every
    Cortex search with the ``rag`` backend constructs a retriever, so the
    uncached form paid a disk read plus a full YAML parse per search. The mtime
    is the invalidation signal — an edited config is picked up on the next call
    without a restart — so this still stat()s the file each time; that is one
    syscall against a ~600-line parse. ``refresh=True`` bypasses the memo.

    The returned dict is SHARED between callers (as the cortex memo's is):
    read it, do not mutate it.
    """
    from tools.rag.config_path import rag_config_path

    config_path = rag_config_path()
    key = str(config_path)
    mtime = config_path.stat().st_mtime if config_path.is_file() else 0.0
    if not refresh:
        cached = _rag_config_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("RAG: unreadable config at %s (%s)", config_path, exc)
        return {}
    _rag_config_cache[key] = (mtime, config)
    return config


class EmbeddingUnavailableError(RuntimeError):
    """The query could not be embedded, so retrieval never ran.

    Deliberately NOT the same as an empty result list (ctx-perf-04). Both used
    to surface as ``[]``, which reaches a chat user as "No matching results were
    found" — a dead embedding provider reported as "nothing matched". Every
    caller of :meth:`RAGRetriever.search` already wraps it in ``try/except``, so
    raising degrades exactly where ``[]`` used to, but a caller that wants to
    tell the two apart now can.
    """


def _get_embedding_provider():
    """Get embedding provider, or None when the whole chain is unavailable."""
    try:
        from tools.llm import get_embedding_provider

        return get_embedding_provider()
    except Exception as exc:
        logger.error(
            "RAG: no embedding provider available (%s) — queries cannot be "
            "embedded; this is a provider failure, not an empty corpus",
            exc,
        )
        return None


def _embed_query(provider, query: str) -> List[float]:
    """Embed one query with whichever provider shape the router handed us.

    An ICDEV ``EmbeddingProvider`` carries its own configured model, so
    ``.embed()`` needs no model argument. A raw OpenAI-style client does not,
    and the model id for that path is resolved from ``embeddings`` in
    args/llm_config.yaml — never a literal in this file.
    """
    if hasattr(provider, "embed"):
        return provider.embed(query)

    from tools.llm.embedding_provider import resolve_embedding_model_id

    resp = provider.embeddings.create(input=query, model=resolve_embedding_model_id())
    return resp.data[0].embedding


# ---------------------------------------------------------------------------
# Fusion strategies (D-RAG-19)
# ---------------------------------------------------------------------------


def _compute_bm25_scores(query: str, results: List[SearchResult]) -> List[float]:
    """Compute raw BM25 scores for results (shared by both fusion methods)."""
    if not results:
        return []
    documents = [r.content for r in results]
    try:
        from rank_bm25 import BM25Okapi

        tokenized = [doc.lower().split() for doc in documents]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(query.lower().split())
    except ImportError:
        # Fallback: simple term frequency
        query_terms = query.lower().split()
        scores = []
        for doc in documents:
            doc_lower = doc.lower()
            score = sum(doc_lower.count(term) for term in query_terms)
            scores.append(float(score))
    return [float(s) for s in scores]


def _rrf_fusion(
    query: str,
    results: List[SearchResult],
    k: int = _RRF_K_DEFAULT,
) -> List[SearchResult]:
    """Reciprocal Rank Fusion of vector + BM25 rankings (D-RAG-19).

    RRF score = 1/(k + rank_vector) + 1/(k + rank_bm25)

    Score-agnostic, rank-based fusion. Eliminates sensitivity to
    vector/BM25 score scale differences. Default k=60 per RRF paper.
    """
    if not results:
        return results

    # Rank 1: vector similarity (results arrive pre-sorted by vector score)
    vector_ranks: Dict[str, int] = {}
    for i, r in enumerate(results):
        vector_ranks[r.chunk_id] = i + 1

    # Rank 2: BM25 keyword scoring
    bm25_scores = _compute_bm25_scores(query, results)
    bm25_sorted_indices = sorted(
        range(len(results)),
        key=lambda i: bm25_scores[i],
        reverse=True,
    )
    bm25_ranks: Dict[str, int] = {}
    for rank, idx in enumerate(bm25_sorted_indices):
        bm25_ranks[results[idx].chunk_id] = rank + 1

    # Compute RRF score and store BM25 scores for logging
    for i, result in enumerate(results):
        result.bm25_score = bm25_scores[i]
        v_rank = vector_ranks.get(result.chunk_id, len(results))
        b_rank = bm25_ranks.get(result.chunk_id, len(results))
        result.final_score = 1.0 / (k + v_rank) + 1.0 / (k + b_rank)

    return results


def _weighted_sum_fusion(
    query: str,
    results: List[SearchResult],
    weight: float = _BM25_WEIGHT_DEFAULT,
) -> List[SearchResult]:
    """Legacy weighted-sum BM25 boost (backward compat, pre-D-RAG-19).

    Blend: (1 - weight) * vector_score + weight * normalized_bm25_score
    """
    if not results or weight <= 0:
        return results

    bm25_scores = _compute_bm25_scores(query, results)
    max_score = max(bm25_scores) if bm25_scores and max(bm25_scores) > 0 else 1.0
    normalized = [s / max_score for s in bm25_scores]

    for i, result in enumerate(results):
        result.bm25_score = normalized[i]
        result.final_score = (1 - weight) * result.score + weight * result.bm25_score

    return results


def _time_decay_adjust(results: List[SearchResult]) -> List[SearchResult]:
    """Apply time-decay scoring to results (D-RAG-12).

    Reuses time_decay logic from tools/memory/time_decay.py.
    """
    try:
        from tools.memory.time_decay import compute_time_aware_score
    except ImportError:
        return results

    for result in results:
        created_at = result.metadata.get("created_at") or result.metadata.get("discovered_at", "")
        if not created_at:
            result.time_decay_score = 1.0
            continue

        importance = result.metadata.get("importance", 5)
        if isinstance(importance, str):
            try:
                importance = int(importance)
            except ValueError:
                importance = 5

        result.time_decay_score = compute_time_aware_score(
            base_score=result.final_score,
            created_at=created_at,
            memory_type="fact",
            importance=importance,
        )
        # Blend time decay into final score
        result.final_score = result.time_decay_score

    return results


# ---------------------------------------------------------------------------
# RAPTOR multi-level merge + dedup (RCE, rce-raptor-02)
# ---------------------------------------------------------------------------


def _merge_raptor_results(
    leaf_results: List[SearchResult],
    summary_results: List[SearchResult],
) -> List[SearchResult]:
    """Merge summary-tier hits into the leaf candidate pool, dedup by lineage.

    Precision + TRUST: when a summary and one of its children (a leaf, or a
    lower-level summary) both hit, prefer the finer-grained result — drop the
    parent summary. Summaries whose children are absent survive as *fallback
    context* (rescues weak leaf retrieval). Leaves are always kept and always
    preferred for citation (summaries carry ``metadata.is_summary = True`` and
    must never be surfaced as a citation source).

    Summaries are processed lowest-level-first so that when a level-1 summary
    survives, its level-2 parent (whose ``child_ids`` include that level-1 id)
    is dropped in favor of the finer level-1 summary.
    """
    if not summary_results:
        return leaf_results
    covered = {r.chunk_id for r in leaf_results}
    kept: List[SearchResult] = []
    for s in sorted(summary_results, key=lambda r: r.metadata.get("level", 1)):
        child_ids = s.metadata.get("child_ids", []) or []
        if any(cid in covered for cid in child_ids):
            continue  # a finer-grained child already present → drop this parent
        kept.append(s)
        covered.add(s.chunk_id)
    return leaf_results + kept


# ---------------------------------------------------------------------------
# Citation validation (D-RAG-21)
# ---------------------------------------------------------------------------


def validate_citations(
    response_text: str,
    injected_count: int,
) -> Dict[str, Any]:
    """Validate [SOURCE-N] citation tags in LLM response (D-RAG-21).

    Args:
        response_text: The LLM-generated response text.
        injected_count: Number of sources that were injected.

    Returns:
        Dict with citation_rate, cited/uncited/hallucinated source lists.
    """
    cited = set(re.findall(r"\[SOURCE-(\d+)\]", response_text))
    available = set(str(i + 1) for i in range(injected_count))
    return {
        "cited_count": len(cited & available),
        "available_count": injected_count,
        "citation_rate": len(cited & available) / max(injected_count, 1),
        "uncited_sources": sorted(available - cited, key=int),
        "hallucinated_citations": sorted(cited - available, key=lambda x: int(x) if x.isdigit() else 0),
    }


# ---------------------------------------------------------------------------
# RAGRetriever
# ---------------------------------------------------------------------------


class RAGRetriever:
    """Two-stage RAG retriever with RRF fusion and cross-encoder re-ranking."""

    def __init__(self, tenant_id: str = "", config: Optional[dict] = None):
        self._tenant_id = tenant_id
        self._config = config or _load_rag_config()
        self._rag_cfg = self._config.get("rag", {})
        self._retrieval_cfg = self._rag_cfg.get("retrieval", {})
        self._rerank_cfg = self._rag_cfg.get("rerank", {})
        # RCE (rce-raptor-02): RAPTOR multi-level retrieval. Default OFF — when
        # disabled the pipeline is byte-for-byte the flat-leaf path.
        self._raptor_cfg = self._rag_cfg.get("raptor", {})
        # agx-rag-02, wired by oss-meas-01. Default OFF — see step 5b.
        self._reflective_cfg = self._rag_cfg.get("reflective_rerank", {})

        # SEC: Warn when tenant_id is empty in multi-tenant environment
        if not tenant_id:
            import os

            if os.environ.get("ICDEV_MULTI_TENANT", "").lower() in ("true", "1", "yes"):
                logger.warning(
                    "RAGRetriever initialized without tenant_id in multi-tenant mode. "
                    "Queries may return cross-tenant results."
                )

    # Alias for backward compat (router.py calls .retrieve())
    def retrieve(self, query: str = "", **kwargs) -> List[SearchResult]:
        """Alias for search() — backward compat with router._rag_augment()."""
        return self.search(query, **kwargs)

    def _reflective_enabled_for(self, surface: Optional[str]) -> bool:
        """Whether reflective reranking (step 5b) fires for *surface*.

        ``rag.reflective_rerank.surfaces`` scopes an expensive per-document LLM
        pass to the surfaces that opted in. Three cases, deliberately distinct:

        * toggle off                     -> never, as before.
        * on, ``surfaces`` absent/empty  -> every surface (the pre-scoping
          semantics, so an existing deployment that set ``enabled: true`` does
          not silently lose the behaviour).
        * on, ``surfaces`` declared      -> only those. A caller that states no
          surface is NOT one of them: the cost is per-document LLM calls on an
          interactive path, and defaulting an unattributed caller into paying it
          is how a scoped toggle becomes a global one again.
        """
        if not self._reflective_cfg.get("enabled", False):
            return False
        surfaces = self._reflective_cfg.get("surfaces") or []
        if not surfaces:
            return True
        return surface in surfaces

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        source_types: Optional[List[str]] = None,
        project_id: str = "",
        rerank: Optional[bool] = None,
        query_label: Optional[str] = None,
        surface: Optional[str] = None,
    ) -> List[SearchResult]:
        """Execute full two-stage retrieval pipeline.

        Args:
            query: Natural language query.
            top_k: Final number of results (default from config).
            source_types: Filter by source types.
            project_id: Optional project filter.
            rerank: Override re-ranking enabled/disabled.
            surface: Consuming surface, in the ``args/trust_gate.yaml`` profile
                vocabulary (``chat_rag``, ``drafting``, ``compliance_evidence``,
                ``agent_output``). Only read by per-surface toggles — today just
                reflective reranking (step 5b). Unset means "unattributed", and
                a per-surface toggle does not fire for it.

        Returns:
            List of SearchResult sorted by final_score descending.
        """
        start_ms = int(time.time() * 1000)
        vector_top_k = self._retrieval_cfg.get("vector_top_k", _VECTOR_TOP_K_DEFAULT)
        final_top_k = top_k or self._retrieval_cfg.get("final_top_k", _FINAL_TOP_K_DEFAULT)
        fusion_method = self._retrieval_cfg.get("fusion_method", "rrf")
        # D-RAG-24: Per-label adaptive weights (Know Your RAG)
        label_weights = self._retrieval_cfg.get("label_weights", {})
        if query_label and query_label in label_weights:
            bm25_weight = label_weights[query_label].get("bm25_weight", _BM25_WEIGHT_DEFAULT)
            rrf_k = label_weights[query_label].get("rrf_k", _RRF_K_DEFAULT)
        else:
            bm25_weight = self._retrieval_cfg.get("bm25_boost_weight", _BM25_WEIGHT_DEFAULT)
            rrf_k = self._retrieval_cfg.get("rrf_k", _RRF_K_DEFAULT)
        time_decay_enabled = self._retrieval_cfg.get("time_decay_enabled", True)
        do_rerank = rerank if rerank is not None else self._rerank_cfg.get("enabled", True)
        min_score = self._retrieval_cfg.get("min_score_threshold", _MIN_SCORE_THRESHOLD)

        # Step 1: Embed query. A failure here means retrieval never RAN, which
        # is not the same answer as "the corpus matched nothing" — so it raises
        # rather than returning [] (ctx-perf-04).
        provider = _get_embedding_provider()
        if not provider:
            raise EmbeddingUnavailableError(
                "no embedding provider is available — the query was never "
                "embedded and no backend was searched"
            )

        try:
            query_embedding = _embed_query(provider, query)
        except Exception as exc:
            logger.error(
                "RAG: query embedding failed via %s (%s) — returning a retrieval "
                "ERROR, not an empty result set",
                type(provider).__name__,
                exc,
            )
            raise EmbeddingUnavailableError(f"query embedding failed: {exc}") from exc

        # Step 2: Vector similarity search
        store = VectorStoreFactory.create(tenant_id=self._tenant_id)
        filters: Dict[str, Any] = {}
        if source_types:
            # Search each source type separately and merge
            all_results: list[SearchResult] = []
            for st in source_types:
                f = {"source_type": st}
                if project_id:
                    f["project_id"] = project_id
                if self._tenant_id:
                    f["tenant_id"] = self._tenant_id
                results = store.search(query_embedding, top_k=vector_top_k, filters=f)
                all_results.extend(results)
            # Sort and truncate to vector_top_k
            all_results.sort(key=lambda r: r.score, reverse=True)
            results = all_results[:vector_top_k]
        else:
            if project_id:
                filters["project_id"] = project_id
            if self._tenant_id:
                filters["tenant_id"] = self._tenant_id
            results = store.search(query_embedding, top_k=vector_top_k, filters=filters)

        # RCE (rce-raptor-02): also search the RAPTOR summary tier and merge it
        # into the leaf pool (dedup by lineage). Default OFF → exact old path.
        if self._raptor_cfg.get("enabled", False):
            summary_results = self._search_summaries(query_embedding, vector_top_k, project_id)
            results = _merge_raptor_results(results, summary_results)

        if not results:
            self._log_retrieval(query, 0, 0.0, start_ms, "vector", project_id)
            return []

        # Initialize final_score from vector score
        for r in results:
            r.final_score = r.score

        # Step 3: Hybrid fusion (D-RAG-19)
        if fusion_method == "rrf":
            results = _rrf_fusion(query, results, k=rrf_k)
            retrieval_mode = "rrf_hybrid"
        else:
            # Legacy weighted-sum (backward compat)
            results = _weighted_sum_fusion(query, results, weight=bm25_weight)
            retrieval_mode = "hybrid"

        # Step 4: Time-decay adjustment
        if time_decay_enabled:
            results = _time_decay_adjust(results)

        # Filter by minimum score (skip for RRF — scores are small by design)
        if fusion_method != "rrf":
            results = [r for r in results if r.final_score >= min_score]

        # Sort by final_score
        results.sort(key=lambda r: r.final_score, reverse=True)

        # Step 5: Re-rank (BGE cross-encoder or qwen3 LLM, D-RAG-20)
        if do_rerank and len(results) > final_top_k:
            try:
                from tools.rag.reranker import rerank_results

                results = rerank_results(
                    query=query,
                    results=results[:vector_top_k],
                    top_k=final_top_k,
                    config=self._rerank_cfg,
                )
                retrieval_mode = "reranked"
            except Exception:
                # Fallback: just truncate
                results = results[:final_top_k]
        else:
            results = results[:final_top_k]

        # Step 5b: Self-RAG per-document reflective reranking (agx-rag-02).
        # Wired by oss-meas-01. It shipped with a config block, a test file and
        # zero callers, so `rag.reflective_rerank.enabled` read as a live
        # capability and did nothing — and a benchmark flipping it measured a
        # 0.0 delta indistinguishable from "wired and useless".
        #
        # Adopted for `chat_rag` ONLY by trust-self-02: `surfaces` scopes the
        # spend to the surfaces that opted in, so drafting and compliance
        # retrieval keep the byte-for-byte previous path. An unattributed call
        # (surface=None) does not fire it — a per-document LLM cost should be
        # charged to a surface that asked for it, not to whoever forgot to say.
        #
        # Runs AFTER the cross-encoder rather than instead of it: this scores
        # each surviving candidate on separate RELEVANT/SUPPORTS/USEFUL axes and
        # composes the ordering in Python, so it refines a shortlist rather than
        # replacing the cheap ranker. `results` is already truncated to
        # final_top_k above, so the per-query cost is at most that many calls.
        if results and self._reflective_enabled_for(surface):
            try:
                from tools.rag.reflective_reranker import reflective_rerank

                report: dict = {}
                results = reflective_rerank(
                    query,
                    results,
                    top_k=final_top_k,
                    max_candidates=self._reflective_cfg.get("max_candidates"),
                    report=report,
                )
                # Only claim the mode when a document was actually judged. An
                # unreachable model falls back to neutral on every axis, which
                # returns the incoming order — labelling that "reflective" is
                # how a dead capability reports itself as live.
                # Both values are in the rag_retrieval_log.retrieval_mode CHECK
                # (migration 20260815002727). Do NOT compose a new string here:
                # _log_retrieval's INSERT is best-effort inside a try/except, so
                # a value outside the constraint drops the row silently.
                if report.get("effective"):
                    retrieval_mode = "reflective_reranked"
                else:
                    retrieval_mode = "reflective_degraded"
                    logger.warning(
                        "reflective rerank judged nothing for surface %s (%s of %s "
                        "documents degraded: %s); keeping order",
                        surface, report.get("degraded"), report.get("reflected"),
                        report.get("reason", "unknown"),
                    )
            except Exception as exc:
                # Same posture as the cross-encoder above: a reranker failure
                # must degrade to the existing ordering, never drop results.
                logger.debug("reflective rerank unavailable (%s); keeping order", exc)

        # Step 6: Log retrieval
        duration_ms = int(time.time() * 1000) - start_ms
        top_score = results[0].final_score if results else 0.0
        self._log_retrieval(
            query,
            len(results),
            top_score,
            duration_ms,
            retrieval_mode,
            project_id,
            source_types=source_types,
            rerank_used=do_rerank,
        )

        # Step 7: Record provenance (optional)
        if self._rag_cfg.get("provenance", {}).get("enabled", True):
            self._record_provenance(query, results, project_id)

        return results

    def _search_summaries(
        self,
        query_embedding: List[float],
        top_k: int,
        project_id: str = "",
    ) -> List[SearchResult]:
        """Search the RAPTOR summary tier (rag_chunk_summaries).

        Best-effort: returns [] when the summary store / table is unavailable
        (e.g. no hierarchy built yet), so enabling raptor never breaks retrieval.
        """
        try:
            from tools.rag.raptor import SummaryStore

            store = SummaryStore(tenant_id=self._tenant_id)
            filters: Dict[str, Any] = {}
            if project_id:
                filters["project_id"] = project_id
            if self._tenant_id:
                filters["tenant_id"] = self._tenant_id
            summary_top_k = self._raptor_cfg.get("summary_top_k", top_k)
            return store.search(query_embedding, top_k=summary_top_k, filters=filters)
        except Exception:
            return []

    def _log_retrieval(
        self,
        query: str,
        results_count: int,
        top_score: float,
        duration_ms: int,
        mode: str,
        project_id: str,
        source_types: Optional[List[str]] = None,
        rerank_used: bool = False,
    ):
        """Log retrieval to rag_retrieval_log (append-only, D-RAG-8)."""
        if not ICDEV_DB.exists():
            return
        # Hash query by default (D282)
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        import os

        record_content = os.environ.get("ICDEV_CONTENT_TRACING_ENABLED", "").lower() == "true"
        query_text = query if record_content else ""

        try:
            conn = get_connection()
            conn.execute(
                """INSERT INTO rag_retrieval_log
                   (query_hash, query_text, results_count, top_score,
                    retrieval_mode, vector_top_k, final_top_k, rerank_used,
                    source_types_queried, duration_ms, tenant_id, project_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    query_hash,
                    query_text,
                    results_count,
                    round(top_score, 4),
                    mode,
                    self._retrieval_cfg.get("vector_top_k", 50),
                    self._retrieval_cfg.get("final_top_k", 5),
                    1 if rerank_used else 0,
                    json.dumps(source_types or []),
                    duration_ms,
                    self._tenant_id,
                    project_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_log_retrieval: best-effort INSERT into rag_retrieval_log failed (non-blocking): %s", exc)

    def _record_provenance(self, query: str, results: List[SearchResult], project_id: str):
        """Record PROV-AGENT provenance for this retrieval (D-RAG-8)."""
        try:
            from tools.observability.provenance.prov_recorder import ProvRecorder

            recorder = ProvRecorder()

            # Create activity entity for the retrieval
            activity_id = recorder.record_activity(
                activity_type="rag_retrieval",
                description=f"RAG retrieval: {len(results)} results",
                agent_id="rag_retriever",
                project_id=project_id,
            )

            # Record chunk entities as inputs
            for result in results:
                entity_id = recorder.record_entity(
                    entity_type="rag_chunk",
                    entity_id=result.chunk_id,
                    description=f"{result.source_type}:{result.source_id}",
                    metadata={"score": result.final_score},
                )
                recorder.record_relation(
                    source_id=activity_id,
                    target_id=entity_id,
                    relation_type="used",
                )
        except Exception:
            pass  # Provenance is best-effort


def main():
    parser = argparse.ArgumentParser(description="RAG Retriever")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results")
    parser.add_argument("--source-types", help="Comma-separated source types filter")
    parser.add_argument("--project-id", default="", help="Project ID filter")
    parser.add_argument("--no-rerank", action="store_true", help="Disable re-ranking")
    parser.add_argument("--tenant-id", default="", help="Tenant ID")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    source_types = args.source_types.split(",") if args.source_types else None
    retriever = RAGRetriever(tenant_id=args.tenant_id)
    results = retriever.search(
        query=args.query,
        top_k=args.top_k,
        source_types=source_types,
        project_id=args.project_id,
        rerank=not args.no_rerank,
    )

    if args.json_output:
        output = {
            "classification": "CUI // SP-CTI",
            "query": args.query,
            "results_count": len(results),
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(output, indent=2))
    else:
        if not results:
            print("No results found.")
            return
        for r in results:
            print(f"[{r.source_type}:{r.source_id}] (score:{r.final_score:.3f}) {r.content[:_RESULT_PREVIEW_CHARS]}...")


if __name__ == "__main__":
    main()
