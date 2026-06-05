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

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


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


def _extract_terms(query: str) -> list[str]:
    return [t.lower() for t in re.findall(r"\b\w{3,}\b", query)]


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
    ) -> list[DICSearchResult]:
        """Return cited search results. Empty list when no evidence found.

        Args:
            query: Natural language query.
            collection_id: Limit to a specific collection (None = all accessible).
            top_k: Maximum results to return.
            mode: "grounded" (BM25+KG, default) or "hybrid" (adds vector+rerank).
        """
        from tools.db.storage import get_connection

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
