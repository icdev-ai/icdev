# CUI // SP-CTI
"""ChromaDB vector store backend (D-RAG-1, optional).

Requires: pip install chromadb
Falls back to SQLite if not available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.rag.vector_store_provider import (
    SearchResult,
    VectorChunk,
    VectorStoreProvider,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class ChromaVectorStore(VectorStoreProvider):
    """ChromaDB vector store implementation.

    Uses persistent ChromaDB collections with tenant-namespaced
    collection names for multi-tenant isolation (D-RAG-7).
    """

    def __init__(self, persist_dir: str = "", tenant_id: str = ""):
        import chromadb

        self._tenant_id = tenant_id
        persist_path = persist_dir or str(BASE_DIR / "data" / "rag" / "chromadb")
        Path(persist_path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_path)
        collection_name = f"{tenant_id}__rag_chunks" if tenant_id else "rag_chunks"
        # ChromaDB collection names max 63 chars
        collection_name = collection_name[:63]
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def provider_name(self) -> str:
        return "chromadb"

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
            existing = self._collection.get(
                where={"content_hash": chunk.content_hash},
                limit=1,
            )
            if existing and existing["ids"]:
                continue

            meta = {
                "content_hash": chunk.content_hash,
                "source_type": chunk.source_type,
                "source_id": chunk.source_id,
                "source_table": chunk.source_table,
                "chunk_index": chunk.chunk_index,
                "total_chunks": chunk.total_chunks,
                "tier": chunk.tier,
                "tenant_id": chunk.tenant_id or self._tenant_id,
                "project_id": chunk.project_id,
                "classification": chunk.classification,
            }
            # Add user metadata (flatten)
            if chunk.metadata:
                for k, v in chunk.metadata.items():
                    if isinstance(v, (str, int, float, bool)):
                        meta[f"meta_{k}"] = v

            self._collection.add(
                ids=[chunk.chunk_id],
                embeddings=[chunk.embedding],
                documents=[chunk.content],
                metadatas=[meta],
            )
            inserted += 1
        return inserted

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 50,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        where = {}
        if filters:
            if "source_type" in filters:
                where["source_type"] = filters["source_type"]
            if "tier" in filters:
                where["tier"] = filters["tier"]
            if "tenant_id" in filters:
                where["tenant_id"] = filters["tenant_id"]
        elif self._tenant_id:
            where["tenant_id"] = self._tenant_id

        query_params = {
            "query_embeddings": [query_embedding],
            "n_results": top_k,
        }
        if where:
            query_params["where"] = where

        results = self._collection.query(**query_params)
        search_results = []
        if results and results["ids"] and results["ids"][0]:
            for i, cid in enumerate(results["ids"][0]):
                doc = results["documents"][0][i] if results["documents"] else ""
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 1.0
                # ChromaDB cosine distance = 1 - similarity
                score = 1.0 - dist

                search_results.append(
                    SearchResult(
                        chunk_id=cid,
                        content=doc,
                        source_type=meta.get("source_type", ""),
                        source_id=meta.get("source_id", ""),
                        source_table=meta.get("source_table", ""),
                        chunk_index=meta.get("chunk_index", 0),
                        score=score,
                        final_score=score,
                        metadata={k: v for k, v in meta.items() if k.startswith("meta_")},
                        tier=meta.get("tier", "hot"),
                        classification=meta.get("classification", "CUI"),
                    )
                )

        search_results.sort(key=lambda r: r.score, reverse=True)
        return search_results

    def delete(self, chunk_ids: List[str]) -> int:
        if not chunk_ids:
            return 0
        self._collection.delete(ids=chunk_ids)
        return len(chunk_ids)

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        if filters:
            where = {}
            if "source_type" in filters:
                where["source_type"] = filters["source_type"]
            if "tier" in filters:
                where["tier"] = filters["tier"]
            if where:
                try:
                    result = self._collection.get(where=where, limit=1, include=[])
                    # ChromaDB get with where returns all matching
                    result = self._collection.get(where=where, include=[])
                    return len(result["ids"]) if result and result["ids"] else 0
                except Exception:
                    pass
        return self._collection.count()

    def check_availability(self) -> bool:
        try:
            self._collection.count()
            return True
        except Exception:
            return False

    def get_by_content_hash(self, content_hash: str) -> Optional[VectorChunk]:
        result = self._collection.get(
            where={"content_hash": content_hash},
            limit=1,
            include=["documents", "metadatas", "embeddings"],
        )
        if not result or not result["ids"]:
            return None
        meta = result["metadatas"][0] if result["metadatas"] else {}
        emb = result["embeddings"][0] if result["embeddings"] else None
        return VectorChunk(
            chunk_id=result["ids"][0],
            content=result["documents"][0] if result["documents"] else "",
            content_hash=content_hash,
            embedding=emb,
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

    def migrate_tier(self, chunk_ids: List[str], target_tier: str) -> int:
        if not chunk_ids or target_tier not in ("hot", "warm", "cold"):
            return 0
        migrated = 0
        for cid in chunk_ids:
            try:
                self._collection.update(
                    ids=[cid],
                    metadatas=[{"tier": target_tier}],
                )
                migrated += 1
            except Exception:
                continue
        return migrated
