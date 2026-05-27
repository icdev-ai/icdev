# [TEMPLATE: CUI // SP-CTI]
"""Shared GraphRAG /ask handler for per-canvas endpoints.

Each canvas blueprint (NDC/SDC/PDC/BDC/DDC/ODC/IDC) registers a thin
`/ask` page + `/api/ask` POST endpoint that delegates to
`handle_ask_request()` here. Keeps the GraphRAG + LLM narration logic in
one place instead of duplicating 80 lines across 7 blueprints.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from typing import Any, Dict

logger = get_logger("icdev.canvas_ask")


def handle_ask_request(
    query: str,
    graph_id: str,
    profile: str,
    top_k: int = 10,
    narrate: bool = False,
    canvas_label: str = "canvas",
) -> Dict[str, Any]:
    """Run GraphRAG retrieval (+ optional LLM narration) for one canvas.

    Returns a JSON-safe dict. Caller wraps in `jsonify(...)` and picks
    the HTTP status code (200 unless query is empty → 400).

    The narration call is graceful — if LLMRouter is unavailable or
    errors, returns narrated=False with narration=None and the caller
    can still show the raw graph hits.
    """
    if not query or not query.strip():
        return {"error": "query is required", "_status": 400}
    query = query.strip()

    try:
        from tools.knowledge_graph.graph_rag import retrieve
        kg = retrieve(
            query=query,
            graph_id=graph_id,
            profile=profile,
            top_k=top_k,
            compress=False,
        )
    except Exception as exc:
        logger.warning("canvas_ask[%s]: graph_rag.retrieve failed: %s", graph_id, exc)
        kg = {"nodes": [], "edges": [], "error": str(exc)[:200]}

    nodes = kg.get("nodes", []) if isinstance(kg, dict) else []
    edges = kg.get("edges", []) if isinstance(kg, dict) else []

    narration = None
    narrated = False
    if narrate and nodes:
        try:
            from tools.llm.router import LLMRouter
            prompt = (
                f"Answer the user's question about the {canvas_label} in 3-6 sentences "
                "using only the KG evidence below. Cite node labels inline.\n\n"
                f"QUESTION: {query}\n\n"
                f"NODES ({len(nodes)}): {json.dumps(nodes[:top_k], ensure_ascii=False)[:3000]}\n\n"
                f"EDGES ({len(edges)}): {json.dumps(edges[:top_k], ensure_ascii=False)[:1500]}\n"
            )
            r = LLMRouter().invoke(
                function="narrative_generation",
                prompt=prompt,
                max_tokens=400,
            )
            if isinstance(r, dict):
                narration = r.get("content") or r.get("text") or str(r)
            else:
                narration = str(r) if r else None
            narrated = bool(narration)
        except Exception as exc:
            logger.info("canvas_ask[%s]: narration skipped: %s", graph_id, exc)

    payload: Dict[str, Any] = {
        "query": query,
        "profile": profile,
        "graph_id": graph_id,
        "nodes_matched": kg.get("nodes_matched", len(nodes)) if isinstance(kg, dict) else len(nodes),
        "nodes_returned": len(nodes),
        "edges_returned": len(edges),
        "nodes": nodes,
        "edges": edges,
        "narration": narration,
        "narrated": narrated,
    }
    if isinstance(kg, dict) and kg.get("error"):
        payload["kg_error"] = kg["error"]
    # JSON-safe (flatten datetime etc.)
    return json.loads(json.dumps(payload, default=str))


def reindex_canvas_on_save(canvas: str) -> None:
    """Best-effort re-index hook for canvas save endpoints.

    Called after a design is created or updated so /ask reflects the
    change immediately instead of waiting up to the canvas_indexer
    reflex's 6-hour cycle. Swallows all errors — a save must never
    fail because the KG indexer had a bad day.
    """
    try:
        from tools.knowledge_graph.canvas_indexer import index_canvas
        index_canvas(canvas)
    except Exception as exc:
        logger.info("reindex_canvas_on_save[%s]: skipped (%s)", canvas, exc)
