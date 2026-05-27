# CUI // SP-CTI
"""Reranker provider ABC and implementations (D-RAG-20).

Follows the Provider ABC pattern (D66). Two backends:
- BGERerankerProvider: BGE-reranker-v2-m3 via Ollama (fast, 50-100ms)
- LLMRerankerProvider: qwen3 LLM-based re-ranking (fallback, 2-5s)

Usage:
    from tools.rag.reranker_provider import get_reranker_provider
    provider = get_reranker_provider(config)
    ranked = provider.rerank(query, documents, top_k=5)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple

logger = get_logger("icdev.rag.reranker")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class RerankerProvider(ABC):
    """Abstract base class for re-ranking backends (D-RAG-20, D66 pattern)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier (e.g. 'bge', 'llm')."""

    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
    ) -> List[Tuple[int, float]]:
        """Re-rank documents by relevance to query.

        Args:
            query: Search query.
            documents: List of document texts to rank.
            top_k: Number of top results to return.

        Returns:
            List of (original_index, relevance_score) sorted by relevance descending.
        """

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if this reranker backend is available."""


class BGERerankerProvider(RerankerProvider):
    """BGE-reranker-v2-m3 via Ollama cross-encoder scoring (D-RAG-20).

    Uses Ollama's /api/embed endpoint with query-document pairs to get
    cross-encoder relevance scores. 568M params, Apache 2.0, 50-100ms.
    """

    def __init__(self, model: str = "bge-reranker-v2-m3", base_url: str = ""):
        import os

        self._model = model
        self._base_url = base_url or os.environ.get(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        )
        # SEC/PERF: Reuse HTTP session across rerank calls (connection pooling)
        self._session = None

    @property
    def provider_name(self) -> str:
        return "bge"

    def _get_session(self):
        """Get or create HTTP session for connection reuse."""
        if self._session is None:
            from tools.http.client import get_session

            self._session = get_session()
        return self._session

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
    ) -> List[Tuple[int, float]]:
        """Score query-document pairs via Ollama and rank by relevance."""
        session = self._get_session()

        scores: List[Tuple[int, float]] = []
        for i, doc in enumerate(documents):
            # SEC: Sanitize inputs — strip newlines from query to prevent format injection
            safe_query = query.replace("\n", " ").replace("\r", " ").strip()
            safe_doc = doc[:800].replace("\r", " ").strip()
            pair_text = f"query: {safe_query}\ndocument: {safe_doc}"
            try:
                resp = session.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self._model, "input": pair_text},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                # BGE reranker returns a single relevance score
                embedding = data.get("embeddings", [[]])[0]
                # For cross-encoders, the embedding IS the score
                # If it's a single float, use directly; if vector, use first dim
                if isinstance(embedding, (int, float)):
                    score = float(embedding)
                elif isinstance(embedding, list) and len(embedding) == 1:
                    score = float(embedding[0])
                elif isinstance(embedding, list) and len(embedding) > 0:
                    # Sum or first element as relevance proxy
                    score = float(embedding[0])
                else:
                    score = 0.0
                scores.append((i, score))
            except Exception as exc:
                logger.debug("BGE rerank failed for doc %d: %s", i, exc)
                scores.append((i, 0.0))

        # Sort by score descending, return top_k
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def check_availability(self) -> bool:
        """Check if Ollama has the BGE reranker model."""
        try:
            from tools.http.client import request

            resp = request("GET", f"{self._base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            return any(self._model in m.get("name", "") for m in models)
        except Exception:
            return False


class LLMRerankerProvider(RerankerProvider):
    """qwen3 LLM-based re-ranking via ICDEV™ LLM router (D-RAG-3 original).

    Uses structured prompt to ask qwen3 to rank chunks by relevance.
    Slower (2-5s) but works without specialized reranker model.
    """

    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._max_preview_chars = self._config.get("max_chunk_preview_chars", 400)

    @property
    def provider_name(self) -> str:
        return "llm"

    def _load_rerank_prompt(self) -> str:
        """Load re-ranking prompt from hardprompts/rag_rerank.md."""
        prompt_path = BASE_DIR / "hardprompts" / "rag_rerank.md"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return (
            "You are a relevance scoring assistant. Given a query and a list of text chunks,\n"
            "rank them by relevance to the query. Return a JSON array of chunk indices sorted by\n"
            "relevance (most relevant first). Only include chunks that are actually relevant.\n\n"
            'Output format: {"ranked_indices": [3, 0, 7, 1, 5]}'
        )

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 5,
    ) -> List[Tuple[int, float]]:
        """Re-rank via LLM structured prompt."""
        # Build chunk previews
        previews = []
        for i, doc in enumerate(documents):
            preview = doc[: self._max_preview_chars].strip()
            previews.append(f"[{i}] {preview}")
        chunks_text = "\n\n".join(previews)
        rerank_prompt = self._load_rerank_prompt()

        user_message = (
            f"Query: {query}\n\nChunks to rank:\n{chunks_text}\n\n"
            f'Return a JSON object with "ranked_indices" containing the indices of the '
            f"most relevant chunks, sorted by relevance (most relevant first). "
            f"Return at most {top_k} indices."
        )

        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            router = LLMRouter()
            request = LLMRequest(
                messages=[{"role": "user", "content": user_message}],
                system_prompt=rerank_prompt,
                max_tokens=512,
                temperature=0.1,
                classification="CUI",
            )
            response = router.invoke("rag_rerank", request)

            # Parse response
            content = response.content.strip()
            if "{" in content:
                json_str = content[content.index("{") : content.rindex("}") + 1]
                parsed = json.loads(json_str)
                ranked_indices = parsed.get("ranked_indices", [])
            else:
                ranked_indices = json.loads(content)

            # Validate and build results
            valid = [
                (idx, 1.0 - rank / max(len(ranked_indices), 1))
                for rank, idx in enumerate(ranked_indices)
                if isinstance(idx, int) and 0 <= idx < len(documents)
            ]
            return valid[:top_k]

        except Exception as exc:
            logger.debug("LLM rerank failed: %s", exc)
            # Fallback: return original order
            return [(i, 0.0) for i in range(min(top_k, len(documents)))]

    def check_availability(self) -> bool:
        """LLM reranker is available if LLM router works."""
        try:
            from tools.llm.router import LLMRouter  # noqa: F401

            return True
        except ImportError:
            return False


def get_reranker_provider(config: Optional[dict] = None) -> RerankerProvider:
    """Factory: get the best available reranker (D-RAG-20).

    Tries BGE first (fast), falls back to LLM (slower but always available).

    Args:
        config: Rerank config section from rag_config.yaml.

    Returns:
        Available RerankerProvider instance.
    """
    cfg = config or {}
    method = cfg.get("method", "bge")

    if method in ("bge", "hybrid"):
        bge_model = cfg.get("bge_model", "bge-reranker-v2-m3")
        provider = BGERerankerProvider(model=bge_model)
        if provider.check_availability():
            return provider
        if method == "bge":
            logger.info("BGE model '%s' not available, falling back to LLM reranker", bge_model)

    return LLMRerankerProvider(config=cfg)
