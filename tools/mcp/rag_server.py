# CUI // SP-CTI
"""RAG MCP server handlers — 9 tool handlers for RAG subsystem (Phase 64).

Each handler is called from the unified MCP gateway via tool_registry.py.
"""

from __future__ import annotations

from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"


def _get_db():
    """Get connection to ICDEV™ DB."""
    conn = get_connection()
    return conn


def handle_rag_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search RAG knowledge base with natural language query."""
    query = arguments.get("query", "")
    top_k = arguments.get("top_k", 5)
    source_type = arguments.get("source_type", "")
    tenant_id = arguments.get("tenant_id", "")
    child_id = arguments.get("child_id", "")

    if not query:
        return {"error": "query is required", "results": []}

    try:
        from tools.rag.retriever import RAGRetriever

        retriever = RAGRetriever(tenant_id=tenant_id)
        filters = {}
        if source_type:
            filters["source_type"] = source_type
        results = retriever.retrieve(
            query=query,
            top_k=top_k,
            filters=filters if filters else None,
            agent_id=f"child:{child_id}" if child_id else "",
        )
        return {
            "classification": "CUI // SP-CTI",
            "query": query,
            "results_count": len(results),
            "results": [r.to_dict() for r in results],
        }
    except ImportError:
        return {"error": "RAG subsystem not available", "results": []}
    except Exception as e:
        return {"error": str(e), "results": []}


def handle_rag_ingest(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a source type into the RAG vector store."""
    source_type = arguments.get("source_type", "")
    tenant_id = arguments.get("tenant_id", "")
    project_id = arguments.get("project_id", "")
    limit = arguments.get("limit", 0)

    if not source_type:
        return {"error": "source_type is required"}

    try:
        from tools.rag.ingestion_manager import ingest_source

        return ingest_source(
            source_type=source_type,
            tenant_id=tenant_id,
            project_id=project_id,
            limit=limit,
        )
    except ImportError:
        return {"error": "RAG ingestion manager not available"}
    except Exception as e:
        return {"error": str(e)}


def handle_rag_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get RAG ingestion and vector store status."""
    tenant_id = arguments.get("tenant_id", "")
    try:
        from tools.rag.ingestion_manager import get_status

        return get_status(tenant_id=tenant_id)
    except ImportError:
        return {"error": "RAG ingestion manager not available"}
    except Exception as e:
        return {"error": str(e)}


def handle_rag_chunk_info(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get info about a specific chunk by ID or content hash."""
    chunk_id = arguments.get("chunk_id", "")
    content_hash = arguments.get("content_hash", "")
    tenant_id = arguments.get("tenant_id", "")

    if not chunk_id and not content_hash:
        return {"error": "chunk_id or content_hash required"}

    try:
        from tools.rag.vector_store_factory import VectorStoreFactory

        store = VectorStoreFactory.create(tenant_id=tenant_id)

        if content_hash:
            chunk = store.get_by_content_hash(content_hash)
        else:
            # Search by chunk_id via direct DB query for SQLite
            chunk = None
            if store.provider_name == "sqlite":
                import sqlite3 as s3

                conn = s3.connect(str(store._db_path))
                conn.row_factory = s3.Row
                row = conn.execute(
                    "SELECT id, content, content_hash, source_type, source_id, "
                    "source_table, chunk_index, total_chunks, metadata, tier, "
                    "tenant_id, project_id, classification, created_at "
                    "FROM rag_chunks WHERE id = ?",
                    (chunk_id,),
                ).fetchone()
                conn.close()
                if row:
                    return {
                        "classification": "CUI // SP-CTI",
                        "chunk": dict(row),
                    }

        if chunk:
            return {
                "classification": "CUI // SP-CTI",
                "chunk": {
                    "chunk_id": chunk.chunk_id,
                    "content": chunk.content[:500],
                    "content_hash": chunk.content_hash,
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                    "source_table": chunk.source_table,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "tier": chunk.tier,
                },
            }
        return {"error": "Chunk not found"}
    except Exception as e:
        return {"error": str(e)}


def handle_rag_delete_source(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Delete all chunks from a specific source type."""
    source_type = arguments.get("source_type", "")
    tenant_id = arguments.get("tenant_id", "")

    if not source_type:
        return {"error": "source_type is required"}

    try:
        from tools.rag.vector_store_factory import VectorStoreFactory

        store = VectorStoreFactory.create(tenant_id=tenant_id)

        if store.provider_name != "sqlite":
            return {"error": "Delete by source only supported for SQLite backend"}

        import sqlite3 as s3

        conn = s3.connect(str(store._db_path))
        # Get IDs first
        ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM rag_chunks WHERE source_type = ?",
                (source_type,),
            ).fetchall()
        ]
        conn.close()

        if not ids:
            return {"deleted": 0, "source_type": source_type}

        deleted = store.delete(ids)
        return {
            "classification": "CUI // SP-CTI",
            "deleted": deleted,
            "source_type": source_type,
        }
    except Exception as e:
        return {"error": str(e)}


def handle_rag_retention_migrate(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run tier migration (hot→warm→cold)."""
    tenant_id = arguments.get("tenant_id", "")
    dry_run = arguments.get("dry_run", False)

    try:
        from tools.rag.retention_manager import migrate_chunks

        return migrate_chunks(tenant_id=tenant_id, dry_run=dry_run)
    except ImportError:
        return {"error": "Retention manager not available"}
    except Exception as e:
        return {"error": str(e)}


def handle_rag_reindex(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Re-index all sources (full sweep)."""
    tenant_id = arguments.get("tenant_id", "")
    sources = arguments.get("sources", None)

    try:
        from tools.rag.ingestion_manager import sweep_all

        source_list = None
        if sources:
            source_list = [s.strip() for s in sources.split(",")]
        return sweep_all(tenant_id=tenant_id, sources=source_list)
    except ImportError:
        return {"error": "RAG ingestion manager not available"}
    except Exception as e:
        return {"error": str(e)}


def handle_rag_retrieval_history(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get recent retrieval history from rag_retrieval_log."""
    limit = arguments.get("limit", 20)
    tenant_id = arguments.get("tenant_id", "")

    if not ICDEV_DB.exists():
        return {"error": "Database not found", "history": []}

    try:
        conn = _get_db()
        sql = """SELECT id, query_hash, results_count, top_score,
                        retrieval_mode, rerank_used, duration_ms,
                        tenant_id, created_at
                 FROM rag_retrieval_log"""
        params = []
        if tenant_id:
            sql += " WHERE tenant_id = ?"
            params.append(tenant_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return {
            "classification": "CUI // SP-CTI",
            "history": [dict(r) for r in rows],
            "count": len(rows),
        }
    except Exception as e:
        return {"error": str(e), "history": []}


def handle_rag_providers(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List available vector store backends and embedding providers."""
    try:
        from tools.rag.vector_store_factory import VectorStoreFactory

        backends = VectorStoreFactory.list_available()

        embedding_available = False
        try:
            from tools.llm import get_embedding_provider

            provider = get_embedding_provider()
            embedding_available = provider is not None
        except Exception:
            pass

        return {
            "classification": "CUI // SP-CTI",
            "vector_store_backends": backends,
            "embedding_available": embedding_available,
        }
    except Exception as e:
        return {"error": str(e)}
