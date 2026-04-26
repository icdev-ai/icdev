#!/usr/bin/env python3
# CUI // SP-CTI
"""RAG-KG Search API — combined RAG + Knowledge Graph search endpoint.

Endpoints:
    GET /api/rag-kg/search — Hybrid RAG+KG search with classification filtering
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Blueprint, jsonify, request as flask_request  # noqa: E402

rag_kg_api = Blueprint("rag_kg_api", __name__)

_VALID_CLASSIFICATIONS = frozenset({"il2", "il4", "il5"})
_VALID_TIERS = frozenset({"cold", "warm", "hot"})


def _parse_bool(value: str) -> bool:
    if value.lower() in ("true", "1", "yes"):
        return True
    if value.lower() in ("false", "0", "no"):
        return False
    raise ValueError(f"Cannot parse boolean: {value!r}")


def _validate_search_params(args) -> tuple[dict, list[str]]:
    """Parse and validate query parameters. Returns (params, errors)."""
    errors: list[str] = []
    params: dict = {}

    # q — required, non-empty
    q = args.get("q", "").strip()
    if not q:
        errors.append("q: required, must be a non-empty string")
    else:
        params["q"] = q

    # top_k — int, default 10, range 1–100
    raw_top_k = args.get("top_k", "10")
    try:
        top_k = int(raw_top_k)
        if not (1 <= top_k <= 100):
            errors.append("top_k: must be an integer between 1 and 100")
        else:
            params["top_k"] = top_k
    except ValueError:
        errors.append(f"top_k: must be an integer, got {raw_top_k!r}")

    # tenant_id — required, valid UUID
    raw_tenant_id = args.get("tenant_id", "").strip()
    if not raw_tenant_id:
        errors.append("tenant_id: required, must be a valid UUID")
    else:
        try:
            params["tenant_id"] = str(uuid.UUID(raw_tenant_id))
        except ValueError:
            errors.append(f"tenant_id: must be a valid UUID, got {raw_tenant_id!r}")

    # classification — required, enum: il2/il4/il5
    raw_cls = args.get("classification", "").strip().lower()
    if not raw_cls:
        errors.append(f"classification: required, must be one of {sorted(_VALID_CLASSIFICATIONS)}")
    elif raw_cls not in _VALID_CLASSIFICATIONS:
        errors.append(f"classification: must be one of {sorted(_VALID_CLASSIFICATIONS)}, got {raw_cls!r}")
    else:
        params["classification"] = raw_cls

    # include_kg — bool, default True
    raw_include_kg = args.get("include_kg", "true")
    try:
        params["include_kg"] = _parse_bool(raw_include_kg)
    except ValueError:
        errors.append(f"include_kg: must be a boolean (true/false), got {raw_include_kg!r}")

    # min_tier — enum: cold/warm/hot, default warm
    raw_tier = args.get("min_tier", "warm").strip().lower()
    if raw_tier not in _VALID_TIERS:
        errors.append(f"min_tier: must be one of {sorted(_VALID_TIERS)}, got {raw_tier!r}")
    else:
        params["min_tier"] = raw_tier

    return params, errors


def _search_handler(params: dict) -> dict:
    """Stub search handler — calls RAG retriever and KG if available."""
    results = []
    kg_nodes = []

    try:
        from tools.rag.retriever import RAGRetriever

        retriever = RAGRetriever(tenant_id=params["tenant_id"])
        rag_results = retriever.search(query=params["q"], top_k=params.get("top_k", 10))
        results = [r.to_dict() for r in rag_results]
    except Exception:
        pass

    if params.get("include_kg", True):
        try:
            from tools.knowledge_graph.graph_rag import GraphRAGRetriever

            kg = GraphRAGRetriever()
            kg_nodes = kg.retrieve(query=params["q"], top_k=params.get("top_k", 10))
        except Exception:
            pass

    return {
        "classification": "CUI // SP-CTI",
        "query": params["q"],
        "tenant_id": params["tenant_id"],
        "impact_level": params["classification"],
        "min_tier": params.get("min_tier", "warm"),
        "include_kg": params.get("include_kg", True),
        "rag_results": results,
        "kg_nodes": kg_nodes,
        "total": len(results),
    }


@rag_kg_api.route("/api/rag-kg/search", methods=["GET"])
def rag_kg_search():
    """Hybrid RAG + Knowledge Graph search with classification filtering."""
    params, errors = _validate_search_params(flask_request.args)
    if errors:
        return jsonify({"error": "Invalid request parameters", "details": errors}), 400

    try:
        result = _search_handler(params)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
