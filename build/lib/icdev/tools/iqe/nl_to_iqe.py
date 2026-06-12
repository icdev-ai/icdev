"""IQE NL-to-IQE translator — converts natural language questions to IQE query strings."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
import re
from typing import Any

logger = get_logger(__name__)

_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_OLLAMA_MODEL = os.environ.get("OLLAMA_IQE_MODEL", os.environ.get("OLLAMA_TOPO_MODEL", "qwen3.5:latest"))
_LLM_TIMEOUT = int(os.environ.get("OLLAMA_IQE_TIMEOUT", "30"))

_IQE_SYSTEM_PROMPT = """You are an IQE (ICDEV Query Engine) query generator.
IQE syntax:
  foreach <var> in <collection> [where <predicate>] [select <fields>]

Predicates: ==, !=, >, <, >=, <=, contains, startswith, and, or, not
Literals: strings in "quotes", numbers, true, false, null

Examples:
  foreach n in nodes select *
  foreach n in nodes where n.type == "router" select n.label, n.type
  foreach n in nodes where n.label contains "core" select n.label, n.id
  foreach e in edges where e.protocol == "BGP" select *

Given the user question and available collections, output ONLY the IQE query — no explanation, no markdown.
If you cannot generate a valid query, output: foreach n in nodes select *"""


def _pattern_translate(question: str, collections: list[str]) -> dict[str, Any] | None:
    """Try simple pattern-based translation before calling LLM."""
    q = question.lower().strip()

    default_coll = collections[0] if collections else "nodes"

    # "show all X" / "list all X" / "get all X"
    m = re.search(r"\b(?:show|list|get|display)\s+all\s+(\w+)", q)
    if m:
        ctype = m.group(1).rstrip("s")  # rough singularize
        # find matching collection
        coll = next((c for c in collections if ctype in c.lower()), default_coll)
        iqe = f"foreach n in {coll} select *"
        return {"iqe": iqe, "explanation": f"Select all records from {coll}"}

    # "how many X" / "count X"
    m = re.search(r"\b(?:how many|count)\s+(\w+)", q)
    if m:
        ctype = m.group(1).rstrip("s")
        coll = next((c for c in collections if ctype in c.lower()), default_coll)
        iqe = f"foreach n in {coll} select *"
        return {"iqe": iqe, "explanation": f"Count all records in {coll} (row_count in response)"}

    # "X of type Y" / "X where type is Y"
    m = re.search(r"\b(\w+)\s+(?:of\s+)?type\s+['\"]?(\w+)['\"]?", q)
    if m:
        coll = next((c for c in collections if m.group(1).rstrip("s") in c.lower()), default_coll)
        dtype = m.group(2)
        iqe = f'foreach n in {coll} where n.type == "{dtype}" select *'
        return {"iqe": iqe, "explanation": f'Filter {coll} by type == "{dtype}"'}

    # "X named/labeled/called Y"
    m = re.search(r'\b(?:named|labeled|called)\s+["\']?([A-Za-z0-9_\-]+)["\']?', q)
    if m:
        label = m.group(1)
        iqe = f'foreach n in {default_coll} where n.label contains "{label}" select *'
        return {"iqe": iqe, "explanation": f'Filter {default_coll} where label contains "{label}"'}

    return None


def _llm_translate(question: str, collections: list[str]) -> dict[str, Any]:
    """Use Ollama to translate question → IQE query."""
    try:
        from tools.http.client import request as _http_request
    except ImportError:
        return _fallback(collections)

    coll_hint = ", ".join(collections) if collections else "nodes"
    user_msg = f"Available collections: {coll_hint}\n\nQuestion: {question}"
    payload = {
        "model": _OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _IQE_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 128, "think": False},
    }
    try:
        resp = _http_request("POST", f"{_OLLAMA_HOST}/api/chat", json=payload, timeout=_LLM_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        raw = (data.get("message") or {}).get("content", "").strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        if raw.lower().startswith("foreach"):
            return {"iqe": raw, "explanation": f"LLM-generated IQE for: {question}"}
    except Exception as exc:
        logger.debug("IQE LLM translation failed: %s", exc)

    return _fallback(collections)


def _fallback(collections: list[str]) -> dict[str, Any]:
    coll = collections[0] if collections else "nodes"
    return {"iqe": f"foreach n in {coll} select *", "explanation": "Default: select all from primary collection"}


def nl_to_iqe(question: str, collections: list[str]) -> dict[str, Any]:
    """Translate a natural language *question* to an IQE query string.

    Args:
        question:    Free-form natural language question.
        collections: Available collection names (e.g. ["nodes", "edges"]).

    Returns:
        dict with keys:
            iqe (str)         — the IQE query string
            explanation (str) — human-readable description of what it does
    """
    question = (question or "").strip()
    if not question:
        return _fallback(collections)

    result = _pattern_translate(question, collections)
    if result:
        return result

    return _llm_translate(question, collections)
