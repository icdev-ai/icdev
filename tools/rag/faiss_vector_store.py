# CUI // SP-CTI
"""FAISS vector store backend (D-RAG-1, optional).

Requires: pip install faiss-cpu
Falls back to SQLite if not available.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.rag.vector_store_provider import (
    SearchResult,
    VectorChunk,
    VectorStoreProvider,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Module-level fallback constants — overridable from args/rag_config.yaml
# under rag.vector_store.faiss.  Change config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_EMBEDDING_DIM  = 768   # default embedding dimension (nomic-embed-text)
_OVER_FETCH_MULTIPLIER  = 3     # over-fetch k*N candidates before post-filtering
_SEARCH_DEFAULT_TOP_K   = 50    # default top_k for vector similarity search


class FAISSVectorStore(VectorStoreProvider):
    """FAISS vector store implementation.

    Uses FAISS for fast approximate nearest neighbor search.
    Metadata stored in a companion JSON file.
    Multi-tenant isolation via separate index directories (D-RAG-7).
    """

    def __init__(self, index_dir: str = "", tenant_id: str = "", dim: int = _DEFAULT_EMBEDDING_DIM):
        import faiss

        self._tenant_id = tenant_id
        base_dir = index_dir or str(BASE_DIR / "data" / "rag" / "faiss")
        if tenant_id:
            base_dir = os.path.join(base_dir, tenant_id)
        Path(base_dir).mkdir(parents=True, exist_ok=True)
        self._index_dir = base_dir
        self._index_path = os.path.join(base_dir, "rag_index.faiss")
        self._meta_path = os.path.join(base_dir, "rag_meta.json")
        self._dim = dim

        # Load or create index
        if os.path.exists(self._index_path):
            self._index = faiss.read_index(self._index_path)
        else:
            self._index = faiss.IndexFlatIP(dim)  # Inner product (cosine after normalization)

        # Load metadata
        if os.path.exists(self._meta_path):
            with open(self._meta_path, "r") as f:
                self._metadata: Dict[str, dict] = json.load(f)
        else:
            self._metadata = {}

        # id_to_idx mapping
        self._id_list: List[str] = list(self._metadata.keys())

    def _save(self):
        """Persist index and metadata."""
        import faiss

        faiss.write_index(self._index, self._index_path)
        with open(self._meta_path, "w") as f:
            json.dump(self._metadata, f)

    def _normalize(self, vec: List[float]) -> "np.ndarray":  # noqa: F821
        """L2 normalize for cosine similarity via inner product."""
        import numpy as np

        v = np.array(vec, dtype=np.float32).reshape(1, -1)
        norm = np.linalg.norm(v)
        if norm > 0:
            v = v / norm
        return v

    @property
    def provider_name(self) -> str:
        return "faiss"

    def upsert(self, chunks: List[VectorChunk]) -> int:

        if not chunks:
            return 0
        inserted = 0
        for chunk in chunks:
            if not chunk.content_hash:
                chunk.compute_content_hash()
            if chunk.embedding is None:
                continue
            # Dedup check
            if chunk.content_hash in {m.get("content_hash", "") for m in self._metadata.values()}:
                continue

            vec = self._normalize(chunk.embedding)
            self._index.add(vec)
            len(self._id_list)
            self._id_list.append(chunk.chunk_id)
            self._metadata[chunk.chunk_id] = {
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "source_type": chunk.source_type,
                "source_id": chunk.source_id,
                "source_table": chunk.source_table,
                "chunk_index": chunk.chunk_index,
                "total_chunks": chunk.total_chunks,
                "metadata": chunk.metadata or {},
                "tier": chunk.tier,
                "tenant_id": chunk.tenant_id or self._tenant_id,
                "project_id": chunk.project_id,
                "classification": chunk.classification,
            }
            inserted += 1

        if inserted > 0:
            self._save()
        return inserted

    def search(
        self,
        query_embedding: List[float],
        top_k: int = _SEARCH_DEFAULT_TOP_K,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:

        if self._index.ntotal == 0:
            return []

        vec = self._normalize(query_embedding)
        k = min(top_k * _OVER_FETCH_MULTIPLIER, self._index.ntotal)
        distances, indices = self._index.search(vec, k)

        results = []
        for i in range(len(indices[0])):
            idx = int(indices[0][i])
            if idx < 0 or idx >= len(self._id_list):
                continue
            cid = self._id_list[idx]
            meta = self._metadata.get(cid, {})

            # Apply filters
            if filters:
                if "source_type" in filters and meta.get("source_type") != filters["source_type"]:
                    continue
                if "tier" in filters and meta.get("tier") != filters["tier"]:
                    continue
                if "tenant_id" in filters:
                    if meta.get("tenant_id", "") not in (filters["tenant_id"], ""):
                        continue
            elif self._tenant_id:
                if meta.get("tenant_id", "") not in (self._tenant_id, ""):
                    continue

            score = float(distances[0][i])  # Already cosine similarity (inner product on normalized)

            results.append(
                SearchResult(
                    chunk_id=cid,
                    content=meta.get("content", ""),
                    source_type=meta.get("source_type", ""),
                    source_id=meta.get("source_id", ""),
                    source_table=meta.get("source_table", ""),
                    chunk_index=meta.get("chunk_index", 0),
                    score=score,
                    final_score=score,
                    metadata=meta.get("metadata", {}),
                    tier=meta.get("tier", "hot"),
                    classification=meta.get("classification", "CUI"),
                )
            )

            if len(results) >= top_k:
                break

        results.sort(key=lambda r: r.score, reverse=True)
        return results

    def delete(self, chunk_ids: List[str]) -> int:
        # FAISS doesn't support direct deletion — rebuild index without deleted IDs
        import numpy as np
        import faiss

        deleted = 0
        remaining_ids = []
        remaining_vecs = []

        for i, cid in enumerate(self._id_list):
            if cid in chunk_ids:
                if cid in self._metadata:
                    del self._metadata[cid]
                deleted += 1
            else:
                remaining_ids.append(cid)
                # Reconstruct vector from index
                try:
                    vec = self._index.reconstruct(i)
                    remaining_vecs.append(vec)
                except Exception:
                    continue

        self._id_list = remaining_ids
        self._index = faiss.IndexFlatIP(self._dim)
        if remaining_vecs:
            vecs = np.array(remaining_vecs, dtype=np.float32)
            self._index.add(vecs)

        self._save()
        return deleted

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        if not filters:
            return self._index.ntotal
        count = 0
        for meta in self._metadata.values():
            match = True
            if "source_type" in filters and meta.get("source_type") != filters["source_type"]:
                match = False
            if "tier" in filters and meta.get("tier") != filters["tier"]:
                match = False
            if "tenant_id" in filters:
                if meta.get("tenant_id", "") not in (filters["tenant_id"], ""):
                    match = False
            if match:
                count += 1
        return count

    def check_availability(self) -> bool:
        try:
            _ = self._index.ntotal
            return True
        except Exception:
            return False

    def get_by_content_hash(self, content_hash: str) -> Optional[VectorChunk]:
        for cid, meta in self._metadata.items():
            if meta.get("content_hash") == content_hash:
                return VectorChunk(
                    chunk_id=cid,
                    content=meta.get("content", ""),
                    content_hash=content_hash,
                    source_type=meta.get("source_type", ""),
                    source_id=meta.get("source_id", ""),
                    source_table=meta.get("source_table", ""),
                    chunk_index=meta.get("chunk_index", 0),
                    total_chunks=meta.get("total_chunks", 1),
                    tier=meta.get("tier", "hot"),
                    tenant_id=meta.get("tenant_id", ""),
                    project_id=meta.get("project_id", ""),
                    classification=meta.get("classification", "CUI"),
                )
        return None

    def migrate_tier(self, chunk_ids: List[str], target_tier: str) -> int:
        if not chunk_ids or target_tier not in ("hot", "warm", "cold"):
            return 0
        migrated = 0
        for cid in chunk_ids:
            if cid in self._metadata:
                self._metadata[cid]["tier"] = target_tier
                migrated += 1
        if migrated > 0:
            self._save()
        return migrated
