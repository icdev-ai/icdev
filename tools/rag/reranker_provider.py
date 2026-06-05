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
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple

logger = get_logger("icdev.rag.reranker")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under reranker.anomaly_detection (and the rag.rerank section).  Change
# config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_TOP_K        = 5      # default number of ranked results returned
_BGE_DOC_CHARS        = 800    # per-document chars sent to BGE cross-encoder
_BGE_EMBED_TIMEOUT    = 30     # seconds to wait for an Ollama /api/embed call
_BGE_AVAIL_TIMEOUT    = 5      # seconds to wait for an Ollama /api/tags probe
_LLM_PREVIEW_CHARS    = 400    # per-chunk preview chars sent to the LLM reranker
_LLM_MAX_TOKENS       = 512    # max tokens for the LLM-as-reranker call
_LLM_TEMPERATURE      = 0.1    # temperature for (near-)deterministic ranking

# Anomaly-detection relevance floor — a rerank whose best document scores below
# this is flagged as a low-confidence ranking (the reranker found nothing
# strongly relevant).  When enough history exists the floor is recomputed
# adaptively as (mean − k·stddev) of past reranked top scores; otherwise this
# module-level floor is used.
_RERANK_SCORE_FLOOR   = 0.30   # top relevance score below this is flagged
_ANOMALY_STDDEV_K     = 2.0    # flag runs below mean − k·stddev of history


def _load_rerank_anomaly_config(config: "dict | None" = None) -> dict:
    """Load reranker.anomaly_detection settings from rag_config.yaml.

    A caller-supplied ``config`` may carry the block directly under an
    ``anomaly_detection`` key (e.g. from the rerank config section); otherwise
    fall back to the top-level ``reranker.anomaly_detection`` block in
    args/rag_config.yaml.  Returns an empty dict when nothing is configured.
    """
    cfg = config or {}
    if "anomaly_detection" in cfg:
        return cfg["anomaly_detection"] or {}
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return raw.get("reranker", {}).get("anomaly_detection", {})
    except Exception as exc:
        logger.debug("Failed to load rag_config.yaml: %s", exc)
        return {}


def _compute_rerank_anomaly_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute an adaptive relevance floor from historical reranked retrievals.

    Reads ``top_score`` from ``rag_retrieval_log`` for runs that used reranking
    and derives a statistical lower bound (mean − k·stddev), so the definition
    of a "low-confidence" rerank tracks the corpus instead of a frozen magic
    number.

    Falls back to the module-level floor when fewer than ``min_samples`` rows
    exist or anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    defaults = {
        "score_floor": cfg.get("fallback_score_floor", _RERANK_SCORE_FLOOR),
        "computed": False,
    }
    if not cfg.get("enabled", True):
        return defaults

    min_samples = cfg.get("min_samples", 30)
    stddev_k    = float(cfg.get("stddev_k", _ANOMALY_STDDEV_K))
    bounds      = cfg.get("adaptive_bounds", {})
    floor_min   = float(bounds.get("floor_min", 0.05))
    floor_max   = float(bounds.get("floor_max", 0.80))

    conn = None
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        # Population mean / stddev via E[x²] − E[x]² (SQLite has no STDDEV()).
        row = conn.execute(
            "SELECT "
            "AVG(top_score) AS mean, AVG(top_score * top_score) AS sq, "
            "COUNT(*) AS n "
            "FROM rag_retrieval_log "
            "WHERE rerank_used = 1 AND top_score IS NOT NULL"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[2]
            if n and n >= min_samples:
                mean = float((row["mean"] if isinstance(row, dict) else row[0]) or 0.0)
                sq   = float((row["sq"] if isinstance(row, dict) else row[1]) or 0.0)
                std  = math.sqrt(max(0.0, sq - mean * mean))
                score_floor = max(floor_min, min(floor_max, mean - stddev_k * std))
                return {
                    "score_floor": round(score_floor, 3),
                    "computed": True,
                    "n_samples": n,
                    "mean": round(mean, 3),
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return defaults


class RerankerProvider(ABC):
    """Abstract base class for re-ranking backends (D-RAG-20, D66 pattern)."""

    # Adaptive anomaly-detection state (lazily configured; see
    # configure_anomaly_detection / flag_anomaly).
    _anomaly_cfg: Optional[dict] = None
    _anomaly_thresholds: Optional[dict] = None

    def configure_anomaly_detection(self, config: Optional[dict] = None) -> None:
        """Load anomaly config and compute the adaptive relevance floor."""
        self._anomaly_cfg = _load_rerank_anomaly_config(config)
        self._anomaly_thresholds = _compute_rerank_anomaly_thresholds(self._anomaly_cfg)

    def flag_anomaly(self, ranked: List[Tuple[int, float]]) -> dict:
        """Flag a rerank result as a low-confidence (anomalous) ranking.

        ``ranked`` is the output of :meth:`rerank` — ``(index, score)`` tuples
        sorted by relevance descending.  The result is flagged when the best
        score falls below the (adaptive) relevance floor, i.e. the reranker
        surfaced nothing strongly relevant.

        Returns ``{"anomalous": bool, "reasons": [...], "score_floor": float}``;
        clean (and reason-free) for an empty result set.
        """
        if self._anomaly_thresholds is None:
            self.configure_anomaly_detection(None)
        floor = self._anomaly_thresholds["score_floor"]
        reasons: List[str] = []
        if ranked:
            top_score = ranked[0][1]
            if top_score < floor:
                reasons.append(f"top rerank score {top_score} < floor {floor}")
        return {"anomalous": bool(reasons), "reasons": reasons, "score_floor": floor}

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

    def __init__(
        self,
        model: str = "bge-reranker-v2-m3",
        base_url: str = "",
        config: Optional[dict] = None,
    ):
        import os

        cfg = config or {}
        self._model = model
        self._base_url = base_url or os.environ.get(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        )
        self._doc_chars = int(cfg.get("bge_doc_chars", _BGE_DOC_CHARS))
        self._embed_timeout = int(cfg.get("bge_embed_timeout", _BGE_EMBED_TIMEOUT))
        self._avail_timeout = int(cfg.get("bge_avail_timeout", _BGE_AVAIL_TIMEOUT))
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
        top_k: int = _DEFAULT_TOP_K,
    ) -> List[Tuple[int, float]]:
        """Score query-document pairs via Ollama and rank by relevance."""
        session = self._get_session()

        scores: List[Tuple[int, float]] = []
        for i, doc in enumerate(documents):
            # SEC: Sanitize inputs — strip newlines from query to prevent format injection
            safe_query = query.replace("\n", " ").replace("\r", " ").strip()
            safe_doc = doc[: self._doc_chars].replace("\r", " ").strip()
            pair_text = f"query: {safe_query}\ndocument: {safe_doc}"
            try:
                resp = session.post(
                    f"{self._base_url}/api/embed",
                    json={"model": self._model, "input": pair_text},
                    timeout=self._embed_timeout,
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

            resp = request("GET", f"{self._base_url}/api/tags", timeout=self._avail_timeout)
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
        self._max_preview_chars = self._config.get(
            "max_chunk_preview_chars", _LLM_PREVIEW_CHARS
        )
        self._max_tokens = int(self._config.get("llm_max_tokens", _LLM_MAX_TOKENS))
        self._temperature = float(self._config.get("llm_temperature", _LLM_TEMPERATURE))

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
        top_k: int = _DEFAULT_TOP_K,
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
                max_tokens=self._max_tokens,
                temperature=self._temperature,
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
        provider = BGERerankerProvider(model=bge_model, config=cfg)
        if provider.check_availability():
            provider.configure_anomaly_detection(cfg)
            return provider
        if method == "bge":
            logger.info("BGE model '%s' not available, falling back to LLM reranker", bge_model)

    provider = LLMRerankerProvider(config=cfg)
    provider.configure_anomaly_detection(cfg)
    return provider
