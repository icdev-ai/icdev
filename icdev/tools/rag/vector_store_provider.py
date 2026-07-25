# [TEMPLATE: CUI // SP-CTI]
"""Vector store provider ABC and data types (D-RAG-1).

Follows the Provider ABC pattern from tools/llm/provider.py (D66).
All vector store implementations must satisfy this interface.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class VectorChunk:
    """A chunk of content ready for vector storage."""

    chunk_id: str = ""
    content: str = ""
    content_hash: str = ""
    embedding: Optional[List[float]] = None
    source_type: str = ""  # e.g. "innovation_signals", "creative_pain_points"
    source_id: str = ""  # row ID in source table
    source_table: str = ""  # actual DB table name
    chunk_index: int = 0  # position within multi-chunk source
    total_chunks: int = 1  # total chunks for this source item
    metadata: Dict[str, Any] = field(default_factory=dict)
    tier: str = "hot"  # hot, warm, cold
    tenant_id: str = ""
    project_id: str = ""
    classification: str = "CUI"
    # Contextual retrieval (rce-ctx-01): optional LLM/heuristic context prefix
    # that situates this chunk within its source document. The EMBEDDING is
    # computed on ``embed_text`` (prefix + content) while ``content`` — the
    # stored/cited text — stays original, preserving citation integrity.
    # Both default to their "unset" values so existing behavior is unchanged.
    context_prefix: str = ""
    embed_text: Optional[str] = None

    def compute_content_hash(self) -> str:
        """Compute SHA-256 hash of content for dedup (D-RAG-5).

        Hash is always computed over the ORIGINAL ``content`` (never the
        contextualized ``embed_text``) so dedup stays stable whether or not
        contextual prefixing is enabled.
        """
        self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        return self.content_hash

    def text_for_embedding(self) -> str:
        """Return the text that should be embedded.

        Returns ``embed_text`` when set (contextual retrieval enabled),
        otherwise the original ``content`` — identical to prior behavior.
        """
        return self.embed_text if self.embed_text else self.content


@dataclass
class SearchResult:
    """A single search result from vector retrieval."""

    chunk_id: str = ""
    content: str = ""
    source_type: str = ""
    source_id: str = ""
    source_table: str = ""
    chunk_index: int = 0
    score: float = 0.0  # cosine similarity score
    bm25_score: float = 0.0  # BM25 keyword score (filled by retriever)
    time_decay_score: float = 0.0
    rerank_score: float = 0.0  # qwen3 re-rank score
    final_score: float = 0.0  # composite score after all stages
    metadata: Dict[str, Any] = field(default_factory=dict)
    tier: str = "hot"
    classification: str = "CUI"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_table": self.source_table,
            "chunk_index": self.chunk_index,
            "score": round(self.score, 4),
            "bm25_score": round(self.bm25_score, 4),
            "time_decay_score": round(self.time_decay_score, 4),
            "rerank_score": round(self.rerank_score, 4),
            "final_score": round(self.final_score, 4),
            "metadata": self.metadata,
            "tier": self.tier,
            "classification": self.classification,
        }


class VectorStoreProvider(ABC):
    """Abstract base class for vector store backends (D-RAG-1).

    Follows tools/llm/provider.py EmbeddingProvider pattern.
    Implementations: SQLite BLOB (default), ChromaDB (optional), FAISS (optional).
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider identifier (e.g. 'sqlite', 'chromadb', 'faiss')."""

    @abstractmethod
    def upsert(self, chunks: List[VectorChunk]) -> int:
        """Insert or update chunks with embeddings.

        Deduplication by content_hash (D-RAG-5). If a chunk with the same
        content_hash exists, it is skipped (not re-embedded).

        Args:
            chunks: List of VectorChunk instances with embeddings.

        Returns:
            Number of chunks actually inserted (excluding dedup skips).
        """

    @abstractmethod
    def search(
        self,
        query_embedding: List[float],
        top_k: int = 50,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Search for similar chunks by vector similarity.

        Args:
            query_embedding: Query vector (768 dims for nomic-embed-text).
            top_k: Maximum results to return.
            filters: Optional filters (source_type, tier, project_id, etc.).

        Returns:
            List of SearchResult sorted by descending similarity score.
        """

    @abstractmethod
    def delete(self, chunk_ids: List[str]) -> int:
        """Delete chunks by ID.

        Args:
            chunk_ids: List of chunk IDs to delete.

        Returns:
            Number of chunks actually deleted.
        """

    @abstractmethod
    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count chunks matching optional filters.

        Args:
            filters: Optional filters (source_type, tier, etc.).

        Returns:
            Number of matching chunks.
        """

    @abstractmethod
    def check_availability(self) -> bool:
        """Check if the vector store backend is available and ready."""

    def get_by_content_hash(self, content_hash: str) -> Optional[VectorChunk]:
        """Look up a chunk by content hash (for dedup).

        Default returns None. Backends should override for efficiency.
        """
        return None

    def migrate_tier(self, chunk_ids: List[str], target_tier: str) -> int:
        """Migrate chunks to a different retention tier (D-RAG-6).

        Default does nothing. Backends that support tiered storage override.

        Args:
            chunk_ids: Chunks to migrate.
            target_tier: Target tier ('hot', 'warm', 'cold').

        Returns:
            Number of chunks migrated.
        """
        return 0
