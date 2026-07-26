# CUI // SP-CTI
"""DIC Consistency Checker — cross-document concept overlap detection.

Uses the KG to find documents that share concept nodes with a changed document,
enabling propagation of review flags when source content is updated.

Public API:
  extract_changed_concepts(before, after) -> list[str]
  find_related_docs(doc_id, changed_concepts, tenant_id="", limit=20) -> list[dict]
  find_docs_citing_changed_entities(changed_entities, min_overlap=1, ...) -> list[dict]
  check_numeric_claims(sections) -> list[dict]
  check_version_consistency(version_id) -> dict
"""
from __future__ import annotations

import json
import re
from typing import Any

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger
from tools.quality.content_grounding import (
    check_numeric_claims as _grounding_numeric_claims,
    placeholder_findings as _grounding_placeholder_findings,
)

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


def _docs_by_concept_overlap(
    conn,
    concepts: list[str],
    exclude_doc_id: str = "",
) -> dict[str, list[str]]:
    """Core KG concept-overlap traversal — shared by find_related_docs and
    find_docs_citing_changed_entities so both use ONE implementation.

    Matches ``kg_nodes`` labels against ``concepts`` (case-insensitive, substring
    either direction), groups matches by graph, resolves ``graph_id`` ->
    ``source_doc_id`` via ``kg_graphs``, and returns
    ``{doc_id: [matched concepts, de-duped]}``. Graphs belonging to
    ``exclude_doc_id`` are skipped so a source document never matches itself.
    All matching is Python-side to avoid SQL JSON / LIKE dialect issues.
    """
    if not concepts:
        return {}

    # Step 1: graphs owned by the excluded (source) doc, if any.
    exclude_graphs: set[str] = set()
    if exclude_doc_id:
        try:
            rows = conn.execute(
                "SELECT id FROM kg_graphs WHERE source_doc_id = %s",
                (exclude_doc_id,),
            ).fetchall()
            exclude_graphs = {_r(r, "id", 0) for r in rows}
        except Exception:
            pass

    # Step 2: scan nodes, group graph_id -> matched concepts.
    matching_graph_map: dict[str, list[str]] = {}
    try:
        concept_lower = [c.lower() for c in concepts if c]
        node_rows = conn.execute(
            "SELECT id, label, graph_id FROM kg_nodes LIMIT 5000"
        ).fetchall()
        for nr in node_rows:
            label = (_r(nr, "label", 1) or "").lower()
            graph_id = _r(nr, "graph_id", 2) or ""
            if not label or graph_id in exclude_graphs:
                continue
            matched = [c for c in concept_lower if c in label or label in c]
            if matched:
                matching_graph_map.setdefault(graph_id, []).extend(matched)
    except Exception as exc:
        logger.debug("consistency_checker: node scan error: %s", exc)
        return {}

    if not matching_graph_map:
        return {}

    # Step 3: resolve graph_id -> source_doc_id, aggregating matches per doc
    # (a doc may own multiple graphs).
    graph_ids = list(matching_graph_map.keys())
    docs_matched: dict[str, list[str]] = {}
    try:
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
                if sdid and sdid != exclude_doc_id:
                    docs_matched.setdefault(sdid, []).extend(
                        matching_graph_map.get(gid, [])
                    )
    except Exception as exc:
        logger.debug("consistency_checker: graph resolve error: %s", exc)
        return {}

    # De-duplicate matched concepts per doc, preserving order.
    return {d: list(dict.fromkeys(m)) for d, m in docs_matched.items()}


def _find_related(conn, doc_id: str, changed_concepts: list[str],
                  tenant_id: str, limit: int) -> list[dict]:
    docs_matched = _docs_by_concept_overlap(conn, changed_concepts, exclude_doc_id=doc_id)
    if not docs_matched:
        return []

    # Enrich with dic_documents metadata.
    related: list[dict] = []
    for rdoc_id, matched_concepts in list(docs_matched.items())[:limit]:
        meta = _get_doc_meta(conn, rdoc_id)
        related.append({
            "doc_id": rdoc_id,
            "doc_title": meta.get("title", rdoc_id),
            "collection_id": meta.get("collection_id", ""),
            "last_updated": meta.get("updated_at") or meta.get("created_at", ""),
            "matching_concepts": matched_concepts[:10],
        })

    return related[:limit]


# ── Blast radius: docs citing N+ changed entities (kg-blast-radius) ────────────

def find_docs_citing_changed_entities(
    changed_entities: list[str],
    *,
    min_overlap: int = 1,
    tenant_id: str = "",
    limit: int = 200,
) -> list[dict]:
    """Return documents whose cited KG entities overlap the changed-entity set
    by at least ``min_overlap`` — the *semantic blast radius* of an entity change.

    When tracked entities change (docmod evidence updates, ``kg_temporal_diff``
    output), this answers "which documents cite N-or-more of the entities that
    just changed?". It extends the same concept-overlap KG traversal used by
    :func:`find_related_docs` (shared :func:`_docs_by_concept_overlap`) — it does
    NOT fork a parallel matcher.

    Args:
        changed_entities: labels/aliases of the entities that changed.
        min_overlap: minimum number of distinct changed entities a doc must cite
            to be flagged (the "N" in "cites N+ changed entities").
        tenant_id: reserved for RLS (KG reads already go through get_connection()).
        limit: max flagged docs to return.

    Returns:
        ``[{doc_id, doc_title, collection_id, last_updated, matched_entities,
        overlap_count}]`` sorted by overlap_count descending. Empty on no match
        or on any KG read error (fail-soft — a freshness scan must never crash).
    """
    if not changed_entities or min_overlap < 1:
        return []

    try:
        with get_connection() as conn:
            docs_matched = _docs_by_concept_overlap(conn, changed_entities)
            if not docs_matched:
                return []

            flagged: list[dict] = []
            for rdoc_id, matched in docs_matched.items():
                if len(matched) < min_overlap:
                    continue
                meta = _get_doc_meta(conn, rdoc_id)
                flagged.append({
                    "doc_id": rdoc_id,
                    "doc_title": meta.get("title", rdoc_id),
                    "collection_id": meta.get("collection_id", ""),
                    "last_updated": meta.get("updated_at") or meta.get("created_at", ""),
                    "matched_entities": matched[:20],
                    "overlap_count": len(matched),
                })
    except Exception as exc:
        logger.warning("consistency_checker.find_docs_citing_changed_entities error: %s", exc)
        return []

    flagged.sort(key=lambda d: d["overlap_count"], reverse=True)
    return flagged[:limit]


# ── Cross-section numeric / placeholder consistency (ground-dic-05) ───────────

def _normalize_sections(sections: list[dict]) -> list[dict]:
    """Map dic_sections rows (heading/content) to the item_number/content
    shape expected by tools.quality.content_grounding."""
    normalized = []
    for s in sections:
        normalized.append({
            "item_number": s.get("heading") or s.get("item_number")
                           or s.get("section_id") or s.get("title") or "?",
            "content": s.get("content") or s.get("ai_draft") or "",
        })
    return normalized


def check_numeric_claims(sections: list[dict]) -> list[dict]:
    """Detect cross-section money (ROM total) and prototype-timeline conflicts.

    Accepts dic_sections rows (heading/content). Delegates to the shared
    tools.quality.content_grounding detector so DIC reports numeric conflicts
    alongside the KG concept-overlap detection above.
    Returns [{type, sections[], message, severity}].
    """
    return _grounding_numeric_claims(_normalize_sections(sections))


def check_version_consistency(version_id: str) -> dict:
    """Publish-gate report for a dic_versions version.

    Loads all sections of the version and returns:
      {placeholders: [{item_number, placeholders[]}],
       numeric_conflicts: [{type, sections[], message, severity}],
       section_count: int}
    Empty placeholders + numeric_conflicts means the gate passes. Errors are
    reported under "error" with empty findings so a DB hiccup never hard-fails
    an approve (the route decides whether to proceed).
    """
    sections: list[dict] = []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT section_id, heading, content FROM dic_sections "
                "WHERE version_id = %s ORDER BY created_at",
                (version_id,),
            ).fetchall()
            for r in rows:
                sections.append({
                    "section_id": _r(r, "section_id", 0),
                    "heading": _r(r, "heading", 1),
                    "content": _r(r, "content", 2),
                })
    except Exception as exc:
        logger.warning("consistency_checker.check_version_consistency error: %s", exc)
        return {"placeholders": [], "numeric_conflicts": [], "section_count": 0,
                "error": str(exc)}

    normalized = _normalize_sections(sections)
    return {
        "placeholders": _grounding_placeholder_findings(normalized),
        "numeric_conflicts": _grounding_numeric_claims(normalized),
        "section_count": len(sections),
    }


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


# --------------------------------------------------------------------------- #
# Citation publish gate (TRUST invariant)
# --------------------------------------------------------------------------- #

#: Section origins that make an AI-attributed claim and therefore must cite.
#: `human_authored` prose and `template` boilerplate assert nothing on the
#: model's behalf, so requiring `[source: ...]` of them would be noise.
AI_ORIGINS: frozenset[str] = frozenset({"ai_generated", "ai_regenerated", "ai_assisted"})


def _allowed_sources(citations_json: Any) -> set[str]:
    """Chunk ids that actually backed a section, from its stored citations.

    A section may only cite evidence that was recorded as retrieved for it; a
    `[source: chunk X]` naming anything else is a hallucinated citation, which
    is exactly what `validate_citations` is for.
    """
    if not citations_json:
        return set()
    try:
        data = json.loads(citations_json) if isinstance(citations_json, str) else citations_json
    except Exception:
        return set()
    if not isinstance(data, list):
        return set()
    out: set[str] = set()
    for c in data:
        if isinstance(c, dict):
            for key in ("chunk_id", "id", "source_id"):
                v = c.get(key)
                if v:
                    out.add(str(v))
                    break
        elif c:
            out.add(str(c))
    return out


def check_version_citations(version_id: str) -> dict:
    """Citation publish-gate report for a ``dic_versions`` version.

    Mirrors :func:`check_version_consistency` in shape and failure posture so the
    approve route can treat `citation_guard` and `placeholder_guard`
    symmetrically (CLAUDE.md TRUST invariant).

    Returns ``{findings: [{item_number, issue, detail}], ai_section_count,
    section_count}``. Empty ``findings`` means the gate passes. ``issue`` is
    ``missing_citations`` or ``hallucinated_citation``, produced by the shared
    ``tools.quality.citation_grounding.citation_gate`` — citation parsing and
    validation are NEVER re-implemented here.

    Errors are reported under ``error`` with empty findings, so a DB hiccup
    never hard-fails an approval; the route decides whether to proceed.
    """
    sections: list[dict] = []
    total = 0
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT section_id, heading, content, citations_json, origin "
                "FROM dic_sections WHERE version_id = %s ORDER BY created_at",
                (version_id,),
            ).fetchall()
            for r in rows:
                total += 1
                origin = (_r(r, "origin", 4) or "").strip().lower()
                if origin not in AI_ORIGINS:
                    continue
                content = _r(r, "content", 2) or ""
                if not content.strip():
                    continue
                sections.append({
                    "item_number": _r(r, "heading", 1) or _r(r, "section_id", 0),
                    "content": content,
                    "allowed_sources": _allowed_sources(_r(r, "citations_json", 3)),
                })
    except Exception as exc:
        logger.warning("dic citation gate: could not load sections for %s: %s", version_id, exc)
        return {"findings": [], "ai_section_count": 0, "section_count": 0, "error": str(exc)}

    try:
        from tools.quality.citation_grounding import citation_gate

        findings = citation_gate(sections, require_citations=True)
    except Exception as exc:
        logger.warning("dic citation gate: gate error for %s: %s", version_id, exc)
        return {"findings": [], "ai_section_count": len(sections),
                "section_count": total, "error": str(exc)}

    return {
        "findings": findings,
        "ai_section_count": len(sections),
        "section_count": total,
    }


def cove_enabled() -> bool:
    """Whether the CoVe publish gate is switched on for this deployment.

    OFF by default. CoVe multiplies LLM calls per artifact, so it is opted into
    deliberately rather than inherited.
    """
    import os

    from tools.document_intelligence.constants import DIC_COVE_GATE_ENV

    return os.environ.get(DIC_COVE_GATE_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def check_version_cove(version_id: str, *, force: bool = False) -> dict:
    """Chain-of-Verification publish gate for a ``dic_versions`` version.

    Third sibling of :func:`check_version_consistency` (placeholders) and
    :func:`check_version_citations` (citations), and deliberately the same
    shape so the approve route treats all three alike.

    Returns ``{enabled, blocked, findings, unrunnable, reason}``. ``findings``
    uses the shared ``{item_number, issue, detail}`` shape.

    Failure posture is the interesting part. ``cove_guard`` fails CLOSED: when
    the CoVe architecture raises — no provider reachable, budget exhausted — it
    reports ``blocked``. Correct for a connected deployment, wrong for an
    air-gapped one, where every approval would be blocked by a check that never
    actually ran. So a gate that RAN and found a defect always blocks, while a
    gate that could not run is governed by ``ICDEV_DIC_COVE_ON_ERROR``
    (``warn`` by default, ``block`` for deployments that want fail-closed).
    """
    import os

    from tools.document_intelligence.constants import (
        DIC_COVE_MAX_QUESTIONS_ENV,
        DIC_COVE_ON_ERROR_ENV,
    )

    if not cove_enabled():
        return {"enabled": False, "blocked": False, "findings": [],
                "unrunnable": False, "reason": "disabled"}

    # Reuse the citation gate's section loader: same population (AI-authored,
    # non-empty) and same evidence resolution. No second source of truth for
    # "which sections does a publish gate judge".
    sections: list[dict] = []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT section_id, heading, content, citations_json, origin "
                "FROM dic_sections WHERE version_id = %s ORDER BY created_at",
                (version_id,),
            ).fetchall()
            for r in rows:
                if (_r(r, "origin", 4) or "").strip().lower() not in AI_ORIGINS:
                    continue
                content = _r(r, "content", 2) or ""
                if content.strip():
                    sections.append({
                        "item_number": _r(r, "heading", 1) or _r(r, "section_id", 0),
                        "content": content,
                        "allowed_sources": _allowed_sources(_r(r, "citations_json", 3)),
                    })
    except Exception as exc:
        logger.warning("dic cove gate: could not load sections for %s: %s", version_id, exc)
        return {"enabled": True, "blocked": False, "findings": [],
                "unrunnable": True, "reason": f"load_failed: {exc}"}

    if not sections:
        return {"enabled": True, "blocked": False, "findings": [],
                "unrunnable": False, "reason": "no_ai_sections"}

    try:
        max_q = int(os.environ.get(DIC_COVE_MAX_QUESTIONS_ENV, "5"))
    except ValueError:
        max_q = 5
    on_error = (os.environ.get(DIC_COVE_ON_ERROR_ENV, "warn") or "warn").strip().lower()

    from tools.quality.cove_guard import cove_guard

    findings: list[dict] = []
    unrunnable = False
    reasons: list[str] = []
    for sec in sections:
        res = cove_guard(
            sec["content"],
            available_sources=sorted(sec["allowed_sources"]),
            force_override=force,
            max_questions=max_q,
        )
        if res.get("method") == "error":
            # The gate could not run. Do NOT report it as a content defect —
            # conflating "unverifiable" with "wrong" is how a broken provider
            # turns into a wall of false findings.
            unrunnable = True
            reasons.append(str((res.get("decision") or {}).get("error", "unknown")))
            continue
        if res.get("needs_revision"):
            findings.append({
                "item_number": sec["item_number"],
                "issue": "cove_contradicted",
                "detail": [c.get("question") or c.get("verdict", "")
                           for c in (res.get("contradicted_claims") or [])][:5],
            })

    blocked = bool(findings) and not force
    if unrunnable and not findings:
        blocked = (on_error == "block") and not force

    return {
        "enabled": True,
        "blocked": blocked,
        "findings": findings,
        "unrunnable": unrunnable,
        "reason": "; ".join(reasons[:3]) if reasons else ("defects" if findings else "clean"),
    }
