#!/usr/bin/env python3
# CUI // SP-CTI
"""R7: Draft Reflex — auto-draft proposal responses with KB + Pulse enrichment.

Wraps tools/govcon/response_drafter.py with Pulse content reuse (D-PG-5)
and knowledge base enrichment (D-PG-4).
Two-tier LLM: qwen3.5 draft → Claude review for quality responses.

Per aiify-opp-5401 the Pulse content-reuse matcher gained an optional NLP
keyword extractor (``nlp_extractor`` paradigm). The deterministic
regex+stopword ``_extract_keywords`` remains the authoritative fallback; when
``ai_keywords=True`` is set on the reflex config the matcher first tries an
LLM-based semantic extraction (``_ai_extract_keywords``) and silently falls
back to the regex path on any failure, so Pulse matching never depends on LLM
availability. See ``_ai_extract_keywords``.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level fallback constant — overridable from proposal_genesis_config.yaml
# under reflexes.draft.anomaly_detection. Change config, not code.
# ---------------------------------------------------------------------------
_PULSE_RELEVANCE_THRESHOLD = 0.1  # keyword-overlap score >= this → store as a Pulse match


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5400): adaptive Pulse-relevance threshold.
#
# The Pulse content-reuse matcher previously hardcoded a 0.1 keyword-overlap
# cutoff for what counts as a "match" (the flagged ``hardcoded_threshold``
# site). A fixed cutoff is blind to the actual distribution of match quality:
# as the Pulse corpus and opportunity mix shift, 0.1 may admit noise or reject
# genuine matches. The ``anomaly_detection`` paradigm computes the cutoff from
# the historical distribution of stored relevance scores so the matcher
# adaptively filters the weakest matches relative to what it has seen, while a
# hard floor keeps it from ever dropping below the proven 0.1 baseline.
#
# Mirrors the canonical adaptive-threshold pattern in reflexes/shape.py
# (``_compute_fit_thresholds``): PERCENTILE_CONT on PostgreSQL, a mean-based
# proxy on SQLite, and a static fallback when fewer than ``min_samples`` rows
# exist or anomaly detection is disabled.
# ---------------------------------------------------------------------------


def _compute_pulse_relevance_threshold(anomaly_cfg: "dict | None" = None) -> float:
    """Compute the adaptive Pulse-match relevance threshold.

    Uses the distribution of ``relevance_score`` in ``pg_pulse_proposal_links``
    (``link_type = 'content_reuse'``) and sets the cutoff at the bottom quartile
    (P25) of observed scores — adaptively dropping the weakest matches relative
    to the historical corpus. A hard floor keeps the cutoff at or above the
    proven static baseline.

    Falls back to the static ``_PULSE_RELEVANCE_THRESHOLD`` when anomaly
    detection is disabled or fewer than ``min_samples`` historical rows exist.
    """
    cfg = anomaly_cfg or {}
    fallback = float(cfg.get("fallback_relevance_threshold", _PULSE_RELEVANCE_THRESHOLD))
    if not cfg.get("enabled", True):
        return fallback

    min_samples = cfg.get("min_samples", 15)
    floor = float(cfg.get("adaptive_bounds", {}).get("relevance_floor", _PULSE_RELEVANCE_THRESHOLD))

    conn = None
    try:
        conn = get_connection()
        try:
            # PostgreSQL: true P25 of the stored relevance distribution.
            row = conn.execute(
                "SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY relevance_score) AS p25, "
                "COUNT(*) AS n FROM pg_pulse_proposal_links "
                "WHERE link_type = 'content_reuse' AND relevance_score IS NOT NULL"
            ).fetchone()
            if row:
                n = row["n"] if isinstance(row, dict) else row[1]
                if n and n >= min_samples:
                    p25 = float(row["p25"] if isinstance(row, dict) else row[0])
                    return max(floor, round(p25, 3))
        except Exception:
            # SQLite fallback: mean minus a fraction of the spread (mean ≈ P50).
            row = conn.execute(
                "SELECT AVG(relevance_score) AS mean_r, COUNT(*) AS n "
                "FROM pg_pulse_proposal_links "
                "WHERE link_type = 'content_reuse' AND relevance_score IS NOT NULL"
            ).fetchone()
            if row:
                n = row["n"] if isinstance(row, dict) else row[1]
                if n and n >= min_samples:
                    mean_r = float(row["mean_r"] if isinstance(row, dict) else row[0])
                    return max(floor, round(mean_r * 0.5, 3))
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return fallback


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


def _get_pulse_content(
    opp_id: str,
    ai_keywords: bool = False,
    relevance_threshold: float = _PULSE_RELEVANCE_THRESHOLD,
) -> List[Dict[str, str]]:
    """Find related Pulse articles for content reuse (D-PG-5).

    Searches for Pulse articles whose topics overlap with the opportunity's
    domain/requirements, returning content blocks that can enrich drafts.

    When ``ai_keywords`` is True the matcher first attempts LLM-based semantic
    keyword extraction (``_ai_extract_keywords``), falling back to the
    deterministic regex extractor on any failure.

    ``relevance_threshold`` is the minimum keyword-overlap score for a Pulse
    article to count as a match. It defaults to the static baseline but is
    supplied by ``run()`` from the adaptive ``anomaly_detection`` computation
    (aiify-opp-5400, see ``_compute_pulse_relevance_threshold``).
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

        # Extract keywords for Pulse overlap. Prefer the opt-in NLP extractor
        # (aiify-opp-5401); fall back to the deterministic regex extractor when
        # disabled or when the LLM is unavailable.
        keywords = None
        if ai_keywords:
            keywords = _ai_extract_keywords(opp_row["title"] or "", opp_row["description"] or "")
        if not keywords:
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
            if score >= relevance_threshold:
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


# ---------------------------------------------------------------------------
# AI-ification (aiify-opp-5401): optional NLP keyword extractor.
#
# ``_extract_keywords`` above is a deterministic regex + stopword filter. It
# treats every >=3-char non-stopword token as a keyword, so it misses
# multi-word domain entities ("zero trust", "C5ISR"), keeps low-signal tokens,
# and cannot weight terms by relevance. When the reflex config opts in via
# ``ai_keywords=True`` we ADDITIONALLY try an LLM extraction that returns the
# salient domain keywords/entities for Pulse content matching.
#
# The input here is USER-PROVIDED opportunity text (the flagged
# ``regex_user_input`` site), so unlike trusted first-party narrative calls we
# DO NOT set ``skip_injection_scan`` — the router's prompt-injection scan stays
# on. Any failure (no-LLM mode, air-gap, malformed output) degrades silently to
# ``None`` and the caller falls back to the deterministic ``_extract_keywords``.
# ---------------------------------------------------------------------------

_KEYWORD_EXTRACTION_SYSTEM_PROMPT = (
    "You are a GovCon proposal analyst extracting search keywords from a "
    "contract opportunity. Return ONLY the most salient domain keywords and "
    "named entities (technologies, agencies, capabilities, mission domains) "
    "that would identify related marketing content. Prefer specific multi-word "
    "domain terms over generic words. Use only terms grounded in the provided "
    "text — never invent. Output a single line of 5-15 lowercase keywords "
    "separated by commas, nothing else: no numbering, no prose, no markdown."
)


def _parse_keyword_response(content: str) -> List[str]:
    """Parse an LLM keyword response into a clean, deduped keyword list.

    Accepts comma- or newline-separated output and tolerates light decoration
    (leading bullets/numbers). Returns at most 30 keywords to mirror the
    deterministic extractor's cap.
    """
    import re

    raw_parts = re.split(r"[,\n]", content or "")
    seen: set = set()
    keywords: List[str] = []
    for part in raw_parts:
        kw = re.sub(r"^[\s\-*0-9.)]+", "", part).strip().lower()
        if 3 <= len(kw) <= 60 and kw not in seen:
            seen.add(kw)
            keywords.append(kw)
    return keywords[:30]


def _ai_extract_keywords(title: str, description: str) -> Optional[List[str]]:
    """LLM-based semantic keyword extraction for Pulse content matching.

    Args:
        title: Opportunity title (user-provided text).
        description: Opportunity description (user-provided text).

    Returns:
        A list of salient keywords, or ``None`` if extraction is unavailable or
        fails for any reason. Callers MUST treat ``None`` as "no AI keywords"
        and fall back to the deterministic ``_extract_keywords``.
    """
    text = (title or "").strip()
    if description:
        text = f"{text}\n{description.strip()}"
    if not text:
        return None
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        req = LLMRequest(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Opportunity title and description:\n"
                        f"{text}\n\n"
                        "Extract the search keywords."
                    ),
                }
            ],
            system_prompt=_KEYWORD_EXTRACTION_SYSTEM_PROMPT,
            max_tokens=256,
            temperature=0.0,
            classification="CUI",
        )
        resp = LLMRouter().invoke("pg_keyword_extraction", req)
        if resp and resp.content:
            keywords = _parse_keyword_response(resp.content)
            return keywords or None
    except Exception:
        pass  # Graceful degradation — deterministic _extract_keywords is authoritative.
    return None


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

    Set ``config["ai_keywords"] = True`` to enable NLP keyword extraction for
    Pulse content matching (aiify-opp-5401); defaults to the deterministic
    regex extractor.

    The Pulse-match relevance cutoff is computed adaptively from the historical
    score distribution via ``config["anomaly_detection"]`` (aiify-opp-5400),
    falling back to the static baseline when disabled or under-sampled.
    """
    cfg = config or {}
    ai_keywords = bool(cfg.get("ai_keywords", False))
    relevance_threshold = _compute_pulse_relevance_threshold(cfg.get("anomaly_detection", {}))
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
        pulse_content = _get_pulse_content(
            row["id"], ai_keywords=ai_keywords, relevance_threshold=relevance_threshold
        )

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
