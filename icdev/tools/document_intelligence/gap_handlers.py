#!/usr/bin/env python3
# CUI // SP-CTI
"""DIC MCP gap handlers — thin wrappers for Document Intelligence Canvas MCP dispatch."""

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def handle_dic_ingest(params: dict) -> dict:
    try:
        from tools.document_intelligence.ingest_orchestrator import ingest_document
        return ingest_document(params)
    except Exception as exc:
        return {"error": str(exc), "status": "failed", "ingested": 0}


def handle_dic_search(params: dict) -> dict:
    try:
        from tools.document_intelligence.search_engine import search
        query = params.get("query", "")
        if not query:
            return {"error": "query is required", "results": []}
        return search(query, params)
    except Exception as exc:
        return {"error": str(exc), "results": []}


def handle_dic_generate(params: dict) -> dict:
    try:
        from tools.document_intelligence.doc_generator import generate
        return generate(params)
    except Exception as exc:
        return {"error": str(exc), "content": ""}


def handle_dic_chat(params: dict) -> dict:
    try:
        from tools.document_intelligence.search_engine import answer
        query = params.get("query", "") or params.get("message", "")
        if not query:
            return {"error": "query is required", "response": ""}
        return answer(query, params)
    except Exception as exc:
        return {"error": str(exc), "response": ""}
