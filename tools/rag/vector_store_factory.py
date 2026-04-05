# [TEMPLATE: CUI // SP-CTI]
"""Vector store factory — config-driven backend selection (D-RAG-1).

Auto-detect order: ChromaDB → FAISS → SQLite (always available).
Config: args/rag_config.yaml → rag.vector_store.backend
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from tools.rag.vector_store_provider import VectorStoreProvider

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_rag_config() -> dict:
    """Load RAG config from args/rag_config.yaml."""
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


class VectorStoreFactory:
    """Factory for creating vector store instances."""

    @staticmethod
    def create(
        backend: str = "",
        tenant_id: Optional[str] = None,
        config: Optional[dict] = None,
    ) -> VectorStoreProvider:
        """Create a vector store instance based on config.

        Args:
            backend: Override backend selection ('sqlite', 'chromadb', 'faiss', 'auto').
            tenant_id: Optional tenant ID for multi-tenant isolation (D-RAG-7).
            config: Optional config dict (loads from args/rag_config.yaml if None).

        Returns:
            VectorStoreProvider instance.
        """
        if config is None:
            config = _load_rag_config()
        rag_cfg = config.get("rag", {})
        vs_cfg = rag_cfg.get("vector_store", {})
        selected = backend or vs_cfg.get("backend", "auto")

        if selected == "auto":
            return VectorStoreFactory._auto_detect(vs_cfg, tenant_id)
        elif selected == "pgvector":
            return VectorStoreFactory._create_pgvector(vs_cfg, tenant_id)
        elif selected == "chromadb":
            return VectorStoreFactory._create_chromadb(vs_cfg, tenant_id)
        elif selected == "faiss":
            return VectorStoreFactory._create_faiss(vs_cfg, tenant_id)
        else:
            return VectorStoreFactory._create_sqlite(vs_cfg, tenant_id)

    @staticmethod
    def _auto_detect(vs_cfg: dict, tenant_id: Optional[str]) -> VectorStoreProvider:
        """Auto-detect best available backend: pgvector → ChromaDB → FAISS → SQLite."""
        # Try pgvector (highest priority when PG is the storage backend)
        try:
            from tools.db.storage import get_backend

            if get_backend() == "postgresql":
                return VectorStoreFactory._create_pgvector(vs_cfg, tenant_id)
        except ImportError:
            pass

        # Try ChromaDB
        try:
            import chromadb  # noqa: F401

            return VectorStoreFactory._create_chromadb(vs_cfg, tenant_id)
        except ImportError:
            pass

        # Try FAISS
        try:
            import faiss  # noqa: F401

            return VectorStoreFactory._create_faiss(vs_cfg, tenant_id)
        except ImportError:
            pass

        # SQLite always available
        return VectorStoreFactory._create_sqlite(vs_cfg, tenant_id)

    @staticmethod
    def _create_pgvector(vs_cfg: dict, tenant_id: Optional[str]) -> VectorStoreProvider:
        """Create pgvector store (PostgreSQL with vector extension)."""
        from tools.rag.pg_vector_store import PgVectorStore

        return PgVectorStore()

    @staticmethod
    def _create_sqlite(vs_cfg: dict, tenant_id: Optional[str]) -> VectorStoreProvider:
        from tools.rag.sqlite_vector_store import SQLiteVectorStore

        sqlite_cfg = vs_cfg.get("sqlite", {})
        db_path = sqlite_cfg.get("db_path", "")
        if db_path:
            db_path = BASE_DIR / db_path
        return SQLiteVectorStore(db_path=db_path or None, tenant_id=tenant_id or "")

    @staticmethod
    def _create_chromadb(vs_cfg: dict, tenant_id: Optional[str]) -> VectorStoreProvider:
        try:
            from tools.rag.chroma_vector_store import ChromaVectorStore

            chroma_cfg = vs_cfg.get("chromadb", {})
            persist_dir = chroma_cfg.get("persist_dir", "data/rag/chromadb/")
            return ChromaVectorStore(
                persist_dir=str(BASE_DIR / persist_dir),
                tenant_id=tenant_id or "",
            )
        except ImportError:
            # Graceful fallback to SQLite
            return VectorStoreFactory._create_sqlite(vs_cfg, tenant_id)

    @staticmethod
    def _create_faiss(vs_cfg: dict, tenant_id: Optional[str]) -> VectorStoreProvider:
        try:
            from tools.rag.faiss_vector_store import FAISSVectorStore

            faiss_cfg = vs_cfg.get("faiss", {})
            index_dir = faiss_cfg.get("index_dir", "data/rag/faiss/")
            return FAISSVectorStore(
                index_dir=str(BASE_DIR / index_dir),
                tenant_id=tenant_id or "",
            )
        except ImportError:
            # Graceful fallback to SQLite
            return VectorStoreFactory._create_sqlite(vs_cfg, tenant_id)

    @staticmethod
    def list_available() -> list[str]:
        """List available vector store backends."""
        available = ["sqlite"]  # Always available
        try:
            import chromadb  # noqa: F401

            available.append("chromadb")
        except ImportError:
            pass
        try:
            import faiss  # noqa: F401

            available.append("faiss")
        except ImportError:
            pass
        return available
