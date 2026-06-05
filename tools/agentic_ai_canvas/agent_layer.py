# CUI // SP-CTI
"""Agentic Research Pipeline — Agent Layer.

Implements the Research Agent node (researcher-agent type) from the
'Agentic Research Pipeline' AADC template.

The ResearchAgent orchestrates two upstream nodes:
  web-search  →  WebSearcher  (retrieves raw documents for the query)
  chunker     →  TextChunker  (splits raw docs into model-ready chunks)

Public API:
    from tools.agentic_ai_canvas.agent_layer import ResearchAgent
    agent = ResearchAgent()
    result = agent.search("What is FedRAMP AC-2?")
    # result.chunks  → list[str] ready for AgenticResearchPipeline.run()
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("icdev.aadc.agent_layer")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    score: float = 1.0


@dataclass
class ResearchResult:
    query: str
    chunks: List[str] = field(default_factory=list)
    sources: List[SearchHit] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str = ""


# ---------------------------------------------------------------------------
# WebSearcher — web-search node
# ---------------------------------------------------------------------------

class WebSearcher:
    """Retrieves raw search results for a query.

    Resolution order:
      1. Tavily API (TAVILY_API_KEY in env) — recommended, structured JSON
      2. SerpAPI  (SERP_API_KEY in env)     — alternative commercial API
      3. DuckDuckGo Lite HTML scrape         — no-key fallback, air-gap unsafe
      4. Empty list                           — graceful offline degradation

    All network failures are caught; callers receive SearchHit[] or [].
    """

    def __init__(self, max_results: int = 10) -> None:
        self.max_results = max_results

    def search(self, query: str) -> List[SearchHit]:
        """Return up to self.max_results SearchHit objects for the query."""
        hits = self._try_tavily(query)
        if hits is not None:
            return hits[: self.max_results]

        hits = self._try_serpapi(query)
        if hits is not None:
            return hits[: self.max_results]

        hits = self._try_duckduckgo(query)
        if hits is not None:
            return hits[: self.max_results]

        logger.warning("WebSearcher: all search backends unavailable — returning empty")
        return []

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    def _try_tavily(self, query: str) -> Optional[List[SearchHit]]:
        import os
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            return None
        try:
            import requests
            resp = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": self.max_results},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                SearchHit(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    score=float(r.get("score", 1.0)),
                )
                for r in data.get("results", [])
            ]
        except Exception as exc:
            logger.debug("WebSearcher._try_tavily error: %s", exc)
            return None

    def _try_serpapi(self, query: str) -> Optional[List[SearchHit]]:
        import os
        api_key = os.environ.get("SERP_API_KEY", "")
        if not api_key:
            return None
        try:
            import requests
            resp = requests.get(
                "https://serpapi.com/search",
                params={"q": query, "api_key": api_key, "num": self.max_results},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                SearchHit(
                    title=r.get("title", ""),
                    url=r.get("link", ""),
                    snippet=r.get("snippet", ""),
                )
                for r in data.get("organic_results", [])
            ]
        except Exception as exc:
            logger.debug("WebSearcher._try_serpapi error: %s", exc)
            return None

    def _try_duckduckgo(self, query: str) -> Optional[List[SearchHit]]:
        try:
            import requests
            from urllib.parse import quote_plus
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            headers = {"User-Agent": "Mozilla/5.0 (compatible; ICDEVResearchAgent/1.0)"}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            # Minimal HTML parse — extract result snippets without lxml dependency
            import re
            snippets = re.findall(
                r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL
            )
            titles = re.findall(
                r'class="result__a"[^>]*>(.*?)</a>', resp.text, re.DOTALL
            )
            urls = re.findall(
                r'href="(https?://[^"]+)"', resp.text
            )

            hits: List[SearchHit] = []
            for i, snippet in enumerate(snippets[: self.max_results]):
                clean = re.sub(r"<[^>]+>", " ", snippet).strip()
                hits.append(SearchHit(
                    title=re.sub(r"<[^>]+>", " ", titles[i]).strip() if i < len(titles) else "",
                    url=urls[i] if i < len(urls) else "",
                    snippet=clean,
                ))
            return hits if hits else None
        except Exception as exc:
            logger.debug("WebSearcher._try_duckduckgo error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# TextChunker — chunker node
# ---------------------------------------------------------------------------

class TextChunker:
    """Splits raw text hits into model-ready chunks.

    Wraps tools.rag.chunker.chunk_content (D-RAG-4) when available.
    Falls back to a simple sentence-boundary splitter otherwise.
    """

    def __init__(self, max_chunk_size: int = 1500, overlap: int = 150) -> None:
        self.max_chunk_size = max_chunk_size
        self.overlap = overlap

    def chunk(self, hits: List[SearchHit]) -> List[str]:
        """Convert a list of SearchHits into plain-text chunks."""
        raw_texts = [
            f"{h.title}\n{h.snippet}".strip()
            for h in hits
            if h.snippet.strip()
        ]
        if not raw_texts:
            return []

        chunks: List[str] = []
        for text in raw_texts:
            chunks.extend(self._chunk_one(text))
        return [c for c in chunks if c.strip()]

    def _chunk_one(self, text: str) -> List[str]:
        try:
            from tools.rag.chunker import chunk_content
            result = chunk_content(
                content=text,
                source_type="web_search",
                source_id=f"ws-{uuid.uuid4().hex[:8]}",
                classification="CUI",
            )
            return [c.content for c in result if c.content.strip()]
        except Exception:
            return self._simple_chunk(text)

    def _simple_chunk(self, text: str) -> List[str]:
        """Paragraph-boundary chunker with sliding overlap."""
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        chunks: List[str] = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 1 > self.max_chunk_size and current:
                chunks.append(current.strip())
                # carry overlap forward
                current = current[-self.overlap:] + "\n" + para
            else:
                current = (current + "\n" + para).strip()
        if current.strip():
            chunks.append(current.strip())
        return chunks


# ---------------------------------------------------------------------------
# ResearchAgent — orchestrates web-search + chunker nodes
# ---------------------------------------------------------------------------

class ResearchAgent:
    """Research Agent — the researcher-agent node in the Agentic Research Pipeline.

    Orchestrates the web-search and chunker nodes upstream of the Model Layer.
    Complies with NIST AI RMF MAP-1 (system boundary defined), autonomy level
    L3 (Human-Initiated): executes autonomously when given a query but requires
    a circuit-breaker + confidence-threshold downstream (Safety Layer).

    Args:
        max_results: Maximum web search hits to retrieve.
        max_chunk_size: Maximum characters per text chunk.
        overlap: Character overlap between adjacent chunks.
    """

    def __init__(
        self,
        max_results: int = 10,
        max_chunk_size: int = 1500,
        overlap: int = 150,
    ) -> None:
        self.searcher = WebSearcher(max_results=max_results)
        self.chunker = TextChunker(max_chunk_size=max_chunk_size, overlap=overlap)

    def search(self, query: str) -> ResearchResult:
        """Execute the research agent pipeline for a query.

        Args:
            query: Research question or keyword string.

        Returns:
            ResearchResult with .chunks (ready for AgenticResearchPipeline.run())
            and .sources (for citation / provenance).  Never raises — all errors
            are captured in ResearchResult.error and degrade to empty chunks.
        """
        t0 = time.monotonic()
        try:
            hits = self.searcher.search(query)
            chunks = self.chunker.chunk(hits)
            duration = (time.monotonic() - t0) * 1000
            logger.info(
                "ResearchAgent: query=%r → %d hits, %d chunks in %.0fms",
                query[:80], len(hits), len(chunks), duration,
            )
            return ResearchResult(
                query=query,
                chunks=chunks,
                sources=hits,
                duration_ms=round(duration, 1),
            )
        except Exception as exc:
            duration = (time.monotonic() - t0) * 1000
            logger.warning("ResearchAgent.search failed: %s", exc)
            return ResearchResult(
                query=query,
                duration_ms=round(duration, 1),
                error=str(exc),
            )
