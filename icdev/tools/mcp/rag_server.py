#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG MCP server — 14 tools for the RAG subsystem.

Phase A (12 tools): added kg_search, kg_expand, kg_to_rag_chunks
Phase B (13 tools): added rag_decompose — query decomposition planner
Phase C (14 tools): added rag_evaluate — per-chunk grading + self-eval loop

Each tool is registered via MCPServer.register_tool() and is also callable
from the unified MCP gateway via tool_registry.py.

Usage:
    python tools/mcp/rag_server.py          # Start MCP server (stdio)
    python tools/mcp/rag_server.py --help   # Show help
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.mcp.base_server import MCPServer  # noqa: E402

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

    if not query:
        return {"error": "query is required", "results": []}

    try:
        from tools.rag.retriever import RAGRetriever
        from tools.rag.retriever_common import run_rag_search

        # Map MCP-schema params onto RAGRetriever.search()'s real signature
        # (query, top_k, source_types, project_id, rerank, query_label). The
        # single ``source_type`` filter becomes the retriever's ``source_types``
        # list; tenant scoping is applied by run_rag_search constructing the
        # retriever with ``tenant_id=``. There is no retriever/provenance hook
        # for a caller-supplied agent/child id (provenance records a fixed
        # ``agent_id="rag_retriever"``), so ``child_id`` is not advertised.
        search_kwargs: Dict[str, Any] = {"top_k": top_k}
        if source_type:
            search_kwargs["source_types"] = [source_type]
        results = run_rag_search(
            RAGRetriever,
            query,
            tenant_id=tenant_id,
            **search_kwargs,
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
                    "FROM rag_chunks WHERE id = %s",
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
                "SELECT id FROM rag_chunks WHERE source_type = %s",
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


# ---------------------------------------------------------------------------
# Phase A — KG bridge tools (kg_search, kg_expand, kg_to_rag_chunks)
# ---------------------------------------------------------------------------


def handle_kg_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search the knowledge graph by keyword/semantic query and return top nodes."""
    query = arguments.get("query", "")
    top_k = int(arguments.get("top_k", 10))
    project_id = arguments.get("project_id", "")
    profile = arguments.get("profile") or None
    compress = bool(arguments.get("compress", False))

    if not query:
        return {"error": "query is required"}

    try:
        from tools.knowledge_graph.graph_rag import retrieve

        result = retrieve(
            query=query,
            project_id=project_id or None,
            profile=profile,
            top_k=top_k,
            compress=compress,
        )
        return {"classification": "CUI // SP-CTI", **result}
    except Exception as exc:
        return {"error": str(exc)}


def handle_kg_expand(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Expand a KG node by N hops and return all reachable nodes and edges."""
    node_id = arguments.get("node_id", "")
    hops = int(arguments.get("hops", 2))
    max_nodes = int(arguments.get("max_nodes", 50))

    if not node_id:
        return {"error": "node_id is required"}

    try:
        conn = _get_db()
        # BFS expansion
        visited_nodes: dict = {}
        visited_edges: list = []
        frontier = {node_id}
        seen_edges: set = set()

        for _ in range(hops):
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            node_rows = conn.execute(
                f"SELECT id, entity_type, label, properties FROM kg_nodes WHERE id IN ({placeholders})",
                list(frontier),
            ).fetchall()
            for r in node_rows:
                nid = r[0] if isinstance(r, (list, tuple)) else r["id"]
                if nid not in visited_nodes:
                    visited_nodes[nid] = {
                        "id": nid,
                        "entity_type": r[1] if isinstance(r, (list, tuple)) else r["entity_type"],
                        "label": r[2] if isinstance(r, (list, tuple)) else r["label"],
                        "properties": r[3] if isinstance(r, (list, tuple)) else r["properties"],
                    }

            if len(visited_nodes) >= max_nodes:
                break

            edge_rows = conn.execute(
                f"""
                SELECT id, source_id, target_id, relation_type, weight
                FROM kg_edges
                WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
                """,
                list(frontier) + list(frontier),
            ).fetchall()

            next_frontier: set = set()
            for r in edge_rows:
                eid = r[0] if isinstance(r, (list, tuple)) else r["id"]
                if eid in seen_edges:
                    continue
                seen_edges.add(eid)
                src = r[1] if isinstance(r, (list, tuple)) else r["source_id"]
                tgt = r[2] if isinstance(r, (list, tuple)) else r["target_id"]
                visited_edges.append({
                    "id": eid,
                    "source_id": src,
                    "target_id": tgt,
                    "relation_type": r[3] if isinstance(r, (list, tuple)) else r["relation_type"],
                    "weight": r[4] if isinstance(r, (list, tuple)) else r["weight"],
                })
                for n in (src, tgt):
                    if n not in visited_nodes:
                        next_frontier.add(n)

            frontier = next_frontier

        return {
            "classification": "CUI // SP-CTI",
            "root_node_id": node_id,
            "hops": hops,
            "node_count": len(visited_nodes),
            "edge_count": len(visited_edges),
            "nodes": list(visited_nodes.values())[:max_nodes],
            "edges": visited_edges,
        }
    except Exception as exc:
        return {"error": str(exc)}


def handle_kg_to_rag_chunks(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Convert KG node IDs to their originating RAG source chunks via source_chunk_id."""
    node_ids = arguments.get("node_ids", [])

    if not node_ids or not isinstance(node_ids, list):
        return {"error": "node_ids must be a non-empty list"}

    try:
        conn = _get_db()
        placeholders = ",".join("?" for _ in node_ids)
        kg_rows = conn.execute(
            f"SELECT id, label, source_chunk_id FROM kg_nodes WHERE id IN ({placeholders})",
            node_ids,
        ).fetchall()

        chunk_ids = []
        node_to_chunk: dict = {}
        for r in kg_rows:
            nid = r[0] if isinstance(r, (list, tuple)) else r["id"]
            cid = r[2] if isinstance(r, (list, tuple)) else r.get("source_chunk_id")
            if cid:
                chunk_ids.append(cid)
                node_to_chunk[nid] = cid

        chunks = []
        if chunk_ids:
            chunk_placeholders = ",".join("?" for _ in chunk_ids)
            rag_rows = conn.execute(
                f"""
                SELECT id, content, source_type, source_id, created_at
                FROM rag_chunks
                WHERE id IN ({chunk_placeholders})
                """,
                chunk_ids,
            ).fetchall()
            for r in rag_rows:
                chunks.append({
                    "id": r[0] if isinstance(r, (list, tuple)) else r["id"],
                    "content": r[1] if isinstance(r, (list, tuple)) else r["content"],
                    "source_type": r[2] if isinstance(r, (list, tuple)) else r["source_type"],
                    "source_id": r[3] if isinstance(r, (list, tuple)) else r["source_id"],
                    "created_at": r[4] if isinstance(r, (list, tuple)) else r["created_at"],
                })

        return {
            "classification": "CUI // SP-CTI",
            "node_ids_requested": len(node_ids),
            "nodes_with_chunk_link": len(node_to_chunk),
            "chunks_found": len(chunks),
            "node_to_chunk_map": node_to_chunk,
            "chunks": chunks,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Phase B — rag_decompose: query decomposition planner
# ---------------------------------------------------------------------------

_DECOMPOSE_PROMPT = """You are a query decomposition planner for a RAG system.

Given a complex query, break it into 2-4 atomic sub-queries. Each sub-query should:
- Be self-contained and answerable independently
- Map to ONE of these retrieval strategies:
  - rag_search: vector similarity search (facts, documents, summaries)
  - kg_search: knowledge graph traversal (entities, relationships, compliance controls)
  - corrective_rag: parallel multi-strategy (multi-hop reasoning, cross-domain)

Return ONLY a JSON array of objects: [{\"query\": \"...\", \"strategy\": \"...\", \"label\": \"...\"}]
Labels: fact_single | summary | reasoning | entity_lookup

Query: {query}
"""


def handle_rag_decompose(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Classify and decompose a complex query into atomic sub-queries with retrieval strategies."""
    query = arguments.get("query", "")
    context = arguments.get("context", "")
    max_sub_queries = int(arguments.get("max_sub_queries", 4))

    if not query:
        return {"error": "query is required"}

    try:
        from tools.rag.query_classifier import classify_query

        classification = classify_query(query, context)
        label = classification.get("label", "fact_single")
        confidence = classification.get("confidence", 0.5)

        # fact_single and unanswerable don't need decomposition
        needs_decomposition = label in ("reasoning", "summary")

        sub_queries = []
        method = "passthrough"

        if needs_decomposition:
            # Try scanner-tier LLM decomposition
            try:
                from tools.llm.router import LLMRouter
                from tools.llm.provider import LLMRequest
                import json as _json

                router = LLMRouter()
                prompt = _DECOMPOSE_PROMPT.format(query=query)
                resp = router.invoke(
                    "rag_decompose",
                    LLMRequest(
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.0,
                        max_tokens=512,
                    ),
                )
                # Extract JSON array from response
                text = (resp.content or "").strip()
                start = text.find("[")
                end = text.rfind("]") + 1
                if start >= 0 and end > start:
                    parsed = _json.loads(text[start:end])
                    sub_queries = [
                        {
                            "query": str(item.get("query", "")),
                            "strategy": item.get("strategy", "rag_search"),
                            "label": item.get("label", "fact_single"),
                        }
                        for item in parsed
                        if item.get("query")
                    ][:max_sub_queries]
                    method = "llm"
            except Exception:
                pass

        # Fallback: single sub-query = original query
        if not sub_queries:
            strategy = "kg_search" if label == "reasoning" else "rag_search"
            sub_queries = [{"query": query, "strategy": strategy, "label": label}]
            method = "passthrough" if not needs_decomposition else "llm_fallback"

        # Determine retrieval plan
        strategies = {sq["strategy"] for sq in sub_queries}
        if len(sub_queries) == 1:
            retrieval_plan = "single"
        elif len(strategies) > 1:
            retrieval_plan = "parallel"
        else:
            retrieval_plan = "sequential"

        return {
            "classification": "CUI // SP-CTI",
            "query": query,
            "label": label,
            "confidence": confidence,
            "needs_decomposition": needs_decomposition,
            "sub_queries": sub_queries,
            "retrieval_plan": retrieval_plan,
            "method": method,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Phase C — rag_evaluate: per-chunk grading + self-evaluating retrieval loop
# ---------------------------------------------------------------------------

_CHUNK_GRADE_PROMPT = """Rate the relevance of this context chunk to the query.

Query: {query}

Chunk:
{chunk}

Return JSON: {{"score": 0.0-1.0, "reason": "one sentence"}}
Score guide: 1.0=directly answers, 0.7=relevant context, 0.4=tangential, 0.1=irrelevant.
"""


def _grade_chunk(query: str, chunk_text: str) -> Dict[str, Any]:
    """Grade a single chunk's relevance to the query using scanner-tier LLM."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        import json as _json

        router = LLMRouter()
        prompt = _CHUNK_GRADE_PROMPT.format(query=query, chunk=chunk_text[:800])
        resp = router.invoke(
            "rag_evaluate",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=80,
            ),
        )
        text = (resp.content or "").strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = _json.loads(text[start:end])
            return {
                "score": float(data.get("score", 0.5)),
                "reason": str(data.get("reason", "")),
                "method": "llm",
            }
    except Exception:
        pass
    # Heuristic fallback: keyword overlap
    q_terms = set(query.lower().split())
    c_terms = set(chunk_text.lower().split())
    overlap = len(q_terms & c_terms) / max(len(q_terms), 1)
    return {"score": min(1.0, overlap * 2), "reason": "keyword_overlap", "method": "heuristic"}


def handle_rag_evaluate(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate RAG quality: grade chunks, score an answer, or run self-evaluating retrieval.

    Modes (select by providing the relevant arguments):
      - grade_chunks: provide query + chunks → per-chunk relevance scores
      - score_answer: provide query + answer + ground_truth → CRAG score
      - self_eval_retrieve: provide query → iterative retrieve-grade loop (max 3 iterations)
    """
    query = arguments.get("query", "")
    if not query:
        return {"error": "query is required"}

    mode = arguments.get("mode", "")
    chunks = arguments.get("chunks", [])
    answer = arguments.get("answer", "")
    ground_truth = arguments.get("ground_truth", "")
    max_iterations = int(arguments.get("max_iterations", 3))
    confidence_threshold = float(arguments.get("confidence_threshold", 0.7))

    # Auto-detect mode
    if not mode:
        if ground_truth:
            mode = "score_answer"
        elif chunks:
            mode = "grade_chunks"
        else:
            mode = "self_eval_retrieve"

    try:
        if mode == "score_answer":
            from tools.rag.crag_evaluator import CRAGScorer

            scorer = CRAGScorer()
            result = scorer.score_answer(
                answer=answer,
                ground_truth=ground_truth,
                question_type=arguments.get("question_type", "simple"),
                is_false_premise=bool(arguments.get("is_false_premise", False)),
            )
            return {
                "classification": "CUI // SP-CTI",
                "mode": "score_answer",
                "query": query,
                **result,
            }

        elif mode == "grade_chunks":
            if not chunks:
                return {"error": "chunks required for grade_chunks mode"}
            graded = []
            for i, chunk in enumerate(chunks):
                text = chunk.get("content", chunk) if isinstance(chunk, dict) else str(chunk)
                grade = _grade_chunk(query, text)
                graded.append({
                    "chunk_index": i,
                    "chunk_id": chunk.get("id", "") if isinstance(chunk, dict) else "",
                    **grade,
                })
            avg_score = sum(g["score"] for g in graded) / len(graded) if graded else 0.0
            return {
                "classification": "CUI // SP-CTI",
                "mode": "grade_chunks",
                "query": query,
                "chunk_count": len(graded),
                "average_relevance": round(avg_score, 3),
                "meets_threshold": avg_score >= confidence_threshold,
                "graded_chunks": graded,
            }

        else:  # self_eval_retrieve
            from tools.rag.retriever import RAGRetriever

            retriever = RAGRetriever()
            history = []
            current_query = query
            final_chunks: list = []
            final_score = 0.0

            for iteration in range(1, max_iterations + 1):
                results = retriever.retrieve(query=current_query, top_k=5)
                iter_chunks = [r.to_dict() if hasattr(r, "to_dict") else r for r in results]

                if not iter_chunks:
                    history.append({"iteration": iteration, "query": current_query,
                                    "chunks_retrieved": 0, "avg_relevance": 0.0})
                    break

                graded = [
                    _grade_chunk(current_query, c.get("content", str(c)))
                    for c in iter_chunks
                ]
                avg = sum(g["score"] for g in graded) / len(graded)
                history.append({
                    "iteration": iteration,
                    "query": current_query,
                    "chunks_retrieved": len(iter_chunks),
                    "avg_relevance": round(avg, 3),
                })

                final_chunks = iter_chunks
                final_score = avg

                if avg >= confidence_threshold:
                    break

                # Refine query for next iteration: append "detailed" or use KG
                if iteration < max_iterations:
                    current_query = f"{query} detailed explanation"

            return {
                "classification": "CUI // SP-CTI",
                "mode": "self_eval_retrieve",
                "query": query,
                "iterations_run": len(history),
                "final_avg_relevance": round(final_score, 3),
                "converged": final_score >= confidence_threshold,
                "threshold": confidence_threshold,
                "iteration_history": history,
                "final_chunks": final_chunks,
            }

    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# MCPServer — register all 14 RAG tools
# ---------------------------------------------------------------------------


def build_server() -> MCPServer:
    """Build and return a configured RAG MCP server."""
    server = MCPServer(name="icdev-rag", version="1.0.0")

    server.register_tool(
        name="rag_search",
        description=(
            "Search ICDEV™ RAG knowledge base with natural language query. "
            "Returns ranked results from all indexed sources (innovation signals, "
            "compliance artifacts, research dossiers, etc.)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Number of results to return (default 5)", "default": 5},
                "source_type": {"type": "string", "description": "Filter by source type (optional)"},
                "tenant_id": {"type": "string", "description": "Tenant ID for multi-tenant isolation"},
                "child_id": {"type": "string", "description": "Child app ID for federated queries"},
            },
            "required": ["query"],
        },
        handler=handle_rag_search,
    )

    server.register_tool(
        name="rag_ingest",
        description=(
            "Ingest a source type into the RAG vector store. "
            "Chunks content, embeds via Ollama nomic-embed-text, and stores with dedup."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source_type": {
                    "type": "string",
                    "description": "Source type key (e.g., innovation_signals, creative_pain_points)",
                },
                "tenant_id": {"type": "string"},
                "project_id": {"type": "string"},
                "limit": {"type": "integer", "description": "Max rows to process (0 = all)", "default": 0},
            },
            "required": ["source_type"],
        },
        handler=handle_rag_ingest,
    )

    server.register_tool(
        name="rag_status",
        description="Get RAG ingestion and vector store status including chunk counts by source type and tier.",
        input_schema={
            "type": "object",
            "properties": {"tenant_id": {"type": "string", "description": "Tenant ID (optional)"}},
            "required": [],
        },
        handler=handle_rag_status,
    )

    server.register_tool(
        name="rag_chunk_info",
        description="Get detailed info about a specific RAG chunk by ID or content hash.",
        input_schema={
            "type": "object",
            "properties": {
                "chunk_id": {"type": "string", "description": "Chunk ID"},
                "content_hash": {"type": "string", "description": "Content SHA-256 hash"},
                "tenant_id": {"type": "string"},
            },
            "required": [],
        },
        handler=handle_rag_chunk_info,
    )

    server.register_tool(
        name="rag_delete_source",
        description="Delete all chunks from a specific source type in the vector store.",
        input_schema={
            "type": "object",
            "properties": {
                "source_type": {"type": "string", "description": "Source type to delete"},
                "tenant_id": {"type": "string"},
            },
            "required": ["source_type"],
        },
        handler=handle_rag_delete_source,
    )

    server.register_tool(
        name="rag_retention_migrate",
        description=(
            "Run tier migration: hot→warm (float16 compression) and warm→cold "
            "(metadata only) based on age thresholds."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "dry_run": {"type": "boolean", "description": "Report candidates without migrating", "default": False},
            },
            "required": [],
        },
        handler=handle_rag_retention_migrate,
    )

    server.register_tool(
        name="rag_reindex",
        description="Re-index all or specified sources by running a full ingestion sweep.",
        input_schema={
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "sources": {
                    "type": "string",
                    "description": "Comma-separated source types to re-index (default: all)",
                },
            },
            "required": [],
        },
        handler=handle_rag_reindex,
    )

    server.register_tool(
        name="rag_retrieval_history",
        description="Get recent retrieval history from the RAG retrieval log (NIST AU-3 compliant).",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                "tenant_id": {"type": "string"},
            },
            "required": [],
        },
        handler=handle_rag_retrieval_history,
    )

    server.register_tool(
        name="rag_providers",
        description="List available vector store backends (SQLite, ChromaDB, FAISS) and embedding provider status.",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=handle_rag_providers,
    )

    # ── Phase A — KG bridge ──────────────────────────────────────────────────

    server.register_tool(
        name="kg_search",
        description=(
            "Search the knowledge graph by keyword/semantic query. Returns scored nodes "
            "with optional LLM context compression. Use profile='compliance' for control mapping, "
            "'security' for threat context, 'exploratory' for open-ended discovery."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "default": 10},
                "project_id": {"type": "string"},
                "profile": {
                    "type": "string",
                    "description": "Scoring profile: compliance | security | exploratory | provenance",
                },
                "compress": {"type": "boolean", "default": False,
                             "description": "Apply scanner-tier LLM context compression"},
            },
            "required": ["query"],
        },
        handler=handle_kg_search,
    )

    server.register_tool(
        name="kg_expand",
        description=(
            "Expand a knowledge graph node by N hops (BFS), returning all reachable nodes and edges. "
            "Use after kg_search to explore entity neighborhoods for multi-hop reasoning."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "description": "Root KG node ID to expand from"},
                "hops": {"type": "integer", "default": 2, "description": "BFS depth (1-4)"},
                "max_nodes": {"type": "integer", "default": 50},
            },
            "required": ["node_id"],
        },
        handler=handle_kg_expand,
    )

    server.register_tool(
        name="kg_to_rag_chunks",
        description=(
            "Convert KG node IDs to their originating RAG source chunks via source_chunk_id linkage. "
            "Use after kg_expand to retrieve the original document context for KG-anchored nodes."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of KG node IDs to resolve",
                },
                "tenant_id": {"type": "string"},
            },
            "required": ["node_ids"],
        },
        handler=handle_kg_to_rag_chunks,
    )

    # ── Phase B — Query decomposition ────────────────────────────────────────

    server.register_tool(
        name="rag_decompose",
        description=(
            "Classify a query and decompose complex queries into atomic sub-queries with "
            "per-sub-query retrieval strategy assignments (rag_search / kg_search / corrective_rag). "
            "Returns the retrieval plan: single | sequential | parallel."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query to classify and decompose"},
                "context": {"type": "string", "description": "Optional retrieved context for answerability check"},
                "max_sub_queries": {"type": "integer", "default": 4},
            },
            "required": ["query"],
        },
        handler=handle_rag_decompose,
    )

    # ── Phase C — Evaluation ─────────────────────────────────────────────────

    server.register_tool(
        name="rag_evaluate",
        description=(
            "Evaluate RAG quality in one of three modes:\n"
            "• grade_chunks — score each retrieved chunk's relevance to the query (0-1)\n"
            "• score_answer — CRAG rubric scoring against ground truth (perfect/acceptable/missing/incorrect)\n"
            "• self_eval_retrieve — iterative retrieve-grade loop: retrieves, grades, refines query, "
            "  stops when avg relevance ≥ threshold (default 0.7) or max_iterations reached"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {
                    "type": "string",
                    "description": "grade_chunks | score_answer | self_eval_retrieve (auto-detected if omitted)",
                },
                "chunks": {
                    "type": "array",
                    "description": "Chunks to grade (grade_chunks mode)",
                    "items": {"type": ["object", "string"]},
                },
                "answer": {"type": "string", "description": "Answer to score (score_answer mode)"},
                "ground_truth": {"type": "string", "description": "Reference answer (score_answer mode)"},
                "question_type": {"type": "string", "default": "simple"},
                "is_false_premise": {"type": "boolean", "default": False},
                "max_iterations": {"type": "integer", "default": 3},
                "confidence_threshold": {"type": "number", "default": 0.7},
            },
            "required": ["query"],
        },
        handler=handle_rag_evaluate,
    )

    return server


# ---------------------------------------------------------------------------
# CLI / entry point
# ---------------------------------------------------------------------------


def main():
    """Build and run the RAG MCP server over stdio."""
    build_server().run()


if __name__ == "__main__":
    main()
