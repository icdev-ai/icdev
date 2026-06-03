# CUI // SP-CTI
"""Agentic Research Pipeline — Model Layer.

Three composable components that back the model nodes in the
'Agentic Research Pipeline' AADC template:

  embedding-model  →  Embedder
  reranker         →  ReRanker
  llm              →  SynthesisLLM

The public entry-point is AgenticResearchPipeline.run(query, chunks).
Each component can also be used independently.

Usage:
    from tools.agentic_ai_canvas.model_layer import AgenticResearchPipeline
    pipeline = AgenticResearchPipeline()
    result = pipeline.run("What is FedRAMP AC-2?", chunks=["..."])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("icdev.aadc.model_layer")


# ---------------------------------------------------------------------------
# Shared result types
# ---------------------------------------------------------------------------

@dataclass
class EmbedResult:
    query: str
    vector: List[float]
    model: str = ""


@dataclass
class RankedChunk:
    content: str
    score: float
    source: str = ""
    rerank_score: float = 0.0


@dataclass
class SynthesisResult:
    query: str
    answer: str
    chunks_used: int
    model: str = ""
    confidence: float = 0.0
    error: str = ""


@dataclass
class PipelineResult:
    query: str
    answer: str
    chunks_used: int
    model: str = ""
    confidence: float = 0.0
    embed_model: str = ""
    top_chunks: List[RankedChunk] = field(default_factory=list)
    error: str = ""
    # Governance layer fields (populated by the audit-logger node)
    confidence_gate_passed: bool = False
    output_valid: bool = False
    audit_entry_id: int = -1


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------

class Embedder:
    """Embeds text using the configured embedding provider (D-RAG-3).

    Backed by tools.llm.get_embedding_provider() which resolves to
    Ollama nomic-embed-text (air-gap safe) with a Claude fallback.
    """

    def __init__(self) -> None:
        self._provider = None

    def _get_provider(self):
        if self._provider is None:
            try:
                from tools.llm import get_embedding_provider
                self._provider = get_embedding_provider()
            except Exception as exc:
                logger.warning("Embedder: embedding provider unavailable — %s", exc)
        return self._provider

    def embed(self, text: str) -> EmbedResult:
        """Embed a single text string.

        Returns an EmbedResult with an empty vector on failure so callers
        can degrade gracefully without try/except.
        """
        provider = self._get_provider()
        if provider is None:
            logger.warning("Embedder: no provider, returning empty vector")
            return EmbedResult(query=text, vector=[], model="none")

        try:
            vector = provider.embed(text)
            model_id = getattr(provider, "model_id", "") or ""
            return EmbedResult(query=text, vector=list(vector), model=model_id)
        except Exception as exc:
            logger.warning("Embedder.embed failed: %s", exc)
            return EmbedResult(query=text, vector=[], model="error")


# ---------------------------------------------------------------------------
# ReRanker
# ---------------------------------------------------------------------------

class ReRanker:
    """Re-ranks candidate chunks using the BGE cross-encoder (D-RAG-20).

    Falls back to qwen3 LLM-based re-ranking when BGE is unavailable.
    Wraps tools.rag.reranker.rerank_results() with RankedChunk output.
    """

    def __init__(self, top_k: int = 5, rerank_weight: float = 0.6) -> None:
        self.top_k = top_k
        self.rerank_weight = rerank_weight

    def rerank(self, query: str, chunks: List[str]) -> List[RankedChunk]:
        """Re-rank a list of text chunks against the query.

        Args:
            query: Research query string.
            chunks: Candidate text chunks (plain strings).

        Returns:
            Re-ranked list of RankedChunk (best first), at most self.top_k.
        """
        if not chunks:
            return []

        try:
            from tools.rag.vector_store_provider import SearchResult
            from tools.rag.reranker import rerank_results

            results = [
                SearchResult(content=c, score=1.0, source_id="pipeline")
                for c in chunks
            ]
            config = {"rerank_weight": self.rerank_weight}
            reranked = rerank_results(query, results, top_k=self.top_k, config=config)

            return [
                RankedChunk(
                    content=r.content,
                    score=getattr(r, "final_score", r.score),
                    source=getattr(r, "source", ""),
                    rerank_score=getattr(r, "rerank_score", 0.0),
                )
                for r in reranked
            ]
        except Exception as exc:
            logger.warning("ReRanker.rerank failed: %s — returning top-%d unranked", exc, self.top_k)
            return [
                RankedChunk(content=c, score=1.0 / (i + 1))
                for i, c in enumerate(chunks[: self.top_k])
            ]


# ---------------------------------------------------------------------------
# SynthesisLLM
# ---------------------------------------------------------------------------

class SynthesisLLM:
    """Generates a grounded answer from re-ranked chunks (rag_synthesis function).

    Uses the LLM router with function='rag_synthesis' so the config in
    args/llm_config.yaml controls model selection (tier1 → tier2 → local).
    """

    _SYSTEM = (
        "You are a precise research assistant. Answer the question using only "
        "the provided context chunks. Cite evidence. If the context is "
        "insufficient, say so explicitly."
    )

    def __init__(self, max_context_chars: int = 8000) -> None:
        self.max_context_chars = max_context_chars

    def synthesize(self, query: str, chunks: List[RankedChunk]) -> SynthesisResult:
        """Synthesize a grounded answer from ranked chunks.

        Args:
            query: Research question.
            chunks: Re-ranked chunks (best first).

        Returns:
            SynthesisResult with answer and metadata.
        """
        if not chunks:
            return SynthesisResult(
                query=query, answer="No context available.", chunks_used=0
            )

        context = self._build_context(chunks)
        user_msg = f"Question: {query}\n\nContext:\n{context}"

        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            router = LLMRouter()
            req = LLMRequest(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=self._SYSTEM,
                max_tokens=2048,
                temperature=0.2,
                skip_injection_scan=True,
                classification="CUI",
            )
            resp = router.invoke("rag_synthesis", req)
            model_id = getattr(resp, "model", "") or ""
            return SynthesisResult(
                query=query,
                answer=resp.content.strip(),
                chunks_used=len(chunks),
                model=model_id,
                confidence=getattr(resp, "confidence", 0.0) or 0.0,
            )
        except Exception as exc:
            logger.warning("SynthesisLLM.synthesize failed: %s", exc)
            return SynthesisResult(
                query=query,
                answer="",
                chunks_used=len(chunks),
                error=str(exc),
            )

    def _build_context(self, chunks: List[RankedChunk]) -> str:
        parts: List[str] = []
        total = 0
        for i, c in enumerate(chunks, 1):
            fragment = f"[{i}] {c.content}"
            if total + len(fragment) > self.max_context_chars:
                break
            parts.append(fragment)
            total += len(fragment)
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# AgenticResearchPipeline — composes all three model nodes
# ---------------------------------------------------------------------------

class AgenticResearchPipeline:
    """Agentic Research Pipeline model layer.

    Implements all pipeline nodes from AADC Template #5:
      Embedder → ReRanker → SynthesisLLM → GovernanceLayer

    The governance layer (ConfidenceGate → OutputValidator →
    PipelineAuditLogger) runs after synthesis to validate output quality
    and write an immutable NIST AU-2 audit entry.

    Args:
        top_k: Maximum chunks to pass to SynthesisLLM.
        rerank_weight: Blend weight for cross-encoder vs vector score.
        max_context_chars: Context window budget for synthesis.
        design_id: AADC design ID forwarded to the audit-logger node.
        actor: Audit actor label (default "pipeline").
        session_id: Correlation ID forwarded to the audit trail.
        confidence_threshold: Minimum confidence to pass the governance gate.
    """

    def __init__(
        self,
        top_k: int = 5,
        rerank_weight: float = 0.6,
        max_context_chars: int = 8000,
        design_id: str = "",
        actor: str = "pipeline",
        session_id: Optional[str] = None,
        confidence_threshold: float = 0.5,
    ) -> None:
        self.embedder = Embedder()
        self.reranker = ReRanker(top_k=top_k, rerank_weight=rerank_weight)
        self.synthesis_llm = SynthesisLLM(max_context_chars=max_context_chars)

        from tools.agentic_ai_canvas.governance_layer import GovernanceLayer
        self.governance = GovernanceLayer(
            confidence_threshold=confidence_threshold,
            design_id=design_id,
            actor=actor,
            session_id=session_id,
        )

    def run(
        self,
        query: str,
        chunks: Optional[List[str]] = None,
    ) -> PipelineResult:
        """Run the full pipeline including the governance layer.

        Args:
            query: Research question.
            chunks: Candidate text chunks from upstream retrieval
                    (web search + chunker nodes).  If empty, the
                    pipeline degrades gracefully.

        Returns:
            PipelineResult with grounded answer, pipeline metadata, and
            governance layer outcomes (confidence_gate_passed, output_valid,
            audit_entry_id).
        """
        chunks = chunks or []

        self.governance.log_started(query)

        try:
            # Stage 1: Embed the query
            embed_result = self.embedder.embed(query)

            # Stage 2: Re-rank candidate chunks
            ranked = self.reranker.rerank(query, chunks)

            # Stage 3: Synthesize grounded answer
            synth = self.synthesis_llm.synthesize(query, ranked)

            # Stage 4: Governance layer (confidence gate → output validator → audit logger)
            gov = self.governance.evaluate(
                query=query,
                answer=synth.answer,
                confidence=synth.confidence,
                chunks_used=synth.chunks_used,
                model=synth.model,
                error=synth.error,
            )

            return PipelineResult(
                query=query,
                answer=synth.answer,
                chunks_used=synth.chunks_used,
                model=synth.model,
                confidence=synth.confidence,
                embed_model=embed_result.model,
                top_chunks=ranked,
                error=synth.error,
                confidence_gate_passed=gov.confidence_gate.passed,
                output_valid=gov.output_validation.valid,
                audit_entry_id=gov.audit_entry_id,
            )
        except Exception as exc:
            self.governance.log_failed(query=query, error=str(exc))
            raise
