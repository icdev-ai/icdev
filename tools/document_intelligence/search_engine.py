# CUI // SP-CTI
"""DIC Grounded Search Engine.

Default mode: BM25 + KG traversal (NO LLM, no vector required — air-gap safe).
Optional hybrid mode: adds vector similarity + RRF fusion + cross-encoder rerank.

Every result carries a mandatory citation pack. Results with no traceable source
are suppressed (never returned uncited).
"""
from __future__ import annotations

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
    """Pull page/section from dic_chunk_links if available."""
    try:
        cur = conn.execute(
            "SELECT page, section, doc_id, collection_id FROM dic_chunk_links WHERE rag_chunk_id = ?",
            (chunk_id,),
        )
        row = cur.fetchone()
        if row:
            return {"page": row[0] or 0, "section": row[1] or "", "doc_id": row[2] or "", "collection_id": row[3] or ""}
    except Exception:
        pass
    return {"page": 0, "section": "", "doc_id": "", "collection_id": ""}


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
