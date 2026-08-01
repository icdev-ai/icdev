#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Capability Enricher -- GraphRAG-enhanced capability mapping and draft enrichment.

Replaces simple keyword matching in R6 (Map) and R7 (Draft) with profile-aware
GraphRAG retrieval and parallel multi-strategy enrichment.  Falls back gracefully
to keyword matching when GraphRAG is unavailable (air-gap safe).

DB tables: pg_proposal_knowledge_base (read), pulse_posts (read),
           rag_chunks (read), audit_trail (write)

Usage:
    python tools/govcon/capability_enricher.py --opportunity-id "opp-xxx" --enrich-mapping --requirements '[...]' --json
    python tools/govcon/capability_enricher.py --opportunity-id "opp-xxx" --retrieve --query "FedRAMP moderate" --json
    python tools/govcon/capability_enricher.py --opportunity-id "opp-xxx" --enrich-section --section-topic "access control" --json
"""

import argparse
import hashlib
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# Optional imports -- graceful degradation
try:
    from tools.knowledge_graph.graph_rag import query_graph_rag

    HAS_GRAPH_RAG = True
except ImportError:
    HAS_GRAPH_RAG = False

try:
    from tools.rag.retriever import retrieve as rag_retrieve

    HAS_RAG = True
except ImportError:
    HAS_RAG = False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="enr"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details, opportunity_id=None):
    conn.execute(
        "INSERT INTO audit_trail (created_at, event_type, actor, action, details, project_id, session_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            _now(),
            event_type,
            "capability_enricher",
            action,
            json.dumps(details) if isinstance(details, dict) else str(details),
            opportunity_id,
            None,
        ),
    )


def _content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _select_profile(requirement_text):
    """Select GraphRAG profile based on requirement content."""
    text_lower = requirement_text.lower()
    compliance_kw = [
        "fedramp",
        "cmmc",
        "nist",
        "stig",
        "ato",
        "rmf",
        "fips",
        "fisma",
        "il4",
        "il5",
        "800-53",
        "800-171",
        "security control",
        "compliance",
        "authorization",
    ]
    exploratory_kw = ["competitor", "alternative", "market", "vendor", "incumbent", "landscape"]

    if any(kw in text_lower for kw in compliance_kw):
        return "compliance"
    if any(kw in text_lower for kw in exploratory_kw):
        return "exploratory"
    return "compliance"  # default for GovCon


# ---------------------------------------------------------------------------
# Source retrievers
# ---------------------------------------------------------------------------


def _retrieve_knowledge_base(query, limit=3):
    """Search proposal knowledge base by keyword."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, content, category FROM pg_proposal_knowledge_base "
            "WHERE content LIKE %s OR title LIKE %s ORDER BY created_at DESC LIMIT %s",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
    except Exception:
        rows = []
    conn.close()

    results = []
    for r in rows:
        results.append(
            {
                "source": "knowledge_base",
                "id": r["id"] if isinstance(r, dict) else r[0],
                "title": r["title"] if isinstance(r, dict) else r[1],
                "content": (r["content"] if isinstance(r, dict) else r[2])[:500],
                "relevance": 0.7,
            }
        )
    return results


def _retrieve_pulse(query, limit=3):
    """Search Pulse blog posts."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, body_markdown FROM pulse_posts "
            "WHERE body_markdown LIKE %s OR title LIKE %s ORDER BY created_at DESC LIMIT %s",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
    except Exception:
        rows = []
    conn.close()

    results = []
    for r in rows:
        results.append(
            {
                "source": "pulse",
                "id": r["id"] if isinstance(r, dict) else r[0],
                "title": r["title"] if isinstance(r, dict) else r[1],
                "content": (r["body_markdown"] if isinstance(r, dict) else r[2] or "")[:500],
                "relevance": 0.6,
            }
        )
    return results


def _retrieve_rag(query, limit=3):
    """Search RAG chunks if available."""
    if HAS_RAG:
        try:
            result = rag_retrieve(query=query, top_k=limit)
            return [
                {
                    "source": "rag",
                    "id": c.get("chunk_id", ""),
                    "title": "",
                    "content": c.get("text", "")[:500],
                    "relevance": c.get("score", 0.5),
                }
                for c in result.get("chunks", [])
            ]
        except Exception:
            pass

    # Fallback: direct DB query
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT chunk_id, content, source_table FROM rag_chunks WHERE content LIKE %s LIMIT %s",
            (f"%{query}%", limit),
        ).fetchall()
    except Exception:
        rows = []
    conn.close()

    return [
        {
            "source": "rag",
            "id": r[0] if isinstance(r, (tuple, list)) else r.get("chunk_id", ""),
            "title": "",
            "content": (r[1] if isinstance(r, (tuple, list)) else r.get("content", ""))[:500],
            "relevance": 0.5,
        }
        for r in rows
    ]


def _retrieve_graphrag(query, profile="compliance", limit=3):
    """Query GraphRAG with profile-aware scoring."""
    if HAS_GRAPH_RAG:
        try:
            result = query_graph_rag(query=query, profile=profile, top_k=limit)
            return [
                {
                    "source": "graphrag",
                    "id": n.get("entity_id", ""),
                    "title": n.get("label", ""),
                    "content": n.get("context", "")[:500],
                    "relevance": n.get("score", 0.8),
                }
                for n in result.get("nodes", [])
            ]
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def parallel_retrieve(query, project_id=None, sources=None):
    """Query multiple sources simultaneously via ThreadPoolExecutor."""
    if sources is None:
        sources = ["knowledge_base", "pulse", "rag", "graphrag"]

    profile = _select_profile(query)
    source_fns = {
        "knowledge_base": lambda: _retrieve_knowledge_base(query),
        "pulse": lambda: _retrieve_pulse(query),
        "rag": lambda: _retrieve_rag(query),
        "graphrag": lambda: _retrieve_graphrag(query, profile),
    }

    all_results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for src in sources:
            if src in source_fns:
                futures[executor.submit(source_fns[src])] = src

        for future in as_completed(futures, timeout=10):
            try:
                results = future.result(timeout=5)
                all_results.extend(results)
            except Exception:
                pass  # graceful degradation

    # Deduplicate by content hash
    seen = set()
    unique = []
    for r in sorted(all_results, key=lambda x: x.get("relevance", 0), reverse=True):
        h = _content_hash(r.get("content", ""))
        if h not in seen:
            seen.add(h)
            unique.append(r)

    return {
        "status": "ok",
        "query": query,
        "profile": profile,
        "results": unique,
        "total_results": len(unique),
        "sources_queried": sources,
    }


def compress_context(raw_results, max_chars=4000):
    """Deterministic context compression — sort by relevance, truncate to max_chars."""
    if not raw_results:
        return {"compressed_context": "", "sources": [], "char_count": 0}

    sorted_results = sorted(raw_results, key=lambda x: x.get("relevance", 0), reverse=True)

    parts = []
    sources = []
    total_chars = 0

    for r in sorted_results:
        content = r.get("content", "").strip()
        if not content:
            continue

        source_label = f"[{r.get('source', 'unknown')}:{r.get('id', 'N/A')[:8]}]"
        entry = f"{source_label} {content}"

        if total_chars + len(entry) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 50:
                parts.append(entry[:remaining] + "...")
                sources.append(r.get("source", "unknown"))
            break

        parts.append(entry)
        sources.append(r.get("source", "unknown"))
        total_chars += len(entry) + 1

    compressed = "\n".join(parts)
    return {
        "compressed_context": compressed,
        "sources": list(set(sources)),
        "char_count": len(compressed),
        "items_included": len(parts),
    }


def enrich_capability_mapping(opportunity_id, requirements):
    """Enrich capability mapping with GraphRAG + keyword hybrid scoring."""
    enriched = []

    for req in requirements:
        req_text = req if isinstance(req, str) else req.get("description", req.get("text", ""))

        # Parallel retrieval
        retrieval = parallel_retrieve(req_text, project_id=opportunity_id)
        results = retrieval.get("results", [])

        # GraphRAG score (if available)
        graphrag_results = [r for r in results if r.get("source") == "graphrag"]
        graphrag_score = max((r.get("relevance", 0) for r in graphrag_results), default=0)

        # Keyword score (simple overlap)
        keyword_score = 0.5  # baseline
        kw_matches = sum(1 for r in results if r.get("source") in ("knowledge_base", "pulse"))
        if kw_matches > 0:
            keyword_score = min(1.0, 0.5 + kw_matches * 0.1)

        # Hybrid: 0.6 graphrag + 0.4 keyword
        hybrid_score = (0.6 * graphrag_score + 0.4 * keyword_score) if graphrag_score > 0 else keyword_score

        context = compress_context(results)

        enriched.append(
            {
                "requirement": req_text[:200],
                "graphrag_score": round(graphrag_score, 3),
                "keyword_score": round(keyword_score, 3),
                "hybrid_score": round(hybrid_score, 3),
                "profile": retrieval.get("profile", "compliance"),
                "sources_found": len(results),
                "context_chars": context["char_count"],
            }
        )

    avg_score = sum(e["hybrid_score"] for e in enriched) / len(enriched) if enriched else 0

    conn = _get_db()
    _audit(
        conn,
        "capability.enrich",
        f"Enriched {len(enriched)} requirements",
        {"avg_score": round(avg_score, 3)},
        opportunity_id,
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "enriched_requirements": enriched,
        "avg_hybrid_score": round(avg_score, 3),
        "graphrag_available": HAS_GRAPH_RAG,
        "rag_available": HAS_RAG,
    }


def enrich_draft_section(opportunity_id, section_topic, section_text=None):
    """Full enrichment pipeline for a draft section."""
    retrieval = parallel_retrieve(section_topic, project_id=opportunity_id)
    context = compress_context(retrieval.get("results", []))

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "section_topic": section_topic,
        "enriched_context": context["compressed_context"],
        "sources": context["sources"],
        "items_included": context["items_included"],
        "char_count": context["char_count"],
        "profile": retrieval.get("profile", "compliance"),
        "total_retrieved": retrieval.get("total_results", 0),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Capability Enricher — GraphRAG-enhanced capability mapping")
    parser.add_argument("--opportunity-id", required=True)

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--enrich-mapping", action="store_true", help="Enrich capability mapping for requirements")
    group.add_argument("--retrieve", action="store_true", help="Parallel multi-source retrieval")
    group.add_argument("--enrich-section", action="store_true", help="Enrich a draft section")

    parser.add_argument("--requirements", help="JSON array of requirement texts")
    parser.add_argument("--query", help="Search query for retrieval")
    parser.add_argument("--section-topic", help="Section topic for draft enrichment")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--human", action="store_true")
    args = parser.parse_args()

    result = {}

    if args.enrich_mapping:
        reqs = json.loads(args.requirements) if args.requirements else []
        result = enrich_capability_mapping(args.opportunity_id, reqs)

    elif args.retrieve:
        if not args.query:
            result = {"status": "error", "message": "Provide --query"}
        else:
            result = parallel_retrieve(args.query, project_id=args.opportunity_id)

    elif args.enrich_section:
        if not args.section_topic:
            result = {"status": "error", "message": "Provide --section-topic"}
        else:
            result = enrich_draft_section(args.opportunity_id, args.section_topic)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
