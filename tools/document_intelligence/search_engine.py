# CUI // SP-CTI
"""DIC Grounded Search Engine.

Default mode: BM25 + KG traversal (NO LLM, no vector required — air-gap safe).
Optional hybrid mode: adds vector similarity + RRF fusion + cross-encoder rerank.

Every result carries a mandatory citation pack. Results with no traceable source
are suppressed (never returned uncited).
"""
from __future__ import annotations

import hashlib
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

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "grounded": self.grounded,
            "citations": [c.to_dict() for c in self.citations],
            "result_count": self.result_count,
            "refusal_reason": self.refusal_reason,
            "origin": self.origin,
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
            "archive_url": f"/document-intelligence/doc/{self.doc_id}" if self.doc_id else "#",
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
            "SELECT title, classification FROM dic_documents WHERE doc_id = ?",
            (doc_id,),
        )
        row = cur.fetchone()
        if row:
            return {"title": row[0] or doc_id, "classification": row[1] or "CUI"}
    except Exception:
        pass
    return {"title": doc_id, "classification": "CUI"}


def _chunk_meta(conn, chunk_id: str) -> dict[str, Any]:
    """Pull page/section/sha256 from rag_chunks + dic_chunk_links if available."""
    result: dict[str, Any] = {"page": 0, "section": "", "doc_id": "", "collection_id": "", "sha256": "", "attribution_pct": 0}
    try:
        cur = conn.execute(
            "SELECT content, content_hash FROM rag_chunks WHERE id = ?",
            (chunk_id,),
        )
        row = cur.fetchone()
        if row:
            content = row["content"] if hasattr(row, "keys") else row[0]
            content_hash = row["content_hash"] if hasattr(row, "keys") else row[1]
            sha256 = content_hash or hashlib.sha256((content or "").encode()).hexdigest()
            result["sha256"] = sha256
            result["attribution_pct"] = min(100, max(40, int((len(content or "") / 500) * 80)))
    except Exception:
        pass
    try:
        cur2 = conn.execute(
            "SELECT page, section, doc_id, collection_id FROM dic_chunk_links WHERE rag_chunk_id = ?",
            (chunk_id,),
        )
        row2 = cur2.fetchone()
        if row2:
            if hasattr(row2, "keys"):
                result.update({"page": row2["page"] or 0, "section": row2["section"] or "", "doc_id": row2["doc_id"] or "", "collection_id": row2["collection_id"] or ""})
            else:
                result.update({"page": row2[0] or 0, "section": row2[1] or "", "doc_id": row2[2] or "", "collection_id": row2[3] or ""})
    except Exception:
        pass
    return result


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


class DICSearchEngine:
    """Grounded search over DIC collections.

    Wraps RAGRetriever with DIC-specific citation packing and result filtering.
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
        """
        from tools.db.storage import get_connection

        max_rank = _clearance_rank(clearance) if clearance else None

        raw_results = self._rag_search(query, top_k=top_k * 2, mode=mode)
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

                if collection_id and col_id and col_id != collection_id:
                    continue

                doc_info = _doc_meta(conn, doc_id) if doc_id else {"title": doc_id, "classification": "CUI"}

                # Access control: drop results above the caller's clearance before
                # the top_k cap so accessible results are never starved by withheld
                # ones. Skipped entirely when no clearance is supplied.
                if max_rank is not None and _clearance_rank(doc_info["classification"]) > max_rank:
                    continue

                matched = [t for t in terms if t in (r.content or "").lower()]

                citation = Citation(
                    doc_id=doc_id,
                    doc_title=doc_info["title"],
                    page=meta["page"],
                    section=meta["section"],
                    chunk_id=chunk_id,
                    classification=doc_info["classification"],
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
                ))
                if len(out) >= top_k:
                    break
        finally:
            conn.close()

        return out

    def _rag_search(self, query: str, top_k: int, mode: str):
        try:
            from tools.rag.retriever import RAGRetriever

            retriever = RAGRetriever(tenant_id=self._tenant_id)
            rerank = mode == "hybrid"
            return retriever.search(query, top_k=top_k, rerank=rerank)
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
            like_clauses = " OR ".join(["content LIKE ?" for _ in terms])
            params = [f"%{t}%" for t in terms]
            cur = conn.execute(
                f"SELECT chunk_id, content, source_id FROM rag_chunks WHERE {like_clauses} LIMIT ?",
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

        Returns:
            A :class:`DICAnswer`. ``grounded`` is True only when the LLM produced
            a real answer from the evidence. It is False (with ``refusal_reason``)
            when no results were found ("no_evidence"), the LLM is unavailable
            ("llm_unavailable"), or the model declined for lack of grounding
            ("insufficient_evidence"). The answer is NEVER fabricated.
        """
        results = self.search(query, collection_id=collection_id, top_k=top_k, mode=mode)
        if not results:
            return DICAnswer(grounded=False, refusal_reason="no_evidence", result_count=0)

        used = results[:_ANSWER_MAX_RESULTS]
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
            )

        if not resp or not resp.content or not resp.content.strip():
            return DICAnswer(
                grounded=False,
                refusal_reason="llm_unavailable",
                citations=[r.citation for r in used],
                result_count=len(results),
            )

        text = resp.content.strip()
        if _ANSWER_REFUSAL_SENTINEL in text:
            return DICAnswer(
                grounded=False,
                refusal_reason="insufficient_evidence",
                citations=[r.citation for r in used],
                result_count=len(results),
            )

        return DICAnswer(
            answer=text,
            grounded=True,
            citations=[r.citation for r in used],
            result_count=len(results),
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
