# [TEMPLATE: CUI // SP-CTI]
"""Universal RAG Subsystem — Phase 64.

Provides vector-indexed retrieval-augmented generation across all ICDEV™ data.
Indexes Innovation, Creative, Research engine outputs plus compliance artifacts,
memory entries, and audit data for context-aware LLM invocations.

Architecture:
    VectorStoreProvider ABC (D-RAG-1) with SQLite/ChromaDB/FAISS backends.
    Adaptive chunking, two-stage retrieval (vector + qwen3 re-rank),
    injection into two-tier LLM pipeline, full PROV-AGENT provenance.

Usage:
    from tools.rag import get_retriever, get_vector_store
    retriever = get_retriever()
    results = retriever.search("FedRAMP AC-2 implementation patterns", top_k=5)
"""

from __future__ import annotations


def get_vector_store(tenant_id: str | None = None):
    """Get configured vector store instance.

    Args:
        tenant_id: Optional tenant ID for multi-tenant isolation.

    Returns:
        VectorStoreProvider instance.
    """
    from tools.rag.vector_store_factory import VectorStoreFactory

    return VectorStoreFactory.create(tenant_id=tenant_id)


def get_retriever(tenant_id: str | None = None):
    """Get configured RAG retriever instance.

    Args:
        tenant_id: Optional tenant ID for multi-tenant isolation.

    Returns:
        RAGRetriever instance.
    """
    from tools.rag.retriever import RAGRetriever

    return RAGRetriever(tenant_id=tenant_id)
