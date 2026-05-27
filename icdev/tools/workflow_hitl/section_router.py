# CUI // SP-CTI
"""Section router — assigns RAG chunks to report sections via semantic search.

For each section:
  1. Semantic search rag_chunks filtered to applicable wf_doc chunks
  2. Score and rank results
  3. Cross-section greedy deduplication (each chunk assigned to highest-scoring section)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import dataclasses
import json
from typing import List, Optional

from tools.workflow_hitl.report_schema import ReportSection

logger = get_logger(__name__)


@dataclasses.dataclass
class RoutedChunk:
    chunk_id: str
    content: str
    relevance_score: float
    rank: int
    section_heading: str = ""
    filename: str = ""
    page_number: Optional[int] = None
    wf_document_template_id: str = ""


@dataclasses.dataclass
class SectionRouteResult:
    section_key: str
    section_name: str
    chunks: List[RoutedChunk]


class SectionRouter:
    """Routes RAG chunks to report sections using semantic similarity with cross-section dedup."""

    def __init__(self, project_id: str = "", top_k_per_section: int = 50):
        self.project_id = project_id
        self.top_k_per_section = top_k_per_section

    def route(
        self,
        sections: List[ReportSection],
        applicable_template_ids: List[str],
    ) -> List[SectionRouteResult]:
        """Main entry point. Returns one SectionRouteResult per section."""
        if not sections:
            return []

        # Step 1: collect raw candidates per section
        candidates: dict[str, list[RoutedChunk]] = {}
        for section in sections:
            raw = self._search_for_section(section, applicable_template_ids)
            candidates[section.key] = raw

        # Step 2: greedy cross-section deduplication
        return self._deduplicate(sections, candidates)

    def _search_for_section(
        self,
        section: ReportSection,
        applicable_template_ids: list[str],
    ) -> list[RoutedChunk]:
        query = f"{section.name}: {section.description}"

        # Try semantic search via Retriever
        try:
            from tools.rag.retriever import Retriever
            retriever = Retriever(project_id=self.project_id)
            results = retriever.search(
                query=query,
                top_k=self.top_k_per_section,
                source_types=["wf_doc"],
            )
            chunks = []
            for r in results:
                meta = self._parse_meta(r)
                tmpl_id = meta.get("wf_document_template_id", "")
                if applicable_template_ids and tmpl_id not in applicable_template_ids:
                    continue
                score = float(getattr(r, "final_score", None) or getattr(r, "score", 0.0))
                chunks.append(RoutedChunk(
                    chunk_id=r.chunk_id,
                    content=r.content,
                    relevance_score=score,
                    rank=0,  # assigned after dedup
                    section_heading=meta.get("section_heading", ""),
                    filename=meta.get("filename", ""),
                    page_number=meta.get("page_number"),
                    wf_document_template_id=tmpl_id,
                ))
            return chunks
        except Exception as exc:
            logger.warning("SectionRouter: semantic search failed for %r — falling back to text: %s",
                           section.key, exc)
            return self._text_search_fallback(query, applicable_template_ids)

    def _text_search_fallback(
        self,
        query: str,
        applicable_template_ids: list[str],
    ) -> list[RoutedChunk]:
        """BM25-style: simple keyword overlap scoring against rag_chunks content."""
        from tools.db.storage import get_connection
        query_words = set(query.lower().split())
        conn = get_connection()
        try:
            if applicable_template_ids:
                # Find ingest IDs for these templates, then match chunks
                placeholders = ",".join("?" * len(applicable_template_ids))
                ingest_rows = conn.execute(
                    f"SELECT id FROM wf_ingested_files WHERE doc_template_id IN ({placeholders})",
                    applicable_template_ids,
                ).fetchall()
                ingest_ids = [r[0] for r in ingest_rows]
                if not ingest_ids:
                    return []
                ph2 = ",".join("?" * len(ingest_ids))
                rows = conn.execute(
                    f"SELECT id, content, metadata FROM rag_chunks "
                    f"WHERE source_type='wf_doc' AND source_id IN ({ph2}) LIMIT 200",
                    ingest_ids,
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, content, metadata FROM rag_chunks "
                    "WHERE source_type='wf_doc' LIMIT 200"
                ).fetchall()
        finally:
            conn.close()

        scored = []
        for row in rows:
            content = row[0] if isinstance(row, tuple) else row["content"]
            chunk_id = row[0] if isinstance(row, tuple) else row["id"]
            meta_str = row[2] if isinstance(row, tuple) else row["metadata"]
            meta = self._parse_meta_str(meta_str)
            words = set(content.lower().split())
            overlap = len(query_words & words)
            score = overlap / max(len(query_words), 1)
            if score > 0:
                scored.append(RoutedChunk(
                    chunk_id=chunk_id,
                    content=content,
                    relevance_score=score,
                    rank=0,
                    section_heading=meta.get("section_heading", ""),
                    filename=meta.get("filename", ""),
                    wf_document_template_id=meta.get("wf_document_template_id", ""),
                ))

        scored.sort(key=lambda c: c.relevance_score, reverse=True)
        return scored[:self.top_k_per_section]

    def _deduplicate(
        self,
        sections: List[ReportSection],
        candidates: dict[str, list[RoutedChunk]],
    ) -> List[SectionRouteResult]:
        """Greedy cross-section deduplication.

        Each chunk is assigned to the section where it has the highest relevance score.
        Ties go to the section with lower sort_order.
        """
        # Build global map: chunk_id → (best_score, best_section_key)
        best_section: dict[str, tuple[float, str]] = {}
        for section in sorted(sections, key=lambda s: s.sort_order):
            for chunk in candidates.get(section.key, []):
                current_best = best_section.get(chunk.chunk_id)
                if current_best is None or chunk.relevance_score > current_best[0]:
                    best_section[chunk.chunk_id] = (chunk.relevance_score, section.key)

        results = []
        for section in sorted(sections, key=lambda s: s.sort_order):
            assigned = [
                c for c in candidates.get(section.key, [])
                if best_section.get(c.chunk_id, (0, ""))[1] == section.key
            ]
            assigned.sort(key=lambda c: c.relevance_score, reverse=True)
            assigned = assigned[:section.max_chunks]
            for rank, chunk in enumerate(assigned, 1):
                chunk.rank = rank
            results.append(SectionRouteResult(
                section_key=section.key,
                section_name=section.name,
                chunks=assigned,
            ))
        return results

    @staticmethod
    def _parse_meta(result) -> dict:
        meta = getattr(result, "metadata", {}) or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return meta

    @staticmethod
    def _parse_meta_str(meta_str) -> dict:
        if not meta_str:
            return {}
        if isinstance(meta_str, dict):
            return meta_str
        try:
            return json.loads(meta_str)
        except Exception:
            return {}
