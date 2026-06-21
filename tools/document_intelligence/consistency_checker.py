# CUI // SP-CTI
"""DIC Consistency Checker — cross-document concept overlap detection.

Uses the KG to find documents that share concept nodes with a changed document,
enabling propagation of review flags when source content is updated.

Public API:
  extract_changed_concepts(before, after) -> list[str]
  find_related_docs(doc_id, changed_concepts, tenant_id="", limit=20) -> list[dict]
"""
from __future__ import annotations

import re
from typing import Any

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Minimum character length for a term to be considered a concept noun phrase
_MIN_TERM_LEN = 3
# Common stop words to exclude from concept extraction
_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "from", "have", "will",
    "been", "are", "not", "but", "can", "all", "any", "each", "was",
    "its", "our", "has", "had", "may", "per", "via", "use", "used",
    "new", "also", "should", "which", "when", "then", "they", "their",
    "into", "more", "data", "base", "type", "list", "item", "note",
    "see", "set", "get", "run", "add", "one", "two",
})


# ── Concept extraction ─────────────────────────────────────────────────────────

def extract_changed_concepts(before: str, after: str) -> list[str]:
    """Extract noun phrases that appear in `after` but not `before`.

    Uses basic tokenization — no NLTK dependency. Returns deduplicated
    lowercase terms present in the updated text but absent from the original.
    Suitable for change detection in short technical sections.
    """
    before_tokens = _tokenize(before)
    after_tokens = _tokenize(after)
    new_terms = [t for t in after_tokens if t not in before_tokens and len(t) >= _MIN_TERM_LEN]
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for t in new_terms:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result[:50]  # cap to avoid oversized payloads


def _tokenize(text: str) -> set[str]:
    """Return lowercase word tokens, filtering stop words."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


# ── Related document finder ────────────────────────────────────────────────────

def find_related_docs(
    doc_id: str,
    changed_concepts: list[str],
    tenant_id: str = "",
    limit: int = 20,
) -> list[dict]:
    """Return docs that share KG concept nodes with the changed doc.

    Steps:
    1. Find KG node IDs where label matches any changed_concept (case-insensitive).
    2. Find all graph_ids that contain those nodes (excluding source doc's graphs).
    3. Resolve graph_id → source_doc_id via kg_graphs.
    4. Enrich with dic_documents metadata.
    All joins are Python-side to avoid SQL JSON dialect issues.
    """
    if not changed_concepts:
        return []

    try:
        with get_connection() as conn:
            return _find_related(conn, doc_id, changed_concepts, tenant_id, limit)
    except Exception as exc:
        logger.warning("consistency_checker.find_related_docs error: %s", exc)
        return []


def _find_related(conn, doc_id: str, changed_concepts: list[str],
                  tenant_id: str, limit: int) -> list[dict]:
    # Step 1: Find graph_ids for the source doc
    source_graphs: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT id FROM kg_graphs WHERE source_doc_id = %s",
            (doc_id,),
        ).fetchall()
        source_graphs = {_r(r, "id", 0) for r in rows}
    except Exception:
        pass

    # Step 2: Find graph_ids that contain nodes matching any concept
    # Python-side matching to avoid SQL JSON / LIKE combinatorics at scale
    matching_graph_map: dict[str, list[str]] = {}  # graph_id → matched concepts
    try:
        concept_lower = [c.lower() for c in changed_concepts]
        # Fetch all nodes (paginate for large KGs; capped at 5000 for performance)
        node_rows = conn.execute(
            "SELECT id, label, graph_id FROM kg_nodes LIMIT 5000"
        ).fetchall()
        for nr in node_rows:
            label = (_r(nr, "label", 1) or "").lower()
            graph_id = _r(nr, "graph_id", 2) or ""
            if graph_id in source_graphs:
                continue
            matched = [c for c in concept_lower if c in label or label in c]
            if matched:
                if graph_id not in matching_graph_map:
                    matching_graph_map[graph_id] = []
                matching_graph_map[graph_id].extend(matched)
    except Exception as exc:
        logger.debug("consistency_checker: node scan error: %s", exc)
        return []

    if not matching_graph_map:
        return []

    # Step 3: Resolve graph_id → source_doc_id
    graph_ids = list(matching_graph_map.keys())
    doc_ids_from_kg: dict[str, str] = {}  # doc_id → graph_id
    try:
        # Batch in chunks to avoid oversized IN lists
        chunk_size = 50
        for i in range(0, len(graph_ids), chunk_size):
            chunk = graph_ids[i: i + chunk_size]
            placeholders = ", ".join(["%s"] * len(chunk))
            rows = conn.execute(
                f"SELECT id, source_doc_id FROM kg_graphs WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            for r in rows:
                gid = _r(r, "id", 0)
                sdid = _r(r, "source_doc_id", 1) or ""
                if sdid and sdid != doc_id:
                    doc_ids_from_kg[sdid] = gid
    except Exception as exc:
        logger.debug("consistency_checker: graph resolve error: %s", exc)
        return []

    if not doc_ids_from_kg:
        return []

    # Step 4: Enrich with dic_documents metadata
    related: list[dict] = []
    for rdoc_id, graph_id in list(doc_ids_from_kg.items())[:limit]:
        meta = _get_doc_meta(conn, rdoc_id)
        matched_concepts = list(dict.fromkeys(matching_graph_map.get(graph_id, [])))
        related.append({
            "doc_id": rdoc_id,
            "doc_title": meta.get("title", rdoc_id),
            "collection_id": meta.get("collection_id", ""),
            "last_updated": meta.get("updated_at") or meta.get("created_at", ""),
            "matching_concepts": matched_concepts[:10],
        })

    return related[:limit]


def _get_doc_meta(conn, doc_id: str) -> dict:
    try:
        row = conn.execute(
            "SELECT doc_id, title, collection_id, created_at FROM dic_documents WHERE doc_id = %s",
            (doc_id,),
        ).fetchone()
        if row:
            return {
                "title": _r(row, "title", 1),
                "collection_id": _r(row, "collection_id", 2),
                "created_at": _r(row, "created_at", 3),
            }
    except Exception:
        pass
    return {}


def _r(row: Any, name: str, index: int) -> Any:
    """Safely access a row by name or index."""
    if isinstance(row, (list, tuple)):
        return row[index] if len(row) > index else None
    try:
        return row[name]
    except (KeyError, IndexError):
        try:
            return row[index]
        except Exception:
            return None
