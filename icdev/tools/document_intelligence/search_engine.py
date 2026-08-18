# CUI // SP-CTI
"""DIC Grounded Search Engine.

Default mode: BM25 + KG traversal (NO LLM, no vector required — air-gap safe).
Optional hybrid mode: adds vector similarity + RRF fusion + cross-encoder rerank.

Every result carries a mandatory citation pack. Results with no traceable source
are suppressed (never returned uncited).
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

from tools.document_intelligence.constants import CLASSIFICATION_LEVELS
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _clearance_rank(classification: str) -> int:
    """Map a classification marking to its position in CLASSIFICATION_LEVELS.

    Higher rank == more restricted. Unknown / compound markings are normalized
    to the nearest base tier so an unexpected label never silently grants access
    (it falls back to CUI, never to UNCLASSIFIED). ``TOP SECRET//SCI`` and any
    other ``TOP SECRET`` variant collapse to the highest tier.
    """
    c = (classification or "CUI").strip().upper()
    for i, level in enumerate(CLASSIFICATION_LEVELS):
        if c == level:
            return i
    if c.startswith("TOP SECRET"):
        return len(CLASSIFICATION_LEVELS) - 1
    if c in ("PUBLIC", "U", "UNCLASS", "UNCLASSIFIED//FOUO"):
        return 0
    return 1  # safe default: treat anything unrecognized as CUI, not open


@dataclass
class Citation:
    doc_id: str = ""
    doc_title: str = ""
    version_id: str = ""
    page: int = 0
    section: str = ""
    chunk_id: str = ""
    source_uri: str = ""
    classification: str = "CUI"

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "version_id": self.version_id,
            "page": self.page,
            "section": self.section,
            "chunk_id": self.chunk_id,
            "source_uri": self.source_uri,
            "classification": self.classification,
            "archive_url": f"/document-intelligence/doc/{self.doc_id}" if self.doc_id else "#",
        }


@dataclass
class DICAnswer:
    """A grounded, LLM-synthesized answer over DIC search results.

    The answer is generated ONLY from the cited search results; it never draws
    on the model's own knowledge. ``grounded`` is False (and ``answer`` empty)
    whenever there is no supporting evidence, the LLM is unavailable, or the
    model itself declines for lack of grounding — callers must surface
    ``refusal_reason`` rather than fabricating a reply.
    """

    answer: str = ""
    grounded: bool = False
    citations: list[Citation] = field(default_factory=list)
    result_count: int = 0
    refusal_reason: str = ""
    origin: str = "ai_generated"
    # Aggregate citation-sufficiency in [0,1] over the evidence fed to synthesis:
    # how strongly the cited chunks support the question (attribution lens).
    citation_quality: float = 0.0

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "grounded": self.grounded,
            "citations": [c.to_dict() for c in self.citations],
            "result_count": self.result_count,
            "refusal_reason": self.refusal_reason,
            "origin": self.origin,
            "citation_quality": round(self.citation_quality, 4),
        }


@dataclass
class DICAccessExplanation:
    """A grounded, LLM-composed explanation of access-controlled search results.

    Mirrors the access-control layer of a permission-aware fulltext search:
    results above the caller's ``clearance`` are withheld, and this object
    explains *that* withholding in natural language WITHOUT leaking any protected
    content. The LLM is given only the structured classification breakdown (level
    names and counts) — never the document text — so the explanation can describe
    what was withheld and why, but can never disclose it. ``message`` always
    carries a usable explanation (a deterministic template is used whenever the
    LLM is unavailable), and ``llm_used`` records whether the model produced it.
    """

    clearance: str = "CUI"
    visible_count: int = 0
    withheld_count: int = 0
    withheld_by_level: dict[str, int] = field(default_factory=dict)
    message: str = ""
    llm_used: bool = False
    origin: str = "ai_generated"

    def to_dict(self) -> dict:
        return {
            "clearance": self.clearance,
            "visible_count": self.visible_count,
            "withheld_count": self.withheld_count,
            "withheld_by_level": self.withheld_by_level,
            "message": self.message,
            "llm_used": self.llm_used,
            "origin": self.origin,
        }


@dataclass
class DICQueryExpansion:
    """An LLM-suggested broadening of a fulltext query to improve match recall.

    Models the *matching* side of a fulltext search: the LLM proposes additional
    search keywords / synonyms (never answers the question, never invents facts)
    so that documents phrased differently than the query still match. The
    expansion is purely additive — ``terms`` are extra keywords, and
    ``expanded_query`` is the original query with those terms appended. When the
    LLM is unavailable the object degrades to the original query with no extra
    terms (``llm_used`` False, ``refusal_reason`` set), so search behavior is
    never worse than the un-expanded baseline.
    """

    original_query: str = ""
    terms: list[str] = field(default_factory=list)
    expanded_query: str = ""
    llm_used: bool = False
    refusal_reason: str = ""
    origin: str = "ai_generated"

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "terms": self.terms,
            "expanded_query": self.expanded_query,
            "llm_used": self.llm_used,
            "refusal_reason": self.refusal_reason,
            "origin": self.origin,
        }


@dataclass
class DICSearchResult:
    chunk_id: str = ""
    doc_id: str = ""
    doc_title: str = ""
    collection_id: str = ""
    content: str = ""
    page: int = 0
    section: str = ""
    score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)
    kg_path: list[str] = field(default_factory=list)
    citation: Citation = field(default_factory=Citation)
    sha256: str = ""
    attribution_pct: int = 0
    # Query-relative attribution strength in [0,1]: how well this chunk's text
    # actually supports the query (attribution lens), reusing the verifier's
    # claim-vs-evidence overlap measure. Drives attribution-lens reranking.
    attribution_score: float = 0.0
    # LLM-generated 1-3 sentence summary of the full document, stored at ingest
    # time via _ai_document_summary() in ingest_orchestrator. Empty string when
    # the document was ingested before this field was added.
    doc_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "collection_id": self.collection_id,
            "content": self.content[:500],
            "page": self.page,
            "section": self.section,
            "score": round(self.score, 4),
            "matched_terms": self.matched_terms,
            "kg_path": self.kg_path,
            "citation": self.citation.to_dict(),
            "sha256": self.sha256,
            "attribution_pct": self.attribution_pct,
            "attribution_score": round(self.attribution_score, 4),
            "archive_url": f"/document-intelligence/doc/{self.doc_id}" if self.doc_id else "#",
            "doc_summary": self.doc_summary,
        }


@dataclass
class DICGroundedSearch:
    """A grounded search response carrying an aggregate citation-quality score.

    Wraps the cited result list from :meth:`DICSearchEngine.grounded_search` with
    a per-answer ``citation_quality`` (sufficiency) score in [0,1] — the mean
    attribution strength of the returned chunks. Results are ordered through the
    attribution lens (strongly-supporting evidence first), and every result still
    carries its mandatory citation pack. ``anomaly_report`` surfaces any
    statistically anomalous patterns in the result attribution scores.
    """

    query: str = ""
    results: list["DICSearchResult"] = field(default_factory=list)
    result_count: int = 0
    citation_quality: float = 0.0
    origin: str = "ai_retrieved"
    anomaly_report: "SearchAnomalyReport | None" = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "result_count": self.result_count,
            "citation_quality": round(self.citation_quality, 4),
            "origin": self.origin,
        }
        if self.anomaly_report is not None:
            d["anomaly_report"] = self.anomaly_report.to_dict()
        return d


@dataclass
class SearchAnomalyReport:
    """Statistical anomaly assessment for a DIC search result set.

    Replaces the external pattern of hard-coding a single relevance cutoff
    (e.g. ``if score < 0.3: skip_result``) with IQR-based distribution analysis.
    The detection adapts to the actual score distribution so no manual threshold
    needs to be maintained. Three anomaly types are detected:

    * ``low_coverage``  — Q3 of attribution scores is below the noise floor,
      meaning even the better-ranked results have near-zero support for the query.
    * ``score_outlier`` — the top result is far above the pack (> mean + 3σ with
      meaningful spread), suggesting only one chunk is actually relevant.
    * ``high_variance`` — scores span a wide range (std > 0.3, IQR > 0.25),
      indicating the result set mixes highly relevant and irrelevant chunks.

    ``is_anomalous`` is False and all floats are 0.0 when the result set is too
    small to characterize (< 2 results) or no anomaly pattern is detected.
    """

    is_anomalous: bool = False
    anomaly_type: str = ""
    mean_attribution: float = 0.0
    score_std: float = 0.0
    top_score: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "is_anomalous": self.is_anomalous,
            "anomaly_type": self.anomaly_type,
            "mean_attribution": round(self.mean_attribution, 4),
            "score_std": round(self.score_std, 4),
            "top_score": round(self.top_score, 4),
            "detail": self.detail,
        }


@dataclass
class DICKeywordSearchResult:
    """Results of an embedding-based keyword-list search over DIC.

    Models the upgrade of a literal keyword-list filter (a document matches only
    when it contains one of an exact list of keywords) to semantic embedding
    search (a document matches when it is *semantically* similar to the keywords,
    even when it never uses the literal term — e.g. the keyword ``vehicle``
    surfaces a document that only says ``automobile``).

    ``embedding_used`` records whether embedding/vector retrieval was actually
    attempted. When it is False the engine fell back to literal keyword matching
    (no embedding provider / air-gap), so results are never *worse* than the
    keyword-list baseline. Every result in ``results`` still carries a full
    citation pack, and access control is applied exactly as in :meth:`search`.
    """

    keywords: list[str] = field(default_factory=list)
    results: list["DICSearchResult"] = field(default_factory=list)
    embedding_used: bool = False
    result_count: int = 0
    refusal_reason: str = ""
    origin: str = "ai_retrieved"

    def to_dict(self) -> dict:
        return {
            "keywords": self.keywords,
            "results": [r.to_dict() for r in self.results],
            "embedding_used": self.embedding_used,
            "result_count": self.result_count,
            "refusal_reason": self.refusal_reason,
            "origin": self.origin,
        }


@dataclass
class DICFilterQuery:
    """LLM-parsed structured filters for DIC document search.

    Models the filter-query module of a fulltext search engine (aiify-opp-29:
    fulltext_search_engine -> llm_generation, analog of paperless
    ``src/documents/filters.py``). Converts natural language filter intent
    (e.g., "recent PDF reports at CUI level from last 30 days") into structured
    parameters that narrow document retrieval without requiring users to know the
    exact field names. The LLM emits ONLY the JSON keys listed in
    :data:`_FILTER_SYSTEM_PROMPT` — it never answers the user's query, never
    invents proper nouns or classification levels not in the schema, and always
    degrades gracefully (empty ``filters``) when unavailable.

    ``confidence`` is a self-reported [0.0, 1.0] estimate from the model of how
    reliably it could extract filters from the natural language. It is 0.0 when
    ``llm_used`` is False (no model ran). ``refusal_reason`` is set when no
    filters were extracted: ``"empty_query"`` for blank input, ``"no_filters"``
    when the model found nothing applicable, or ``"llm_unavailable"`` on failure.
    """

    natural_query: str = ""
    filters: dict = field(default_factory=dict)
    confidence: float = 0.0
    llm_used: bool = False
    refusal_reason: str = ""
    origin: str = "ai_generated"

    def to_dict(self) -> dict:
        return {
            "natural_query": self.natural_query,
            "filters": self.filters,
            "confidence": round(self.confidence, 4),
            "llm_used": self.llm_used,
            "refusal_reason": self.refusal_reason,
            "origin": self.origin,
        }


@dataclass
class DICFilterCoverageAnomaly:
    """Anomaly report for a DIC filter predicate's coverage over a candidate set.

    Models the upgrade of hardcoded filter thresholds (aiify-opp-31:
    hardcoded_threshold -> anomaly_detection, analog of paperless
    ``src/documents/filters.py``).  Instead of fixed cutoffs that must be
    maintained by hand (e.g. ``min_pages > 5``, ``confidence >= 0.7``), this
    report characterises the distribution of the relevant document property via
    IQR-based statistics and flags when the requested threshold sits outside the
    natural range of the candidate set.

    Two anomaly types are detected:

    * ``over_restrictive`` — threshold is above Q3+1.5×IQR; the filter would
      exclude the vast majority of candidates (< 10 % coverage).
    * ``over_permissive`` — threshold is below Q1−1.5×IQR; the filter would
      admit nearly all candidates (> 90 % coverage), adding no discriminative value.

    ``is_anomalous`` is False when the candidate set is too small for reliable IQR
    (< 4 values) or no anomaly pattern is detected.  ``suggested_threshold`` is set
    only when an anomaly is flagged; it is the natural-distribution anchor (Q1 or
    Q3) the caller should consider using instead.
    """

    is_anomalous: bool = False
    anomaly_type: str = ""
    coverage_pct: float = 0.0
    q1: float = 0.0
    q3: float = 0.0
    iqr: float = 0.0
    suggested_threshold: float | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        d: dict = {
            "is_anomalous": self.is_anomalous,
            "anomaly_type": self.anomaly_type,
            "coverage_pct": round(self.coverage_pct, 4),
            "q1": round(self.q1, 4),
            "q3": round(self.q3, 4),
            "iqr": round(self.iqr, 4),
            "detail": self.detail,
        }
        if self.suggested_threshold is not None:
            d["suggested_threshold"] = round(self.suggested_threshold, 4)
        return d


def _extract_terms(query: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\b\w{3,}\b", query)]


def _normalize_keywords(keywords: list[str] | None) -> list[str]:
    """Trim, drop blanks, and de-duplicate (case-insensitively) a keyword list.

    Order is preserved and the original casing of the first occurrence is kept,
    so the returned list reads back the way the caller supplied it.
    """
    out: list[str] = []
    seen: set[str] = set()
    for kw in keywords or []:
        term = (kw or "").strip()
        if not term:
            continue
        low = term.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(term)
    return out


def _doc_meta(conn, doc_id: str) -> dict[str, Any]:
    try:
        cur = conn.execute(
            "SELECT title, classification, summary FROM dic_documents WHERE doc_id = %s",
            (doc_id,),
        )
        row = cur.fetchone()
        if row:
            return {
                "title": row[0] or doc_id,
                "classification": row[1] or "CUI",
                "summary": row[2] or "",
            }
    except Exception:
        pass
    return {"title": doc_id, "classification": "CUI", "summary": ""}


def _chunk_meta(conn, chunk_id: str) -> dict[str, Any]:
    """Pull page/section/sha256 from rag_chunks + dic_chunk_links if available."""
    result: dict[str, Any] = {"page": 0, "section": "", "doc_id": "", "collection_id": "", "sha256": "", "attribution_pct": 0}
    try:
        cur = conn.execute(
            "SELECT content, content_hash, project_id FROM rag_chunks WHERE id = %s",
            (chunk_id,),
        )
        row = cur.fetchone()
        if row:
            content = row["content"] if hasattr(row, "keys") else row[0]
            content_hash = row["content_hash"] if hasattr(row, "keys") else row[1]
            project_id = (row["project_id"] if hasattr(row, "keys") else row[2]) or ""
            sha256 = content_hash or hashlib.sha256((content or "").encode()).hexdigest()
            result["sha256"] = sha256
            result["attribution_pct"] = min(100, max(40, int((len(content or "") / 500) * 80)))
            # Collection of record. `rag_chunks.project_id` is what the retriever
            # itself filters on (`_rag_search` passes `project_id=collection_id`),
            # so seeding from it keeps the caller's post-filter consistent with the
            # query that produced the candidate. `dic_chunk_links` refines it below
            # when a link row exists.
            #
            # Without this the post-filter re-derived the collection from
            # `dic_chunk_links` ALONE, which is populated only by
            # `ingest_orchestrator.ingest_file`. Chunks ingested by any other path
            # — or before linking existed — had no row, resolved to "", and were
            # dropped by `if collection_id and col_id != collection_id`. Measured
            # on the live corpus: 168 of 559 chunks linked, so a scoped query
            # against a 236-chunk collection returned ZERO while the retriever had
            # correctly returned its chunks. Silent, and it looks like bad recall.
            result["collection_id"] = project_id
    except Exception:
        pass
    try:
        cur2 = conn.execute(
            "SELECT page, section, doc_id, collection_id FROM dic_chunk_links WHERE rag_chunk_id = %s",
            (chunk_id,),
        )
        row2 = cur2.fetchone()
        if row2:
            if hasattr(row2, "keys"):
                page, section, doc_id, col = row2["page"], row2["section"], row2["doc_id"], row2["collection_id"]
            else:
                page, section, doc_id, col = row2[0], row2[1], row2[2], row2[3]
            result.update({"page": page or 0, "section": section or "", "doc_id": doc_id or ""})
            # Only override when the link actually names a collection — a link row
            # with a NULL/empty collection_id must not erase the project_id seeded
            # above and re-create the drop-everything behaviour.
            if col:
                result["collection_id"] = col
    except Exception:
        pass
    return result


# --------------------------------------------------------------------------- #
# Attribution-lens reranking + citation-quality scoring (dic-adapt-04).
#
# DIC mandates citations but the raw retrieval score only measures topical match,
# not whether a cited chunk actually *supports* the query. Adapting the arXiv
# "Re-Ranking Through an Attribution Lens for Citation Quality in Legal QA" and
# EviRank work, we compute a per-chunk attribution strength (how strongly the
# chunk supports the query) and rerank the candidate set so better-supporting
# evidence ranks higher, then expose a per-answer citation-sufficiency score.
#
# The attribution measure REUSES the verifier's per-claim evidence-overlap
# (icdev/tools/document_intelligence/verifier.py::_lexical_overlap) — the same
# token-recall used by the anti-hallucination gate — so search ranking and
# verification agree on what "supported" means. No LLM is required (air-gap safe).
# --------------------------------------------------------------------------- #

# Blend weight for the attribution lens vs. the raw retrieval score when
# reranking. Attribution dominates (so a strongly-supporting chunk outranks a
# weakly-related one) while retrieval still breaks near-ties. Override via env.
_ATTR_RERANK_WEIGHT = max(0.0, min(1.0, float(os.environ.get("DIC_ATTR_RERANK_WEIGHT", "0.7"))))


def _attribution_strength(query: str, content: str) -> float:
    """Per-chunk attribution/sufficiency for ``query`` in [0,1].

    Reuses the verifier's claim-vs-evidence token-overlap (``_lexical_overlap``)
    so search-time attribution and verify-time support use one definition. Treats
    the query as the "claim" and the chunk as the "evidence". Returns 0.0 if the
    verifier helper cannot be imported (degrades to retrieval-only ranking).
    """
    try:
        from tools.document_intelligence.verifier import _lexical_overlap
    except Exception:
        return 0.0
    try:
        return float(_lexical_overlap(query or "", content or ""))
    except Exception:
        return 0.0


def _rerank_by_attribution(
    query: str, results: list["DICSearchResult"], rerank: bool = True
) -> list["DICSearchResult"]:
    """Score every result by attribution strength and (optionally) reorder.

    Always populates ``attribution_score`` on each result. When ``rerank`` is
    True (default) the list is re-sorted by a blend of attribution strength and
    the normalized retrieval score, attribution-weighted so stronger-supporting
    chunks rise. The sort is stable, so equal-attribution results keep their
    original retrieval order. Citations are untouched — this only reorders.
    """
    if not results:
        return results
    max_ret = max((r.score for r in results), default=0.0) or 1.0
    scored: list[tuple[float, int, "DICSearchResult"]] = []
    for idx, r in enumerate(results):
        r.attribution_score = _attribution_strength(query, r.content)
        ret_norm = (r.score / max_ret) if max_ret else 0.0
        combined = _ATTR_RERANK_WEIGHT * r.attribution_score + (1.0 - _ATTR_RERANK_WEIGHT) * ret_norm
        scored.append((combined, idx, r))
    if not rerank:
        return [r for _, _, r in scored]
    # idx as secondary key preserves original order among equal combined scores.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, _, r in scored]


def _citation_quality(results: list["DICSearchResult"]) -> float:
    """Aggregate per-answer citation sufficiency: mean attribution strength."""
    if not results:
        return 0.0
    return sum(r.attribution_score for r in results) / len(results)


# --------------------------------------------------------------------------- #
# Search-result anomaly detection (aiify-opp-68: hardcoded_threshold ->
# anomaly_detection). The external analogue hard-codes a single relevance
# cutoff (``if score < constant: drop``). Instead we characterise the full
# attribution-score *distribution* via IQR-based statistics so detection
# adapts to each result set without requiring a hand-tuned threshold.
# --------------------------------------------------------------------------- #


def detect_search_anomalies(results: list["DICSearchResult"]) -> "SearchAnomalyReport":
    """Detect statistically anomalous attribution-score patterns in a result set.

    Requires attribution scores to have been populated first (call
    :func:`_rerank_by_attribution` or :meth:`DICSearchEngine.search` beforehand).
    Returns a :class:`SearchAnomalyReport` with ``is_anomalous=False`` when the
    result set is too small (< 2 results) or no anomaly pattern is detected.
    """
    if len(results) < 2:
        mean = results[0].attribution_score if results else 0.0
        return SearchAnomalyReport(mean_attribution=mean)

    scores = [r.attribution_score for r in results]
    n = len(scores)
    mean = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / n
    std = variance ** 0.5
    top = max(scores)

    sorted_s = sorted(scores)
    q1 = sorted_s[n // 4]
    q3 = sorted_s[(3 * n) // 4]
    iqr = q3 - q1

    anomaly_type = ""
    detail = ""

    if q3 < 0.05:
        # Even the upper-quartile results are near zero — query has no coverage.
        anomaly_type = "low_coverage"
        detail = (
            f"Upper-quartile attribution is {q3:.3f} (below noise floor); "
            "query may not match any indexed content."
        )
    elif top > q3 + 1.5 * iqr and std > 0.1:
        # IQR-fence outlier: top result is far above the pack (using the Tukey
        # fence q3 + 1.5*IQR instead of mean+3σ, because σ itself is inflated
        # by the very outlier we are trying to detect).
        anomaly_type = "score_outlier"
        detail = (
            f"Top score {top:.3f} exceeds IQR fence {q3 + 1.5 * iqr:.3f} "
            f"(Q3={q3:.3f}, IQR={iqr:.3f}); only one chunk appears relevant."
        )
    elif std > 0.3 and iqr > 0.25:
        # Wide spread: mix of highly relevant and irrelevant results.
        anomaly_type = "high_variance"
        detail = (
            f"Attribution std={std:.3f}, IQR={iqr:.3f}; "
            "result set mixes relevant and irrelevant chunks."
        )

    report = SearchAnomalyReport(
        is_anomalous=bool(anomaly_type),
        anomaly_type=anomaly_type,
        mean_attribution=mean,
        score_std=std,
        top_score=top,
        detail=detail,
    )
    if report.is_anomalous:
        logger.info(
            "DIC search anomaly [%s]: %s",
            anomaly_type,
            detail,
        )
    return report


# --------------------------------------------------------------------------- #
# Filter-predicate coverage anomaly detection (aiify-opp-31:
# hardcoded_threshold -> anomaly_detection, analog of paperless
# src/documents/filters.py).  The external pattern hard-codes filter thresholds
# (e.g. ``min_confidence=0.7``, ``min_pages=5``) that must be tuned by hand for
# each deployment.  This layer characterises the *distribution* of the candidate
# document property values via IQR-based statistics and reports when the
# requested threshold sits outside the natural range — making the predicate
# either over-restrictive or over-permissive for the actual corpus.
# --------------------------------------------------------------------------- #


def detect_filter_coverage_anomaly(
    candidate_values: list[float],
    threshold: float,
    direction: str = "min",
) -> "DICFilterCoverageAnomaly":
    """Detect anomalous filter threshold coverage over a document property distribution.

    Args:
        candidate_values: Observed values of the filtered property across the
            candidate document set (e.g. page counts, confidence scores).
        threshold: The filter cutoff value to evaluate.
        direction: ``"min"`` means the filter keeps values *≥ threshold*
            (e.g. ``min_pages``); ``"max"`` keeps values *≤ threshold*
            (e.g. ``max_pages``).

    Returns:
        A :class:`DICFilterCoverageAnomaly` with ``is_anomalous=False`` when the
        candidate set is too small (< 4 values) or no anomaly is detected.
    """
    n = len(candidate_values)
    if n < 4:
        return DICFilterCoverageAnomaly()

    sorted_v = sorted(candidate_values)
    q1 = sorted_v[n // 4]
    q3 = sorted_v[(3 * n) // 4]
    iqr = q3 - q1
    upper_fence = q3 + 1.5 * iqr
    lower_fence = q1 - 1.5 * iqr

    if direction == "min":
        matching = sum(1 for v in candidate_values if v >= threshold)
    else:
        matching = sum(1 for v in candidate_values if v <= threshold)
    coverage_pct = matching / n

    anomaly_type = ""
    detail = ""
    suggested: float | None = None

    if direction == "min":
        if threshold > upper_fence and coverage_pct < 0.10:
            anomaly_type = "over_restrictive"
            suggested = q3
            detail = (
                f"Filter threshold {threshold:.3f} exceeds upper IQR fence "
                f"{upper_fence:.3f} (Q3={q3:.3f}); only {coverage_pct:.1%} of "
                f"candidates qualify. Consider relaxing to Q3={q3:.3f}."
            )
        elif iqr > 0 and threshold < lower_fence and coverage_pct > 0.90:
            anomaly_type = "over_permissive"
            suggested = q1
            detail = (
                f"Filter threshold {threshold:.3f} is below lower IQR fence "
                f"{lower_fence:.3f} (Q1={q1:.3f}); {coverage_pct:.1%} of candidates "
                f"qualify, adding no discriminative value. Consider tightening to "
                f"Q1={q1:.3f}."
            )
    else:  # direction == "max"
        # For max-direction filters, the IQR fence can be negative for
        # positive-only value sets, so we use quartile anchors directly.
        if threshold < q1 and coverage_pct < 0.10:
            anomaly_type = "over_restrictive"
            suggested = q1
            detail = (
                f"Max filter threshold {threshold:.3f} is below Q1={q1:.3f}; "
                f"only {coverage_pct:.1%} of candidates qualify."
            )
        elif iqr > 0 and threshold > q3 and coverage_pct > 0.90:
            anomaly_type = "over_permissive"
            suggested = q3
            detail = (
                f"Max filter threshold {threshold:.3f} exceeds Q3={q3:.3f}; "
                f"{coverage_pct:.1%} of candidates qualify, adding no "
                f"discriminative value."
            )

    report = DICFilterCoverageAnomaly(
        is_anomalous=bool(anomaly_type),
        anomaly_type=anomaly_type,
        coverage_pct=coverage_pct,
        q1=q1,
        q3=q3,
        iqr=iqr,
        suggested_threshold=suggested,
        detail=detail,
    )
    if report.is_anomalous:
        logger.info(
            "DIC filter coverage anomaly [%s]: %s",
            anomaly_type,
            detail,
        )
    return report


# --------------------------------------------------------------------------- #
# LLM-powered result snippet generation (aiify-opp-74: fulltext_search_engine
# -> llm_generation, analog of paperless src/documents/serialisers.py
# highlights field). The external pattern computes highlighted excerpts server-
# side to show WHY a document matched a search query. DIC upgrades this: the
# LLM extracts the single most query-relevant passage from each chunk's content,
# producing a focused, context-aware excerpt rather than a raw content[:500]
# truncation. The model NEVER answers the question — it only identifies the most
# relevant passage. Degrades gracefully to raw truncation when unavailable
# (air-gap safe). User-provided queries are injection-scanned (passed as user
# turn, not injected into the system prompt). Batch variant caps at
# _SNIPPET_MAX_RESULTS so a large result set doesn't flood the LLM.
# --------------------------------------------------------------------------- #

_SNIPPET_MAX_CONTENT_CHARS = 1200  # max content fed to the model per result
_SNIPPET_MAX_RESULTS = 8           # max results processed in generate_snippets()
_SNIPPET_MAX_TOKENS = 200          # max tokens per snippet response

_SNIPPET_SYSTEM_PROMPT = (
    "You are a search result highlighter for a document repository. "
    "Given a search query and a document excerpt, extract and return the SINGLE "
    "most relevant passage (1–3 sentences) from the excerpt that best relates to "
    "the query. Return ONLY the extracted passage — no preamble, no explanation, "
    "no rephrasing. If no passage is clearly relevant, return the first sentence "
    "of the excerpt verbatim. Do NOT add information not in the excerpt."
)

_SNIPPET_NO_CONTENT_SENTINEL = "NO_CONTENT"


@dataclass
class DICResultSnippet:
    """An AI-extracted, query-focused passage from a single DIC search result.

    Models the upgrade of ``serialisers.py`` highlights — where a document
    management system returns raw content truncated to a fixed length — to a
    semantically extracted excerpt that surfaces the passage most relevant to
    the caller's query. The LLM locates the best supporting passage inside the
    chunk content; it never generates text outside the source, so the snippet
    is always grounded in the cited chunk.

    ``llm_used`` is False when the raw fallback was returned (LLM unavailable
    or content empty). ``refusal_reason`` is set to ``"empty_content"`` when
    the chunk had no text or ``"llm_unavailable"`` when the model could not run.
    The ``snippet`` field is always populated (fallback = ``content[:500]``).
    """

    chunk_id: str = ""
    doc_id: str = ""
    query: str = ""
    snippet: str = ""
    llm_used: bool = False
    refusal_reason: str = ""
    origin: str = "ai_generated"

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "query": self.query,
            "snippet": self.snippet,
            "llm_used": self.llm_used,
            "refusal_reason": self.refusal_reason,
            "origin": self.origin,
        }


# --------------------------------------------------------------------------- #
# LLM grounded answer synthesis (aiify-opp-6046: fulltext_search_engine ->
# llm_generation). DIC search returns cited chunks with NO LLM by default
# (air-gap safe). This OPTIONAL layer takes the top cited results and asks the
# LLM to compose a natural-language answer grounded STRICTLY in those chunks,
# with inline [n] citation markers. It never runs unless explicitly requested,
# never invents content beyond the retrieved evidence, and refuses (grounded=
# False) when there is no evidence or the model cannot answer from the context.
# The query is user-provided, so injection scanning stays ON (not skipped).
# --------------------------------------------------------------------------- #

# Per-result excerpt budget when building the grounding context. Bounds cost and
# keeps each citation block readable; longer chunks are truncated, not dropped.
_ANSWER_CHARS_PER_RESULT = 800

# Never feed more than this many top results into the synthesis prompt.
_ANSWER_MAX_RESULTS = 6

_ANSWER_SYSTEM_PROMPT = (
    "You are a grounded question-answering assistant for a document repository. "
    "Answer the user's question using ONLY the numbered source excerpts provided. "
    "Cite every claim with the matching bracketed marker, e.g. [1] or [2]. Do NOT "
    "use any outside knowledge, and do NOT invent facts, numbers, names, or "
    "sources. If the provided excerpts do not contain enough information to answer, "
    "reply with exactly: INSUFFICIENT_EVIDENCE. Keep the answer concise and "
    "strictly supported by the cited excerpts."
)

# Sentinel the model is instructed to emit when the context cannot answer.
_ANSWER_REFUSAL_SENTINEL = "INSUFFICIENT_EVIDENCE"


# --------------------------------------------------------------------------- #
# Access-control explanation (aiify-opp-6045: fulltext_search_engine ->
# llm_generation, modeled on a permission-aware fulltext search). When a query
# matches documents above the caller's clearance, those results are withheld by
# search() and this layer composes a short, grounded, NON-LEAKING explanation of
# what was withheld and why. The LLM receives ONLY the structured classification
# breakdown (level names + counts) and the caller's clearance — never any
# document content, title, or the raw query — so it physically cannot disclose
# protected material. A deterministic template is used whenever the LLM is
# unavailable, so an explanation is always returned.
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Query expansion (aiify-opp-6039: fulltext_search_engine -> llm_generation,
# modeled on the document-matching module of a fulltext search engine). DIC
# matches a query to chunks via BM25 + KG with NO LLM by default. This OPT-IN
# layer asks the LLM to propose additional search keywords / synonyms so a
# document phrased differently than the query still matches (better recall). It
# NEVER answers the question and NEVER invents facts — it only emits search
# terms. The query is user-provided, so injection scanning stays ON. When the
# LLM is unavailable it degrades to the original query (no extra terms), so
# matching is never worse than the un-expanded baseline.
# --------------------------------------------------------------------------- #

# Hard cap on how many expansion terms are accepted from the model — bounds the
# query size and stops a runaway model from flooding the search with noise.
_EXPANSION_MAX_TERMS = 8

# Reject any single "term" longer than this — guards against the model returning
# a sentence/explanation instead of a keyword.
_EXPANSION_MAX_TERM_LEN = 40

_EXPANSION_SYSTEM_PROMPT = (
    "You expand search queries for a document repository. Given a user's query, "
    "output ONLY a comma-separated list of additional search keywords or synonyms "
    "that would help retrieve relevant documents phrased differently than the "
    "query. Output keywords only — no sentences, no explanations, no numbering, "
    "and do NOT answer the user's question. Do NOT invent proper nouns, product "
    "names, numbers, or facts; emit only general synonyms and closely related "
    "terms. If you cannot suggest useful keywords, output exactly: NONE."
)

# Sentinel the model emits when it has no useful expansion terms.
_EXPANSION_NONE_SENTINEL = "NONE"

_ACCESS_SYSTEM_PROMPT = (
    "You are an access-control assistant for a classified document repository. "
    "You will be told a user's clearance level and a count of search matches that "
    "were withheld because they are classified above that clearance, broken down "
    "by classification level. Write ONE short, professional sentence (two at most) "
    "informing the user that results were withheld, how many, and at which "
    "classification levels, and that they may request elevated access. Do NOT "
    "speculate about the withheld content, do NOT invent document names, topics, "
    "numbers, or any detail beyond the counts and levels given. Output only the "
    "message text."
)

# --------------------------------------------------------------------------- #
# LLM-powered filter query parser (aiify-opp-29: fulltext_search_engine ->
# llm_generation, analog of paperless src/documents/filters.py). The external
# pattern hand-codes a Django FilterSet of metadata fields (date, type,
# classification, etc.) that the user must configure manually. This layer asks
# the LLM to extract those same filter intents from natural language so users
# can type "recent CUI PDFs from last month" and get back structured params.
# The model emits ONLY the JSON keys listed below — never document content,
# never proper nouns, never invented field values — so the extracted filter is
# always safe to apply without sanitizing free-text LLM output through SQL.
# --------------------------------------------------------------------------- #

# Allowed classification values: only exact schema values are permitted so
# the filter predicate never silently drops results (unknown label → no match).
_FILTER_VALID_CLASSIFICATIONS = frozenset({"UNCLASSIFIED", "CUI", "SECRET", "TOP SECRET"})

# Allowed content_type prefixes — normalized to lowercase before comparison.
_FILTER_VALID_CONTENT_TYPES = frozenset({
    "pdf", "docx", "doc", "txt", "md", "xlsx", "xls", "csv", "pptx", "ppt",
    "json", "xml", "html", "htm", "odt", "ods", "odp",
})

_FILTER_SYSTEM_PROMPT = (
    "You are a document search filter extractor. Given a natural language description "
    "of what documents to find, output ONLY a valid JSON object with these optional "
    "keys — include a key only when the user's description clearly implies it:\n"
    "  classification: one of UNCLASSIFIED, CUI, SECRET, TOP SECRET\n"
    "  content_type: file extension (pdf, docx, txt, etc.)\n"
    "  date_range_days: integer number of days back from today (e.g. 30)\n"
    "  date_after: ISO date string YYYY-MM-DD\n"
    "  date_before: ISO date string YYYY-MM-DD\n"
    "  title_contains: keyword or short phrase that should appear in the document title\n"
    "  collection_id: exact collection identifier if explicitly named\n"
    "  min_pages: minimum page count (integer)\n"
    "  max_pages: maximum page count (integer)\n"
    "  confidence: your confidence (0.0-1.0) that you extracted the filters correctly\n"
    "Do NOT invent values. Do NOT answer the user's question. Do NOT add keys not "
    "listed above. If you cannot extract any filter, output exactly: {}"
)

_FILTER_NONE_SENTINEL = "{}"

# --------------------------------------------------------------------------- #
# LLM-powered query intent classifier (aiify-opp-28: fulltext_search_engine ->
# llm_generation, analog of paperless src/documents/filters.py). The external
# pattern uses a static DocumentSearchFilter that combines fulltext + metadata
# search but requires the caller to configure every field manually. This layer
# asks the LLM to assess the *intent* of a search query and recommend the
# optimal DIC retrieval strategy (mode, expansion, filtering, synthesis) as a
# structured decision object. The model outputs a schema-constrained JSON object
# of boolean flags and an intent type — it never answers the question, never
# invents document content, and degrades gracefully (all flags False, llm_used
# False) when unavailable, so callers can always treat the result as a safe hint
# rather than a hard dependency.
# --------------------------------------------------------------------------- #

_INTENT_VALID_TYPES = frozenset({
    "factual_qa", "document_search", "filtered_search", "broad_exploration",
})
_INTENT_VALID_MODES = frozenset({"grounded", "hybrid"})
_INTENT_DEFAULT_TYPE = "document_search"
_INTENT_MAX_TOKENS = 160

_INTENT_SYSTEM_PROMPT = (
    "You classify search queries for a classified document repository. Given a "
    "user query, output ONLY a valid JSON object with these keys:\n"
    '  "intent_type": one of "factual_qa" | "document_search" | '
    '"filtered_search" | "broad_exploration"\n'
    "    factual_qa: user wants a direct answer (e.g. 'What is the TTX for X?')\n"
    "    document_search: user wants to find specific documents\n"
    "    filtered_search: query implies metadata constraints (dates, types, "
    "classification levels, page counts)\n"
    "    broad_exploration: user wants to explore a topic broadly\n"
    '  "recommended_mode": "grounded" (default) or "hybrid" (for exploration)\n'
    '  "should_expand": true if synonym expansion would improve recall\n'
    '  "should_filter": true if metadata filters are implied by the query\n'
    '  "should_synthesize": true if a direct LLM answer beats a document list\n'
    '  "confidence": float 0.0-1.0\n'
    "Output ONLY the JSON object. No explanation. No commentary."
)


@dataclass
class DICQueryIntent:
    """LLM-classified intent of a DIC fulltext search query.

    Models the upgrade of a static filter+search configuration (paperless
    ``src/documents/filters.py``: ``DocumentSearchFilter`` that combines
    fulltext text search with metadata FilterSet fields the caller must
    configure manually) to a dynamic LLM-assessed query intent
    (aiify-opp-28: fulltext_search_engine -> llm_generation).

    Instead of requiring the caller to select mode, expansion, filtering, and
    synthesis parameters by hand, the LLM reads the raw query and recommends
    the optimal DIC retrieval strategy:

    * ``intent_type`` classifies the query's high-level intent.
    * ``recommended_mode`` is either ``"grounded"`` (BM25+KG) or ``"hybrid"``.
    * ``should_expand`` signals that query expansion would improve recall.
    * ``should_filter`` signals that metadata filtering should be applied.
    * ``should_synthesize`` signals that grounded answer synthesis would serve
      the user better than a raw document list.
    * ``intent_type`` classifies the query's high-level intent — ``"factual_qa"``
      (user wants a direct answer), ``"document_search"`` (user wants specific
      documents), ``"filtered_search"`` (metadata constraints implied), or
      ``"broad_exploration"`` (topic survey).
    * ``recommended_mode`` is either ``"grounded"`` (BM25+KG, the default) or
      ``"hybrid"`` (adds vector similarity, suited to broad exploration).
    * ``should_expand`` signals that query expansion (:meth:`DICSearchEngine.
      expand_query`) would improve recall for this query.
    * ``should_filter`` signals that metadata filtering (:meth:`DICSearchEngine.
      filter_query`) should be applied (i.e. the query implies date, type, or
      classification constraints).
    * ``should_synthesize`` signals that :meth:`DICSearchEngine.answer` (LLM
      grounded answer synthesis) would serve the user better than a raw list.

    All flags are False and ``llm_used`` is False when the model is unavailable
    — callers must treat the intent as a hint, never a hard dependency.
    ``refusal_reason`` is ``"empty_query"`` for blank input or
    ``"llm_unavailable"`` on failure.
    """

    query: str = ""
    intent_type: str = ""
    recommended_mode: str = "grounded"
    should_expand: bool = False
    should_filter: bool = False
    should_synthesize: bool = False
    confidence: float = 0.0
    llm_used: bool = False
    refusal_reason: str = ""
    origin: str = "ai_generated"

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "intent_type": self.intent_type,
            "recommended_mode": self.recommended_mode,
            "should_expand": self.should_expand,
            "should_filter": self.should_filter,
            "should_synthesize": self.should_synthesize,
            "confidence": round(self.confidence, 4),
            "llm_used": self.llm_used,
            "refusal_reason": self.refusal_reason,
            "origin": self.origin,
        }


# --------------------------------------------------------------------------- #
# The filesystem wiki cache was REMOVED here (cef-di-04). It is not coming back.
#
# `_file_qa_to_wiki` wrote high-confidence `answer()` results into the Claude
# Code auto-memory directory as markdown, and `_check_wiki_cache` /
# `_wiki_keyword_search` read them back BEFORE any retrieval ran. That put an
# evidence source outside the database and outside the vector store, in front of
# the canvas's mandatory chokepoint, where four controls this canvas exists to
# enforce could not see it:
#
#   * TENANT. The cache key was `sha256(collection_id | query)` -- no tenant_id
#     anywhere in it, and the reader took no tenant either. One tenant's answer
#     was served verbatim to the next.
#   * CLEARANCE. A cached answer carried no document classification, so the
#     clearance drop in `search()` -- the one this file is careful to run BEFORE
#     the top_k cap -- had nothing to filter and was bypassed entirely.
#   * CITATIONS. A hit returned `grounded=True` with an EMPTY citation list and
#     a `citation_quality` set to the filing threshold rather than measured.
#     This module's own contract is that results are "never returned uncited";
#     the cache was the one path that returned an ungrounded answer labelled
#     grounded.
#   * FRESHNESS. Files were never invalidated: `if topic_file.exists(): return`.
#     A re-ingested or superseded document could not dislodge a cached answer.
#
# The fuzzy lane was worse than the exact one: at >= 0.70 keyword overlap it
# returned a DIFFERENT question's answer as this question's.
#
# It was governed by nobody and it was also INERT: measured 2026-08-18 on the
# live deployment, 0 of 567 files in the auto-memory directory carried the
# `dic-qa-` prefix, so the cache had never filed or served a single answer.
# Removing it is behaviour-preserving in the strict sense.
#
# The alternative -- "bring it under the governed seam" -- was considered and
# rejected: a per-query answer cache already exists inside Cortex
# (`cache.operations` in args/cortex_config.yaml), keyed by the governed context
# and invalidated with it. Re-implementing one on the filesystem, in the user's
# cross-project memory directory, would be a second cache to govern rather than
# a governed cache.
# --------------------------------------------------------------------------- #

class DICSearchEngine:
    """Grounded search over DIC collections.

    Wraps RAGRetriever with DIC-specific citation packing and result filtering.

    LAYERING (cef-di-04) — read this before adding a call to Cortex anywhere in
    this class. ``search()`` IS Cortex's ``dic`` rung: ``search_service.py
    ::search_dic`` constructs a ``DICSearchEngine`` and calls this exact method,
    and ``dic`` is in ``resolve.backends`` in ``args/cortex_config.yaml``. So the
    graph is a cycle by construction and the only question is where it is cut.

    It is cut in :mod:`tools.document_intelligence.search_evidence`, by a
    PROCESS-WIDE interlock — not a thread-local one, because Cortex's fan-out
    submits each backend onto a shared ``ThreadPoolExecutor`` and the re-entrant
    call therefore arrives on a different thread. The rule: **the innermost DIC
    search inside a resolve fan-out is always the raw rung.** DIC asks Cortex;
    Cortex asks DIC; DIC does not ask Cortex again. Depth is bounded at 1.

    Which means: only :meth:`_rag_search` may consult the seam, and it must do so
    through :meth:`_governed_candidates`. A second, unguarded ``cortex.*`` call
    added elsewhere in this class re-opens the cycle, and it will look fine in a
    single-threaded test.
    """

    def __init__(self, tenant_id: str = "default"):
        self._tenant_id = tenant_id

    def search(
        self,
        query: str,
        collection_id: str | None = None,
        top_k: int = 10,
        mode: str = "grounded",
        clearance: str | None = None,
        rerank_attribution: bool = True,
    ) -> list[DICSearchResult]:
        """Return cited search results. Empty list when no evidence found.

        Args:
            query: Natural language query.
            collection_id: Limit to a specific collection (None = all accessible).
            top_k: Maximum results to return.
            mode: "grounded" (BM25+KG, default) or "hybrid" (adds vector+rerank).
            clearance: Caller's maximum classification (e.g. "CUI"). When set,
                results whose document classification outranks the clearance are
                dropped *before* the ``top_k`` cap, so the cap fills with
                accessible results. When None (default), no access filtering is
                applied — behavior is unchanged for existing callers.
            rerank_attribution: When True (default), the accessible candidate set
                is reordered through the attribution lens — chunks that more
                strongly support the query rank higher — before the ``top_k`` cap.
                Every result gets its ``attribution_score`` populated regardless.
                Set False to keep the raw retrieval order.
        """
        from tools.db.storage import get_connection

        max_rank = _clearance_rank(clearance) if clearance else None

        # `clearance` reaches retrieval as well as the drop below. On the
        # governed path it becomes the CortexContext classification, so the
        # fan-out's own read-down applies at every rung; on the direct path the
        # retriever ignores it, exactly as before. Either way the authoritative
        # screen is still the drop below, over the document's OWN classification
        # read from `dic_documents`, and it still runs before the top_k cap.
        raw_results = self._rag_search(
            query, top_k=top_k * 2, mode=mode, collection_id=collection_id,
            clearance=clearance,
        )
        if not raw_results:
            return []

        conn = get_connection()
        try:
            terms = _extract_terms(query)
            out: list[DICSearchResult] = []
            for r in raw_results:
                chunk_id = r.chunk_id or r.source_id or ""
                meta = _chunk_meta(conn, chunk_id)
                doc_id = meta["doc_id"] or r.source_id or ""
                col_id = meta["collection_id"] or ""

                # When a collection filter is requested, exclude chunks whose
                # collection_id doesn't match — including empty-string col_id
                # (chunks from other canvases that share the rag_chunks table).
                if collection_id and col_id != collection_id:
                    continue

                doc_info = _doc_meta(conn, doc_id) if doc_id else {"title": doc_id, "classification": "CUI"}

                # Effective marking is the MORE RESTRICTIVE of the document's own
                # and the one the retrieval rung reported for this candidate.
                #
                # `_doc_meta` answers from `dic_documents` and returns "CUI" when
                # there is no row — which there is not for a candidate from a rung
                # that is not DIC. The governed path can return one (a `kb` entry
                # surfaced as `icdev-tool-…` in the live comparison), and taking
                # `_doc_meta`'s default for it would hand a caller a marking the
                # source never claimed. Max, not override, so this can only ever
                # tighten: `SearchResult.classification` defaults to "CUI", the
                # same rank as `_doc_meta`'s default, so a candidate whose rung
                # reported nothing is unchanged. It differs only when a rung
                # explicitly reported something MORE restrictive than the document
                # row, and returning that to an under-cleared caller is a defect,
                # not a behaviour worth preserving.
                classification = doc_info["classification"]
                reported = getattr(r, "classification", "") or ""
                if reported and _clearance_rank(reported) > _clearance_rank(classification):
                    classification = reported

                # Access control: drop results above the caller's clearance before
                # the top_k cap so accessible results are never starved by withheld
                # ones. Skipped entirely when no clearance is supplied.
                if max_rank is not None and _clearance_rank(classification) > max_rank:
                    continue

                matched = [t for t in terms if t in (r.content or "").lower()]

                citation = Citation(
                    doc_id=doc_id,
                    doc_title=doc_info["title"],
                    page=meta["page"],
                    section=meta["section"],
                    chunk_id=chunk_id,
                    classification=classification,
                )

                out.append(DICSearchResult(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    doc_title=doc_info["title"],
                    collection_id=col_id,
                    content=r.content or "",
                    page=meta["page"],
                    section=meta["section"],
                    score=r.final_score or r.score,
                    matched_terms=matched,
                    citation=citation,
                    sha256=meta.get("sha256", ""),
                    attribution_pct=meta.get("attribution_pct", 0),
                    doc_summary=doc_info.get("summary", ""),
                ))
        finally:
            conn.close()

        # Attribution-lens rerank over the full accessible candidate pool, then
        # cap to top_k so a strongly-supporting chunk is never starved by a
        # weakly-related one that merely retrieved earlier. Always scores
        # attribution_score (even when rerank is disabled).
        out = _rerank_by_attribution(query, out, rerank=rerank_attribution)
        return out[:top_k]

    def grounded_search(
        self,
        query: str,
        collection_id: str | None = None,
        top_k: int = 10,
        mode: str = "grounded",
        clearance: str | None = None,
    ) -> DICGroundedSearch:
        """Cited search with an attribution-lens ordering and a sufficiency score.

        Thin wrapper over :meth:`search` (which already reranks through the
        attribution lens) that also computes a per-answer ``citation_quality`` —
        the mean attribution strength of the returned chunks — so callers can show
        how well the cited evidence actually supports the query. Citations remain
        mandatory; results with no traceable source are never returned.

        Args:
            query: Natural language query (user-provided — injection-scanned).
            collection_id: Restrict to a specific collection (None = all).
            top_k: Maximum results to return.
            mode: "grounded" (BM25+KG, default) or "hybrid".
            clearance: Caller's maximum classification; results above it are
                dropped before the cap (see :meth:`search`).

        Returns:
            A :class:`DICGroundedSearch`. ``citation_quality`` is 0.0 when nothing
            matched and ``results`` is empty.
        """
        results = self.search(
            query, collection_id=collection_id, top_k=top_k, mode=mode, clearance=clearance,
        )
        return DICGroundedSearch(
            query=query,
            results=results,
            result_count=len(results),
            citation_quality=_citation_quality(results),
            anomaly_report=detect_search_anomalies(results),
        )

    @staticmethod
    def _search_evidence_module():
        """The ONE copy of the governed evidence seam this process talks to.

        ``search_evidence`` ships byte-identical under ``tools/`` and
        ``icdev/tools/`` and they are SEPARATE module objects with separate run
        state. Resolving through here (``icdev`` first, matching the canonical
        namespace) means a process only ever touches one -- and a test patches
        what this returns, never a namespace it guessed at.

        Returns ``None`` when neither imports, which reads as "seam off" at
        every call site.
        """
        import importlib

        for name in (
            "icdev.tools.document_intelligence.search_evidence",
            "tools.document_intelligence.search_evidence",
        ):
            try:
                return importlib.import_module(name)
            except Exception:  # noqa: BLE001 -- try the other tree
                continue
        return None

    def _governed_candidates(
        self, query: str, top_k: int, collection_id: str | None, clearance: str | None
    ):
        """Governed candidates for ``query``, or ``None`` for the legacy path.

        The whole of the cef-di-04 migration is this call plus the branch in
        :meth:`_rag_search` below. Everything the caller does WITH a candidate --
        the citation pack, the collection post-filter, the clearance drop before
        the ``top_k`` cap, the attribution rerank -- is deliberately downstream
        of here and identical on both paths.
        """
        module = self._search_evidence_module()
        if module is None:
            return None
        try:
            return module.resolve_evidence(
                query,
                collection_id=collection_id,
                tenant_id=self._tenant_id,
                clearance=clearance,
                top_k=top_k,
            )
        except Exception as exc:  # noqa: BLE001 -- the seam can never fail a search
            logger.warning(
                "DICSearchEngine: governed evidence seam failed (%s) — direct retriever",
                exc,
            )
            return None

    def _rag_search(
        self,
        query: str,
        top_k: int,
        mode: str,
        collection_id: str | None = None,
        clearance: str | None = None,
    ):
        """Candidate retrieval. Governed seam first when armed, direct retriever always.

        The governed path (``cortex.enabled`` in ``args/dic_search_config.yaml``,
        DEFAULT OFF) resolves the query through ``cortex.resolve`` -- one call
        that fans out over the currency store, RAG, DIC, the knowledge graph and
        the KB under the TRUST chain -- and returns candidates in exactly the
        shape this method has always returned, so nothing downstream changes.

        Three ways it hands back to the direct retriever, and all three are the
        pre-migration behaviour rather than a failure: the seam declined
        (``None`` -- off, re-entrant, collection-scoped, capped, or no Cortex),
        the resolution was REFUSED by governance (a bundle carrying ``blocked``),
        or it came back with no candidate at all and ``fallback_on_empty`` is on.
        """
        bundle = self._governed_candidates(query, top_k, collection_id, clearance)
        if bundle is not None:
            module = self._search_evidence_module()
            if not bundle.is_empty:
                return bundle.candidates
            # Empty is not the same as "the seam did not run": a governance
            # refusal and a dead fan-out are both empty here, and `errors` /
            # `blocked` on the bundle are what tell them apart from a corpus
            # that genuinely matched nothing.
            logger.info(
                "DICSearchEngine: governed evidence empty for %r "
                "(blocked=%s, backends=%s, errors=%s)",
                query[:60], bundle.blocked or "-", bundle.backends, bundle.errors,
            )
            if module is not None and not module.fallback_on_empty():
                return []

        try:
            from tools.rag.retriever import RAGRetriever

            retriever = RAGRetriever(tenant_id=self._tenant_id)
            rerank = mode == "hybrid"
            # Scope vector retrieval to the collection when one is requested so
            # the returned chunk ids are the ones actually linked to that collection.
            return retriever.search(
                query,
                top_k=top_k,
                rerank=rerank,
                project_id=collection_id or "",
            )
        except Exception as exc:
            logger.warning("DICSearchEngine: RAG search failed (%s), trying BM25 fallback", exc)
            return self._bm25_fallback(query, top_k)

    def _bm25_fallback(self, query: str, top_k: int) -> list:
        """Pure-text BM25 fallback when vector store unavailable (air-gap)."""
        from tools.db.storage import get_connection
        from tools.rag.vector_store_provider import SearchResult

        terms = _extract_terms(query)
        if not terms:
            return []
        conn = get_connection()
        try:
            # One placeholder style per statement. This mixed SQLite `?` with
            # `%s` for LIMIT, so psycopg2 raised on every call and the outer
            # `except` returned [] — the keyword safety net was dead on the
            # PRIMARY backend, silently, for every query that reached it.
            like_clauses = " OR ".join(["content LIKE %s" for _ in terms])
            params = [f"%{t}%" for t in terms]
            cur = conn.execute(
                f"SELECT chunk_id, content, source_id FROM rag_chunks WHERE {like_clauses} LIMIT %s",
                params + [top_k],
            )
            rows = cur.fetchall()
            results = []
            for row in rows:
                r = SearchResult(chunk_id=row[0], content=row[1] or "", source_id=row[2] or "")
                r.final_score = sum(1 for t in terms if t in (row[1] or "").lower()) / max(len(terms), 1)
                results.append(r)
            return sorted(results, key=lambda x: x.final_score, reverse=True)
        except Exception as exc:
            logger.warning("DICSearchEngine: BM25 fallback failed: %s", exc)
            return []
        finally:
            conn.close()

    def _embeddings_available(self) -> bool:
        """Report whether an embedding provider is configured for vector search.

        Used by :meth:`keyword_search` to decide between true embedding search and
        the literal keyword fallback, and to set ``embedding_used`` honestly. Any
        failure to resolve the provider is treated as "unavailable" so callers
        degrade gracefully (air-gap safe) rather than erroring.
        """
        try:
            from tools.rag.retriever import _get_embedding_provider

            return _get_embedding_provider() is not None
        except Exception:
            return False

    def keyword_search(
        self,
        keywords: list[str],
        collection_id: str | None = None,
        top_k: int = 10,
        clearance: str | None = None,
    ) -> DICKeywordSearchResult:
        """Embedding-based search over a literal list of keywords.

        This upgrades a classic keyword-list filter — where a document matches
        only if it contains one of an exact set of keywords — to semantic
        embedding search: the keywords are embedded as a single query and matched
        against chunk embeddings, so a document is retrieved when it is
        *semantically* related to the keywords even if it never uses the literal
        terms. Results carry full citations and honor the caller's ``clearance``
        exactly as :meth:`search` does.

        When no embedding provider is available (air-gap / no vector store), the
        method degrades to literal keyword matching via the BM25 fallback path,
        so it is never worse than the keyword-list baseline. ``embedding_used`` on
        the returned object records which path actually ran.

        Args:
            keywords: Literal keywords to search for. Trimmed, de-duplicated
                (case-insensitively), and order-preserved before use.
            collection_id: Restrict to a specific collection (None = all).
            top_k: Maximum results to return.
            clearance: Caller's maximum classification; results above it are
                dropped before the ``top_k`` cap (see :meth:`search`).

        Returns:
            A :class:`DICKeywordSearchResult`. ``refusal_reason`` is
            ``"no_keywords"`` when the list is empty/blank (no search is run) and
            ``"no_matches"`` when the search found nothing; it is empty on success.
        """
        norm = _normalize_keywords(keywords)
        if not norm:
            return DICKeywordSearchResult(
                keywords=[], embedding_used=False, refusal_reason="no_keywords",
            )

        embedding_used = self._embeddings_available()
        # "hybrid" engages the vector+rerank path when embeddings are present;
        # "grounded" still falls back to literal keyword matching when they are
        # not, so the keyword list always yields the baseline behavior at worst.
        mode = "hybrid" if embedding_used else "grounded"
        query = " ".join(norm)
        results = self.search(
            query, collection_id=collection_id, top_k=top_k, mode=mode, clearance=clearance,
        )
        if not results:
            return DICKeywordSearchResult(
                keywords=norm, embedding_used=embedding_used, refusal_reason="no_matches",
            )

        return DICKeywordSearchResult(
            keywords=norm,
            results=results,
            embedding_used=embedding_used,
            result_count=len(results),
        )

    def answer(
        self,
        query: str,
        collection_id: str | None = None,
        top_k: int = 10,
        mode: str = "grounded",
        clearance: str | None = None,
    ) -> DICAnswer:
        """Synthesize a grounded LLM answer over cited DIC search results.

        This is an OPT-IN layer on top of :meth:`search`. It first runs the
        normal cited search, then asks the LLM to compose an answer using ONLY
        the retrieved excerpts, citing each with a bracketed ``[n]`` marker.

        Args:
            query: Natural language question (user-provided — injection-scanned).
            collection_id: Restrict to a specific collection (None = all).
            top_k: How many results to retrieve before synthesis.
            mode: "grounded" (BM25+KG, default) or "hybrid".
            clearance: Caller's maximum classification; results above it are
                dropped before the ``top_k`` cap (see :meth:`search`). ``None``
                (the default) applies no access filtering, which is what this
                method did unconditionally before cef-di-04 -- it called
                :meth:`search` with no clearance at all, so a synthesized answer
                could be composed over evidence the caller could not have been
                shown by :meth:`search` itself.

        Returns:
            A :class:`DICAnswer`. ``grounded`` is True only when the LLM produced
            a real answer from the evidence. It is False (with ``refusal_reason``)
            when no results were found ("no_evidence"), the LLM is unavailable
            ("llm_unavailable"), or the model declined for lack of grounding
            ("insufficient_evidence"). The answer is NEVER fabricated.
        """
        results = self.search(
            query, collection_id=collection_id, top_k=top_k, mode=mode,
            clearance=clearance,
        )
        if not results:
            return DICAnswer(grounded=False, refusal_reason="no_evidence", result_count=0)

        used = results[:_ANSWER_MAX_RESULTS]
        # Per-answer citation sufficiency over the evidence actually fed to
        # synthesis (attribution lens). search() already ordered results by
        # attribution strength, so the strongest-supporting chunks are used.
        cq = _citation_quality(used)
        context = "\n\n".join(
            f"[{i}] (source: {r.doc_title or r.doc_id or 'unknown'})\n"
            f"{(r.content or '').strip()[:_ANSWER_CHARS_PER_RESULT]}"
            for i, r in enumerate(used, start=1)
        )

        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter

            req = LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Question: {query}\n\n"
                            "Numbered source excerpts:\n"
                            f"{context}\n\n"
                            "Answer the question using only these excerpts, citing "
                            "each claim with its [n] marker."
                        ),
                    }
                ],
                system_prompt=_ANSWER_SYSTEM_PROMPT,
                max_tokens=512,
                temperature=0.1,
                classification="CUI",
            )
            resp = LLMRouter().invoke("summarization", req)
        except Exception:
            return DICAnswer(
                grounded=False,
                refusal_reason="llm_unavailable",
                citations=[r.citation for r in used],
                result_count=len(results),
                citation_quality=cq,
            )

        if not resp or not resp.content or not resp.content.strip():
            return DICAnswer(
                grounded=False,
                refusal_reason="llm_unavailable",
                citations=[r.citation for r in used],
                result_count=len(results),
                citation_quality=cq,
            )

        text = resp.content.strip()
        if _ANSWER_REFUSAL_SENTINEL in text:
            return DICAnswer(
                grounded=False,
                refusal_reason="insufficient_evidence",
                citations=[r.citation for r in used],
                result_count=len(results),
                citation_quality=cq,
            )

        return DICAnswer(
            answer=text,
            grounded=True,
            citations=[r.citation for r in used],
            result_count=len(results),
            citation_quality=cq,
        )

    def expand_query(self, query: str, max_terms: int = _EXPANSION_MAX_TERMS) -> DICQueryExpansion:
        """Suggest extra search keywords to broaden fulltext match recall.

        OPT-IN layer that asks the LLM for synonyms / related keywords for the
        query, then appends them to it. The model is constrained to emit keywords
        only — it never answers the question and never invents proper nouns or
        facts (see :data:`_EXPANSION_SYSTEM_PROMPT`). Returned terms are
        de-duplicated against the query's own words, length-bounded, and capped
        at ``max_terms``.

        Args:
            query: Natural language query (user-provided — injection-scanned).
            max_terms: Maximum number of expansion terms to keep.

        Returns:
            A :class:`DICQueryExpansion`. ``expanded_query`` always falls back to
            the original ``query`` (with ``llm_used`` False and a
            ``refusal_reason``) when the query is empty, the LLM is unavailable,
            or the model returns no usable terms — so callers can always search
            with it safely.
        """
        q = (query or "").strip()
        if not q:
            return DICQueryExpansion(
                original_query="", expanded_query="", refusal_reason="empty_query",
            )

        base_terms = set(_extract_terms(q))

        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter

            req = LLMRequest(
                messages=[{"role": "user", "content": f"Query: {q}\n\nExpansion keywords:"}],
                system_prompt=_EXPANSION_SYSTEM_PROMPT,
                max_tokens=120,
                temperature=0.2,
                classification="CUI",
            )
            resp = LLMRouter().invoke("summarization", req)
        except Exception:
            return DICQueryExpansion(
                original_query=q, expanded_query=q, refusal_reason="llm_unavailable",
            )

        if not resp or not resp.content or not resp.content.strip():
            return DICQueryExpansion(
                original_query=q, expanded_query=q, refusal_reason="llm_unavailable",
            )

        raw = resp.content.strip()
        if _EXPANSION_NONE_SENTINEL in raw.upper().split():
            return DICQueryExpansion(
                original_query=q, expanded_query=q, llm_used=True, refusal_reason="no_terms",
            )

        # Parse the model's comma-separated keywords. Reject overlong "terms"
        # (a returned sentence), strip noise, and drop words already in the query
        # so the expansion is strictly additive.
        terms: list[str] = []
        seen = set(base_terms)
        for piece in raw.replace("\n", ",").split(","):
            term = piece.strip().strip(".;:\"'()[]").lower()
            if not term or len(term) > _EXPANSION_MAX_TERM_LEN:
                continue
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
            if len(terms) >= max_terms:
                break

        if not terms:
            return DICQueryExpansion(
                original_query=q, expanded_query=q, llm_used=True, refusal_reason="no_terms",
            )

        expanded = f"{q} {' '.join(terms)}"
        return DICQueryExpansion(
            original_query=q, terms=terms, expanded_query=expanded, llm_used=True,
        )

    def access_explanation(
        self,
        query: str,
        clearance: str = "CUI",
        collection_id: str | None = None,
        top_k: int = 10,
        mode: str = "grounded",
    ) -> DICAccessExplanation:
        """Explain, in grounded natural language, which matches were withheld.

        Runs the search WITHOUT clearance filtering to see the full candidate
        set, partitions it by the caller's ``clearance``, and — when anything is
        above clearance — asks the LLM to compose a short, non-leaking notice of
        what was withheld and why. The model is fed only the per-level counts and
        the clearance, never document content or the query, so it cannot disclose
        protected material. Falls back to a deterministic template when the LLM is
        unavailable, so ``message`` is always populated.

        Args:
            query: Natural language query.
            clearance: Caller's maximum classification (defaults to "CUI").
            collection_id: Restrict to a specific collection (None = all).
            top_k: How many candidate results to consider.
            mode: "grounded" (BM25+KG, default) or "hybrid".

        Returns:
            A :class:`DICAccessExplanation`. ``withheld_count`` is 0 and
            ``message`` confirms full visibility when nothing is restricted.
        """
        max_rank = _clearance_rank(clearance)
        candidates = self.search(query, collection_id=collection_id, top_k=top_k, mode=mode)

        visible: list[DICSearchResult] = []
        withheld_by_level: dict[str, int] = {}
        for r in candidates:
            cls = r.citation.classification or "CUI"
            if _clearance_rank(cls) > max_rank:
                withheld_by_level[cls] = withheld_by_level.get(cls, 0) + 1
            else:
                visible.append(r)

        withheld_count = sum(withheld_by_level.values())
        if withheld_count == 0:
            return DICAccessExplanation(
                clearance=clearance,
                visible_count=len(visible),
                withheld_count=0,
                withheld_by_level={},
                message="All matching results are within your access level.",
                llm_used=False,
            )

        # Deterministic, leak-free fallback message (also used to ground the LLM).
        breakdown = ", ".join(
            f"{count} at {level}" for level, count in sorted(withheld_by_level.items())
        )
        fallback = (
            f"{withheld_count} matching result(s) were withheld because they are "
            f"classified above your {clearance} clearance ({breakdown}). "
            "Request elevated access to view them."
        )

        message, llm_used = fallback, False
        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter

            req = LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"User clearance: {clearance}\n"
                            f"Withheld matches by classification level: {breakdown}\n"
                            f"Total withheld: {withheld_count}\n\n"
                            "Write the access-control notice."
                        ),
                    }
                ],
                system_prompt=_ACCESS_SYSTEM_PROMPT,
                max_tokens=160,
                temperature=0.1,
                classification="CUI",
            )
            resp = LLMRouter().invoke("summarization", req)
            if resp and resp.content and resp.content.strip():
                message, llm_used = resp.content.strip(), True
        except Exception:
            # Keep the deterministic fallback; never fail the access notice.
            pass

        return DICAccessExplanation(
            clearance=clearance,
            visible_count=len(visible),
            withheld_count=withheld_count,
            withheld_by_level=withheld_by_level,
            message=message,
            llm_used=llm_used,
        )

    def filter_query(self, natural_query: str) -> "DICFilterQuery":
        """Parse natural language into structured document search filters.

        This is the DIC analog of a fulltext-search engine's filter module: instead
        of requiring the caller to know exact field names and operators (the pattern
        in ``src/documents/filters.py``), it uses the LLM to extract filter intent
        from plain English. The model emits ONLY a JSON object of safe, schema-
        aligned parameters — never free-text that would pass through to SQL.

        The returned :class:`DICFilterQuery` always has a usable ``filters`` dict
        (possibly empty) and ``refusal_reason`` is set when nothing was extracted.
        ``llm_used`` is False and the dict is empty when the query is blank or the
        LLM is unavailable, so callers can always call :meth:`filtered_search` with
        the result safely (empty filters → no restriction, full search).

        Args:
            natural_query: Free-text description of desired document attributes,
                e.g. "recent CUI PDFs under 10 pages from the contracts collection".

        Returns:
            A :class:`DICFilterQuery` with ``filters`` populated when extraction
            succeeded, or empty with a ``refusal_reason`` on failure.
        """
        import json as _json

        q = (natural_query or "").strip()
        if not q:
            return DICFilterQuery(
                natural_query="",
                refusal_reason="empty_query",
            )

        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter

            req = LLMRequest(
                messages=[{"role": "user", "content": f"Documents to find: {q}"}],
                system_prompt=_FILTER_SYSTEM_PROMPT,
                max_tokens=256,
                temperature=0.0,
                classification="CUI",
            )
            resp = LLMRouter().invoke("summarization", req)
        except Exception:
            return DICFilterQuery(
                natural_query=q,
                refusal_reason="llm_unavailable",
            )

        if not resp or not resp.content or not resp.content.strip():
            return DICFilterQuery(
                natural_query=q,
                refusal_reason="llm_unavailable",
            )

        raw = resp.content.strip()
        # Strip markdown code fences if present.
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()

        try:
            parsed = _json.loads(raw)
        except Exception:
            # Attempt to extract the first {...} block from a verbose response.
            m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
            if m:
                try:
                    parsed = _json.loads(m.group())
                except Exception:
                    parsed = {}
            else:
                parsed = {}

        if not isinstance(parsed, dict) or not parsed:
            return DICFilterQuery(
                natural_query=q,
                llm_used=True,
                refusal_reason="no_filters",
            )

        # Sanitize: keep only known keys and validate values against the schema.
        safe: dict = {}

        cls_raw = (parsed.get("classification") or "").strip().upper()
        if cls_raw in _FILTER_VALID_CLASSIFICATIONS:
            safe["classification"] = cls_raw

        ct_raw = (parsed.get("content_type") or "").strip().lower().lstrip(".")
        if ct_raw in _FILTER_VALID_CONTENT_TYPES:
            safe["content_type"] = ct_raw

        for key in ("date_after", "date_before"):
            val = (parsed.get(key) or "").strip()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
                safe[key] = val

        dr = parsed.get("date_range_days")
        if isinstance(dr, int) and 1 <= dr <= 3650:
            safe["date_range_days"] = dr
        elif isinstance(dr, float) and 1 <= dr <= 3650:
            safe["date_range_days"] = int(dr)

        tc = (parsed.get("title_contains") or "").strip()
        if tc and len(tc) <= 120:
            safe["title_contains"] = tc

        cid = (parsed.get("collection_id") or "").strip()
        if cid and len(cid) <= 64:
            safe["collection_id"] = cid

        for key in ("min_pages", "max_pages"):
            pv = parsed.get(key)
            if isinstance(pv, (int, float)) and pv >= 0:
                safe[key] = int(pv)

        confidence_raw = parsed.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.8 if safe else 0.0

        if not safe:
            return DICFilterQuery(
                natural_query=q,
                llm_used=True,
                confidence=confidence,
                refusal_reason="no_filters",
            )

        return DICFilterQuery(
            natural_query=q,
            filters=safe,
            confidence=confidence,
            llm_used=True,
        )

    def filtered_search(
        self,
        query: str,
        filters: "dict | DICFilterQuery | None" = None,
        top_k: int = 10,
        mode: str = "grounded",
        clearance: str | None = None,
    ) -> list[DICSearchResult]:
        """Search restricted to documents matching structured metadata filters.

        Combines :meth:`search` with pre-filter document selection. The caller
        supplies either a :class:`DICFilterQuery` (from :meth:`filter_query`) or a
        raw ``dict`` of filter params. The method resolves matching ``doc_id``s from
        ``dic_documents``, runs the normal search, and post-filters results to only
        those whose ``doc_id`` appears in the matching set.

        When ``filters`` is None or empty, behavior is identical to :meth:`search`
        — no documents are excluded, so the method is always safe to call.

        Supported filter keys (matching :data:`_FILTER_SYSTEM_PROMPT`):
        - ``classification``: exact classification level
        - ``content_type``: file extension (pdf, docx, etc.)
        - ``date_after`` / ``date_before``: ISO date bounds on ``created_at``
        - ``date_range_days``: rolling window in days from today
        - ``title_contains``: substring match on title (case-insensitive)
        - ``collection_id``: restrict to exact collection
        - ``min_pages`` / ``max_pages``: page count bounds

        Args:
            query: Natural language search query.
            filters: Structured filter params — dict or DICFilterQuery. None = no filter.
            top_k: Maximum results.
            mode: "grounded" (BM25+KG) or "hybrid" (adds vector+rerank).
            clearance: Caller's maximum classification (see :meth:`search`).

        Returns:
            Cited search results whose source document matches the filters.
            Empty list when no candidates match or the base search returns nothing.
        """
        import datetime as _dt
        from tools.db.storage import get_connection

        fdict: dict = {}
        if isinstance(filters, DICFilterQuery):
            fdict = filters.filters or {}
        elif isinstance(filters, dict):
            fdict = filters

        # Resolve matching doc_ids from dic_documents when any filter is active.
        allowed_doc_ids: set[str] | None = None
        if fdict:
            clauses: list[str] = []
            params: list = []

            # collection_id filter (also accepted directly in search() but we
            # handle it here for consistency with the filter API).
            if "collection_id" in fdict:
                clauses.append("collection_id = ?")
                params.append(fdict["collection_id"])

            if "classification" in fdict:
                clauses.append("classification = ?")
                params.append(fdict["classification"])

            if "content_type" in fdict:
                # Match both bare extension and MIME-type prefixes stored in the DB.
                ct = fdict["content_type"]
                clauses.append("(LOWER(content_type) = ? OR LOWER(filename) LIKE ?)")
                params.extend([ct, f"%.{ct}"])

            if "title_contains" in fdict:
                clauses.append("LOWER(title) LIKE ?")
                params.append(f"%{fdict['title_contains'].lower()}%")

            if "date_after" in fdict:
                clauses.append("created_at >= ?")
                params.append(fdict["date_after"])

            if "date_before" in fdict:
                clauses.append("created_at <= ?")
                params.append(fdict["date_before"])

            if "date_range_days" in fdict:
                cutoff = (
                    _dt.datetime.now(_dt.timezone.utc)
                    - _dt.timedelta(days=int(fdict["date_range_days"]))
                ).strftime("%Y-%m-%d")
                clauses.append("created_at >= ?")
                params.append(cutoff)

            if "min_pages" in fdict:
                clauses.append("page_count >= ?")
                params.append(int(fdict["min_pages"]))

            if "max_pages" in fdict:
                clauses.append("page_count <= ?")
                params.append(int(fdict["max_pages"]))

            if clauses:
                where = " AND ".join(clauses)
                conn = get_connection()
                try:
                    cur = conn.execute(
                        f"SELECT doc_id FROM dic_documents WHERE {where}",
                        params,
                    )
                    allowed_doc_ids = {r[0] for r in cur.fetchall() if r[0]}
                except Exception as exc:
                    logger.warning("DICSearchEngine.filtered_search: filter query failed (%s); ignoring filters", exc)
                    allowed_doc_ids = None
                finally:
                    conn.close()

                if allowed_doc_ids is not None and not allowed_doc_ids:
                    # Filters active but no documents matched — return empty immediately.
                    return []

        # Run the base search with a wider fetch window so the post-filter has
        # enough candidates to fill top_k even after restricting by doc_id.
        fetch_k = top_k * 4 if allowed_doc_ids else top_k
        results = self.search(
            query,
            collection_id=fdict.get("collection_id"),
            top_k=fetch_k,
            mode=mode,
            clearance=clearance,
        )

        if allowed_doc_ids is not None:
            results = [r for r in results if r.doc_id in allowed_doc_ids]

        return results[:top_k]

    def classify_query_intent(self, query: str) -> "DICQueryIntent":
        """Classify a search query's intent to recommend the optimal DIC retrieval strategy.

        This is the DIC analog of paperless's combined ``DocumentSearchFilter`` —
        instead of requiring the caller to manually configure fulltext search mode,
        query expansion, metadata filters, and answer synthesis as separate steps,
        the LLM assesses the query's *intent* and recommends which DIC capabilities
        to apply as a structured decision object.

        The model outputs a schema-constrained JSON object of boolean flags and an
        intent type. It never answers the query, never invents document content, and
        always degrades to a safe all-False default when unavailable (air-gap safe).

        Args:
            query: Natural language search query to classify.

        Returns:
            A :class:`DICQueryIntent`. On failure, ``llm_used`` is False and all
            flags are False — callers can always proceed safely with plain search.
            A :class:`DICQueryIntent`. ``intent_type`` is one of ``"factual_qa"``,
            ``"document_search"``, ``"filtered_search"``, or
            ``"broad_exploration"``. ``should_expand``, ``should_filter``, and
            ``should_synthesize`` are boolean hints for :meth:`expand_query`,
            :meth:`filter_query`, and :meth:`answer` respectively.
            On failure, ``llm_used`` is False and all flags are False — callers
            can always proceed safely with plain :meth:`search`.
        """
        import json as _json

        q = (query or "").strip()
        if not q:
            return DICQueryIntent(query="", refusal_reason="empty_query")

        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter

            req = LLMRequest(
                messages=[{"role": "user", "content": f"Query: {q}"}],
                system_prompt=_INTENT_SYSTEM_PROMPT,
                max_tokens=_INTENT_MAX_TOKENS,
                temperature=0.0,
                classification="CUI",
            )
            resp = LLMRouter().invoke("summarization", req)
        except Exception:
            return DICQueryIntent(query=q, refusal_reason="llm_unavailable")

        if not resp or not resp.content or not resp.content.strip():
            return DICQueryIntent(query=q, refusal_reason="llm_unavailable")

        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()

        try:
            parsed = _json.loads(raw)
        except Exception:
            m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
            if m:
                try:
                    parsed = _json.loads(m.group())
                except Exception:
                    parsed = {}
            else:
                parsed = {}

        if not isinstance(parsed, dict) or not parsed:
            return DICQueryIntent(query=q, llm_used=True, refusal_reason="llm_unavailable")

        intent_raw = (parsed.get("intent_type") or "").strip().lower()
        intent_type = intent_raw if intent_raw in _INTENT_VALID_TYPES else _INTENT_DEFAULT_TYPE

        mode_raw = (parsed.get("recommended_mode") or "grounded").strip().lower()
        recommended_mode = mode_raw if mode_raw in _INTENT_VALID_MODES else "grounded"

        def _bool(key: str) -> bool:
            val = parsed.get(key, False)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
            return bool(val)

        confidence_raw = parsed.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.0

        return DICQueryIntent(
            query=q,
            intent_type=intent_type,
            recommended_mode=recommended_mode,
            should_expand=_bool("should_expand"),
            should_filter=_bool("should_filter"),
            should_synthesize=_bool("should_synthesize"),
            confidence=confidence,
            llm_used=True,
        )

    def generate_snippet(self, query: str, result: "DICSearchResult") -> "DICResultSnippet":
        """Extract the most query-relevant passage from a single search result.

        DIC analog of paperless ``serialisers.py`` highlights: instead of
        truncating ``content`` at a fixed character limit, the LLM is asked to
        identify the passage inside the chunk that most directly relates to the
        query, producing a focused excerpt without generating new content.

        Args:
            query: The original search query (user-provided — injection-scanned
                by construction: passed as user turn, never into system prompt).
            result: A :class:`DICSearchResult` whose ``content`` will be analysed.

        Returns:
            A :class:`DICResultSnippet`. ``llm_used`` is False and ``snippet``
            falls back to ``content[:500]`` when the model is unavailable or the
            chunk has no text. ``refusal_reason`` explains any non-LLM path.
        """
        content = (result.content or "").strip()
        if not content:
            return DICResultSnippet(
                chunk_id=result.chunk_id,
                doc_id=result.doc_id,
                query=query,
                snippet="",
                llm_used=False,
                refusal_reason="empty_content",
            )

        fallback = content[:500]
        truncated = content[:_SNIPPET_MAX_CONTENT_CHARS]

        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter

            req = LLMRequest(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Search query: {query}\n\n"
                            f"Document excerpt:\n{truncated}\n\n"
                            "Extract the single most relevant passage."
                        ),
                    }
                ],
                system_prompt=_SNIPPET_SYSTEM_PROMPT,
                max_tokens=_SNIPPET_MAX_TOKENS,
                temperature=0.0,
                classification="CUI",
            )
            resp = LLMRouter().invoke("summarization", req)
        except Exception:
            return DICResultSnippet(
                chunk_id=result.chunk_id,
                doc_id=result.doc_id,
                query=query,
                snippet=fallback,
                llm_used=False,
                refusal_reason="llm_unavailable",
            )

        if not resp or not resp.content or not resp.content.strip():
            return DICResultSnippet(
                chunk_id=result.chunk_id,
                doc_id=result.doc_id,
                query=query,
                snippet=fallback,
                llm_used=False,
                refusal_reason="llm_unavailable",
            )

        text = resp.content.strip()
        return DICResultSnippet(
            chunk_id=result.chunk_id,
            doc_id=result.doc_id,
            query=query,
            snippet=text,
            llm_used=True,
        )

    def generate_snippets(
        self,
        query: str,
        results: "list[DICSearchResult]",
        top_k: int = _SNIPPET_MAX_RESULTS,
    ) -> "list[DICResultSnippet]":
        """Extract query-focused snippets for a batch of search results.

        Calls :meth:`generate_snippet` for each of the first ``top_k`` results
        (capped at :data:`_SNIPPET_MAX_RESULTS` to bound LLM cost). Results
        beyond the cap receive a raw-fallback snippet (``llm_used=False``) so
        the caller always gets a snippet for every result.

        Args:
            query: The original search query.
            results: Ordered list of :class:`DICSearchResult` objects.
            top_k: Maximum results for which the LLM is invoked. Must be ≤
                :data:`_SNIPPET_MAX_RESULTS`; values above are clamped silently.

        Returns:
            One :class:`DICResultSnippet` per input result, in the same order.
        """
        cap = min(top_k, _SNIPPET_MAX_RESULTS)
        snippets: list[DICResultSnippet] = []
        for idx, r in enumerate(results):
            if idx < cap:
                snippets.append(self.generate_snippet(query, r))
            else:
                # Beyond the LLM cap: raw truncation fallback, never LLM call.
                snippets.append(
                    DICResultSnippet(
                        chunk_id=r.chunk_id,
                        doc_id=r.doc_id,
                        query=query,
                        snippet=(r.content or "")[:500],
                        llm_used=False,
                        refusal_reason="beyond_cap",
                    )
                )
        return snippets
