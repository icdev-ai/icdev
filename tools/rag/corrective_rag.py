#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Corrective RAG — parallel multi-strategy retrieval (D-KARL-3).

Fires three retrieval strategies concurrently via ThreadPoolExecutor:
    1. RAG vector retrieval (tools/rag/retriever.py)
    2. GraphRAG profile-aware retrieval (tools/knowledge_graph/graph_rag.py)
    3. Source registry keyword scan (tools/rag/source_registry.py)

Results are merged, deduplicated, and optionally compressed via scanner-tier
LLM (qwen3.5, zero Claude tokens).

Usage:
    python tools/rag/corrective_rag.py --parallel --query "NIST compliance gaps" --json
    python tools/rag/corrective_rag.py --parallel --query "zero trust" --profile compliance --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.rag.corrective_rag")

ICDEV_DB = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Load knowledge_graph_config.yaml for parallel retrieval settings."""
    config_path = BASE_DIR / "args" / "knowledge_graph_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Strategy 1: RAG Vector Retrieval
# ---------------------------------------------------------------------------


def _strategy_rag_vector(
    query: str,
    top_k: int = 5,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run RAG two-stage retrieval pipeline."""
    try:
        from tools.rag.retriever import RAGRetriever

        retriever = RAGRetriever()
        results = retriever.search(query=query, top_k=top_k, project_id=project_id or "")
        documents = []
        for r in results:
            documents.append(
                {
                    "content": r.content,
                    "source": r.source_type or "rag_vector",
                    "id": r.chunk_id or "",
                    "score": getattr(r, "final_score", r.score),
                    "metadata": r.metadata if hasattr(r, "metadata") else {},
                }
            )
        return {"strategy": "rag_vector", "documents": documents, "count": len(documents)}
    except Exception as exc:
        logger.debug("RAG vector strategy failed: %s", exc)
        return {"strategy": "rag_vector", "documents": [], "count": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Strategy 2: GraphRAG Profile-Aware Retrieval
# ---------------------------------------------------------------------------


def _strategy_graphrag(
    query: str,
    top_k: int = 5,
    profile: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run GraphRAG with scoring profiles (D-KARL-1)."""
    try:
        from tools.knowledge_graph.graph_rag import retrieve as graphrag_retrieve

        result = graphrag_retrieve(
            query=query,
            project_id=project_id,
            profile=profile,
            top_k=top_k,
            compress=False,
        )
        # Normalize GraphRAG output into documents list
        documents = []
        nodes = result.get("nodes", [])
        context = result.get("context", "")
        if nodes:
            for node in nodes:
                documents.append(
                    {
                        "content": f"{node.get('label', '')} ({node.get('entity_type', '')})",
                        "source": "graphrag",
                        "id": node.get("id", ""),
                        "score": node.get("score", 0.0),
                        "metadata": {
                            "entity_type": node.get("entity_type", ""),
                            "centrality": node.get("centrality", 0.0),
                            "profile": result.get("profile_used", ""),
                        },
                    }
                )
        elif context:
            # Fallback: use the full context as a single document
            documents.append(
                {
                    "content": context,
                    "source": "graphrag",
                    "id": "graphrag_context",
                    "score": 0.5,
                    "metadata": {"profile": result.get("profile_used", "")},
                }
            )
        return {"strategy": "graphrag", "documents": documents, "count": len(documents)}
    except Exception as exc:
        logger.debug("GraphRAG strategy failed: %s", exc)
        return {"strategy": "graphrag", "documents": [], "count": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Strategy 3: Source Registry Keyword Scan
# ---------------------------------------------------------------------------


def _strategy_source_scan(
    query: str,
    top_k: int = 5,
) -> Dict[str, Any]:
    """Scan source registry tables for keyword matches."""
    try:
        from tools.rag.source_registry import SOURCE_REGISTRY
        from tools.db.storage import get_connection

        query_terms = query.lower().split()
        documents = []

        # Scan highest-priority realtime sources
        sources = sorted(
            [(k, v) for k, v in SOURCE_REGISTRY.items() if v.get("mode") == "realtime"],
            key=lambda x: x[1].get("priority", 99),
        )

        for source_key, cfg in sources[:6]:
            try:
                content_cols = cfg.get("content_cols", [])
                if not content_cols:
                    continue

                db_name = cfg.get("db", "icdev")
                conn = get_connection(db_name)
                if conn is None:
                    continue

                # Build LIKE query for keyword matching
                table = cfg["table"]
                pk = cfg.get("pk", "id")
                col_concat = " || ' ' || ".join(f"COALESCE({c}, '')" for c in content_cols)
                where_clauses = [f"({col_concat}) LIKE ?" for _ in query_terms]
                params = [f"%{term}%" for term in query_terms]

                if not where_clauses:
                    continue

                sql = (  # nosec B608 — table/pk/content_cols from hardcoded SOURCE_REGISTRY, not user input
                    f"SELECT {pk}, {', '.join(content_cols)} FROM {table} "  # nosec B608 -- table/column names are internal constants, not user input
                    f"WHERE {' AND '.join(where_clauses)} "
                    f"LIMIT ?"
                )
                params.append(top_k)

                cursor = conn.execute(sql, params)
                rows = cursor.fetchall()

                for row in rows:
                    row_id = row[0]
                    content_parts = [str(row[i + 1]) for i in range(len(content_cols)) if row[i + 1]]
                    content = " — ".join(content_parts)
                    if content.strip():
                        documents.append(
                            {
                                "content": content,
                                "source": f"source_scan:{source_key}",
                                "id": str(row_id),
                                "score": 0.3,
                                "metadata": {"source_type": source_key, "table": table},
                            }
                        )
            except Exception:
                continue

        # Trim to top_k
        documents = documents[:top_k]
        return {"strategy": "source_scan", "documents": documents, "count": len(documents)}
    except Exception as exc:
        logger.debug("Source scan strategy failed: %s", exc)
        return {"strategy": "source_scan", "documents": [], "count": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Aggregation / Compression
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    """SHA-256 hash for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _deduplicate(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate documents by content hash."""
    seen: set = set()
    unique: list = []
    for doc in documents:
        h = _content_hash(doc.get("content", ""))
        if h not in seen:
            seen.add(h)
            unique.append(doc)
    return unique


def _aggregate_with_llm(
    strategy_results: Dict[str, Dict[str, Any]],
    query: str,
) -> str:
    """Compress multi-strategy results via scanner-tier LLM (qwen3.5)."""
    try:
        from tools.llm.router import LLMRouter

        router = LLMRouter()

        # Collect all documents into a context block
        all_docs = []
        for strategy_name, data in strategy_results.items():
            for doc in data.get("documents", []):
                all_docs.append(f"[{strategy_name}] {doc.get('content', '')}")

        if not all_docs:
            return ""

        context = "\n".join(all_docs[:20])  # Cap at 20 docs
        prompt = (
            f"Synthesize the following retrieval results into a concise, "
            f"unified context paragraph for the query: '{query}'\n\n{context}"
        )

        from tools.llm.provider import LLMRequest

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a concise knowledge synthesizer. Combine the retrieval results into a single coherent context.",
        )
        result = router.invoke("memory_consolidation", request)
        return result.content if result and result.content else ""
    except Exception as exc:
        logger.debug("LLM aggregation failed, returning raw context: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------


def parallel_retrieve(
    query: str,
    project_id: Optional[str] = None,
    top_k: int = 5,
    aggregate: bool = False,
    profile: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute parallel multi-strategy retrieval (D-KARL-3).

    Args:
        query: Natural language search query.
        project_id: Optional project scope.
        top_k: Max results per strategy.
        aggregate: If True, synthesize results via scanner-tier LLM.
        profile: GraphRAG scoring profile (compliance/exploratory/provenance/security).
        sources: List of strategies to run. Default: all three.

    Returns:
        Dict with strategy_results, merged documents, and optional aggregated context.
    """
    config = _load_config()
    parallel_cfg = config.get("parallel_retrieval", {})
    max_workers = parallel_cfg.get("max_workers", 3)

    available_strategies = sources or ["rag_vector", "graphrag", "source_scan"]
    strategy_fns = {
        "rag_vector": lambda: _strategy_rag_vector(query, top_k, project_id),
        "graphrag": lambda: _strategy_graphrag(query, top_k, profile, project_id),
        "source_scan": lambda: _strategy_source_scan(query, top_k),
    }

    start_time = time.time()
    strategy_results: Dict[str, Dict[str, Any]] = {}

    # Fire strategies in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for name in available_strategies:
            if name in strategy_fns:
                futures[executor.submit(strategy_fns[name])] = name

        for future in as_completed(futures, timeout=30):
            name = futures[future]
            try:
                strategy_results[name] = future.result(timeout=15)
            except Exception as exc:
                strategy_results[name] = {
                    "strategy": name,
                    "documents": [],
                    "count": 0,
                    "error": str(exc),
                }

    # Merge and deduplicate across strategies
    all_documents = []
    for data in strategy_results.values():
        all_documents.extend(data.get("documents", []))

    # Sort by score descending, then deduplicate
    all_documents.sort(key=lambda d: d.get("score", 0), reverse=True)
    merged = _deduplicate(all_documents)[: top_k * 2]

    # Optional aggregation via scanner-tier LLM
    aggregated_context = ""
    if aggregate and merged:
        aggregated_context = _aggregate_with_llm(strategy_results, query)

    elapsed = time.time() - start_time

    return {
        "status": "ok",
        "query": query,
        "project_id": project_id,
        "profile": profile,
        "strategy_results": strategy_results,
        "merged_documents": merged,
        "total_results": len(merged),
        "aggregated_context": aggregated_context,
        "strategies_run": list(strategy_results.keys()),
        "strategies_succeeded": [k for k, v in strategy_results.items() if "error" not in v],
        "elapsed_seconds": round(elapsed, 3),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Corrective RAG — parallel multi-strategy retrieval (D-KARL-3)",
    )
    parser.add_argument("--parallel", action="store_true", help="Run strategies in parallel")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--profile", help="GraphRAG scoring profile")
    parser.add_argument("--project-id", help="Project scope")
    parser.add_argument("--top-k", type=int, default=5, help="Max results per strategy")
    parser.add_argument("--aggregate", action="store_true", help="LLM-synthesize results")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result = parallel_retrieve(
        query=args.query,
        project_id=args.project_id,
        top_k=args.top_k,
        aggregate=args.aggregate,
        profile=args.profile,
    )

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Query: {result['query']}")
        print(f"Strategies: {', '.join(result['strategies_run'])}")
        print(f"Succeeded: {', '.join(result['strategies_succeeded'])}")
        print(f"Total results: {result['total_results']}")
        print(f"Elapsed: {result['elapsed_seconds']}s")
        if result["merged_documents"]:
            print("\n--- Top Results ---")
            for i, doc in enumerate(result["merged_documents"][:10], 1):
                score = doc.get("score", 0)
                source = doc.get("source", "?")
                content = doc.get("content", "")[:120]
                print(f"  {i}. [{source}] (score={score:.3f}) {content}")


if __name__ == "__main__":
    main()
