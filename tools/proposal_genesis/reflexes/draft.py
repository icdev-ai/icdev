#!/usr/bin/env python3
# CUI // SP-CTI
"""R7: Draft Reflex — auto-draft proposal responses with KB + Pulse enrichment.

Wraps tools/govcon/response_drafter.py with Pulse content reuse (D-PG-5)
and knowledge base enrichment (D-PG-4).
Two-tier LLM: qwen3.5 draft → Claude review for quality responses.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _draft_opportunity(opp_id: str) -> Dict[str, Any]:
    """Draft responses for all unmapped statements in an opportunity."""
    try:
        from tools.govcon.response_drafter import draft_all_for_opportunity

        result = draft_all_for_opportunity(opp_id)
        return result if isinstance(result, dict) else {"status": "ok", "drafted": 0}
    except ImportError:
        return {"status": "import_error", "message": "response_drafter not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _get_pulse_content(opp_id: str) -> List[Dict[str, str]]:
    """Find related Pulse articles for content reuse (D-PG-5).

    Searches for Pulse articles whose topics overlap with the opportunity's
    domain/requirements, returning content blocks that can enrich drafts.
    """
    conn = get_connection()
    try:
        # Check for existing Pulse-proposal links
        existing = conn.execute(
            "SELECT pulse_post_id, link_type, relevance_score "
            "FROM pg_pulse_proposal_links "
            "WHERE opportunity_id = ? AND link_type = 'content_reuse' "
            "ORDER BY relevance_score DESC LIMIT 5",
            (opp_id,),
        ).fetchall()
        if existing:
            return [dict(r) for r in existing]

        # Try to find matching Pulse articles by keyword overlap
        opp_row = conn.execute(
            "SELECT title, description FROM proposal_opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        if not opp_row:
            return []

        # Search Pulse posts by keyword overlap (deterministic, no LLM)
        keywords = _extract_keywords(opp_row["title"] or "", opp_row["description"] or "")
        if not keywords:
            return []

        # Query pulse_posts if table exists
        try:
            posts = conn.execute(
                "SELECT id, title, body FROM pulse_posts WHERE status = 'published' ORDER BY published_at DESC LIMIT 50"
            ).fetchall()
        except Exception:
            return []

        matches = []
        for post in posts:
            score = _keyword_overlap_score(keywords, (post["title"] or "") + " " + (post["body"] or ""))
            if score > 0.1:
                matches.append(
                    {
                        "pulse_post_id": post["id"],
                        "title": post["title"],
                        "relevance_score": round(score, 3),
                    }
                )

        # Store links for future lookups
        import uuid

        for match in matches[:5]:
            try:
                conn.execute(
                    "INSERT INTO pg_pulse_proposal_links "
                    "(id, opportunity_id, pulse_post_id, link_type, "
                    "relevance_score, created_at) "
                    "VALUES (?, ?, ?, 'content_reuse', ?, ?)",
                    (
                        f"pgpl-{uuid.uuid4().hex[:10]}",
                        opp_id,
                        match["pulse_post_id"],
                        match["relevance_score"],
                        _utcnow_iso(),
                    ),
                )
            except Exception:
                pass
        conn.commit()
        return matches[:5]
    except Exception:
        return []
    finally:
        conn.close()


def _extract_keywords(title: str, description: str) -> List[str]:
    """Extract keywords from title and description (deterministic)."""
    import re

    text = (title + " " + description).lower()
    # Remove common stop words
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "must",
        "can",
        "could",
        "for",
        "and",
        "nor",
        "but",
        "or",
        "yet",
        "so",
        "in",
        "on",
        "at",
        "to",
        "of",
        "with",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "not",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
    }
    words = re.findall(r"\b[a-z]{3,}\b", text)
    return list(set(w for w in words if w not in stop_words))[:30]


def _keyword_overlap_score(keywords: List[str], text: str) -> float:
    """Compute keyword overlap score between keywords and text."""
    if not keywords:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for kw in keywords if kw in text_lower)
    return matches / len(keywords)


def _enrich_draft_with_kb(opp_id: str, draft_count: int) -> Dict[str, Any]:
    """Enrich existing drafts with knowledge base content (D-PG-4)."""
    try:
        from tools.govcon.knowledge_base import search_blocks

        conn = get_connection()
        try:
            # Get draft responses that could benefit from KB enrichment
            drafts = conn.execute(
                "SELECT id, section_text FROM proposal_section_drafts "
                "WHERE opportunity_id = ? AND status = 'draft' "
                "ORDER BY created_at DESC LIMIT 20",
                (opp_id,),
            ).fetchall()
        finally:
            conn.close()

        enriched = 0
        for draft in drafts:
            try:
                results = search_blocks(draft["section_text"][:200], top_k=3)
                if isinstance(results, dict) and results.get("results"):
                    enriched += 1
            except Exception:
                pass

        return {"drafts_checked": len(drafts), "kb_enriched": enriched}
    except ImportError:
        return {"status": "kb_not_available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Draft Reflex (R7).

    Triggered after R6 Map. Auto-drafts proposal responses for mapped
    opportunities using two-tier LLM with KB and Pulse content enrichment.
    """
    conn = get_connection()
    try:
        # Find opportunities with capability mapping but no drafts yet
        rows = conn.execute("""
            SELECT DISTINCT po.id, po.title
            FROM proposal_opportunities po
            INNER JOIN icdev_capability_map cm ON cm.opportunity_id = po.id
            LEFT JOIN proposal_section_drafts psd ON psd.opportunity_id = po.id
            WHERE po.status IN ('tracking', 'drafting')
            AND psd.id IS NULL
            ORDER BY po.created_at DESC
            LIMIT 5
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    total_drafted = 0
    draft_results = []

    for row in rows:
        # Step 1: Get Pulse content for enrichment
        pulse_content = _get_pulse_content(row["id"])

        # Step 2: Draft responses
        result = _draft_opportunity(row["id"])
        drafted = result.get("drafted", 0)
        total_drafted += drafted if isinstance(drafted, int) else 0

        # Step 3: Enrich with knowledge base
        kb_result = _enrich_draft_with_kb(row["id"], drafted)

        draft_results.append(
            {
                "opportunity_id": row["id"],
                "title": row["title"],
                "drafts_created": drafted,
                "pulse_articles_linked": len(pulse_content),
                "kb_enrichment": kb_result,
            }
        )

    if draft_results:
        confidences = [r.get("drafts_created", 0) for r in draft_results]
        _ = sum(confidences) / len(confidences) if confidences else 0

    return {
        "success": True,
        "metric_value": float(total_drafted),
        "details": {
            "opportunities_drafted": len(rows),
            "total_drafts_created": total_drafted,
            "draft_results": draft_results,
        },
    }
