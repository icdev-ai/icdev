#!/usr/bin/env python3
# CUI // SP-CTI
"""War Council Brief Generator — assembles full strategic assessment briefs.

Combines:
  1. Clausewitz CoG doctrine passages (doctrine_corpus)
  2. Scenario-matched doctrine citations (doctrine_corpus)
  3. Historical precedent quotes (doctrine_corpus)
  4. LLM COA narratives grounded in retrieved doctrine (strategy_agent + RAG)

The resulting brief is rendered via the war_council.md.j2 Jinja2 template and
optionally persisted to sg_intelligence_briefs.

Usage:
    from tools.strategos.war_council_generator import generate_war_council_brief
    result = generate_war_council_brief(
        theater="taiwan",
        scenario="PLA ADIZ incursion preceded by amphibious buildup",
    )
    print(result["content_md"])

CLI:
    python tools/strategos/war_council_generator.py \\
        --theater taiwan \\
        --scenario "PLA ADIZ incursion" \\
        --json
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.war_council_generator")

_CLASSIFICATION = "CUI // SP-CTI"

# CoG fallback analysis when theater-specific context is unavailable
_COG_ANALYSIS_TEMPLATE = (
    "In the {theater} theater, the adversary's center of gravity is assessed as "
    "their ability to project sustained force while maintaining political cohesion. "
    "The friendly CoG centers on C2 integrity, coalition cohesion, and sea/air "
    "dominance within the operational area."
)

_FRIENDLY_COG_DEFAULT = "Command-and-control network + allied force integration"
_ADVERSARY_COG_DEFAULT = "Integrated strike capability + A2/AD umbrella"
_FRIENDLY_CC_DEFAULT = "ISR fusion + joint fires"
_ADVERSARY_CC_DEFAULT = "Long-range precision strike and denial"
_FRIENDLY_CV_DEFAULT = "Logistics sustainment nodes under A2/AD threat"
_ADVERSARY_CV_DEFAULT = "C2 relay infrastructure and logistics depots"


def generate_war_council_brief(
    theater: str,
    scenario: str,
    commander_intent: str = "",
    pmesii_summary: str = "",
    constraints: list[str] | None = None,
    pmesii_vector: list[float] | None = None,
    friendly_cog: str = "",
    adversary_cog: str = "",
    save: bool = True,
) -> dict[str, Any]:
    """Generate a War Council brief grounded in doctrine citations and RAG-enriched COAs.

    Args:
        theater:          Theater / AOR label (e.g. "taiwan", "ukraine", "middle east").
        scenario:         Scenario description used for doctrine retrieval and COA generation.
        commander_intent: Optional commander's intent statement.
        pmesii_summary:   Optional PMESII-PT assessment summary.
        constraints:      Optional list of planning constraints.
        pmesii_vector:    Optional 7-element PMESII-PT vector [P,M,E,S,I,I2,PT] 0–1.
        friendly_cog:     Override for friendly center of gravity label.
        adversary_cog:    Override for adversary center of gravity label.
        save:             If True, persist the brief to sg_intelligence_briefs.

    Returns:
        dict with keys:
            brief_id                — UUID (from DB if saved, generated otherwise)
            content_md              — rendered Markdown brief
            rag_active              — True when RAG enrichment retrieved at least one doc
            rag_doc_count           — number of RAG documents retrieved
            doctrine_citations_count — doctrine entries retrieved from corpus
            historical_precedents_count — historical cases retrieved from corpus
            coa_count               — number of COAs generated
            latency_ms              — total generation latency in ms
            theater, generated_at
    """
    from tools.strategos.doctrine_corpus import (
        get_cog_passage,
        get_doctrine_citations,
        get_historical_precedents,
    )
    from tools.strategos.strategy_agent import get_coas
    from tools.intelligence.brief_generator import BriefGenerator

    t0 = time.monotonic()
    brief_id = str(uuid.uuid4())
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    # ── Step 1: Retrieve CoG doctrine passage ─────────────────────────────
    cog_entry = get_cog_passage(theater=theater)

    # ── Step 2: Retrieve doctrine citations (non-historical) ──────────────
    doctrine_entries = get_doctrine_citations(scenario=scenario, theater=theater, top_k=3)

    # ── Step 3: Retrieve historical precedents ────────────────────────────
    precedent_entries = get_historical_precedents(scenario=scenario, theater=theater, top_k=2)

    # ── Step 4: Generate RAG-enriched COAs ───────────────────────────────
    # doctrine_corpus entries are pre-seeded into sg_corpus_documents via seed_corpus().
    # strategy_agent._enrich_with_rag() pulls from there + sg_conflict_events.
    coa_result = get_coas(
        scenario=scenario,
        theater=theater,
        commander_intent=commander_intent,
        constraints=constraints,
        pmesii_vector=pmesii_vector,
    )

    # ── Step 5: Enrich COAs with doctrine_basis and historical_analogy ────
    # If Ollama populated these fields, keep them; otherwise inject from corpus.
    coa_list_dicts = []
    for i, coa in enumerate(coa_result.courses_of_action):
        coa_d = coa.to_dict()

        # Assign doctrine citation when LLM left the field blank
        if not coa_d.get("doctrine_basis") and doctrine_entries:
            entry = doctrine_entries[i % len(doctrine_entries)]
            coa_d["doctrine_basis"] = entry.citation_line()

        # Assign historical analogy when LLM left the field blank
        if not coa_d.get("historical_analogy") and i < len(precedent_entries):
            coa_d["historical_analogy"] = precedent_entries[i].title

        coa_list_dicts.append(coa_d)

    # ── Step 6: Assemble template context ────────────────────────────────
    ctx: dict[str, Any] = {
        "brief_id": brief_id,
        "dtg": generated_at,
        "generated_at": generated_at,
        "theater": theater,
        "classification": _CLASSIFICATION,
        "commander_intent": commander_intent,
        "scenario": scenario,
        "pmesii_summary": pmesii_summary,
        # CoG analysis
        "cog_passage": cog_entry.content,
        "cog_analysis": _COG_ANALYSIS_TEMPLATE.format(theater=theater),
        "friendly_cog": friendly_cog or _FRIENDLY_COG_DEFAULT,
        "adversary_cog": adversary_cog or _ADVERSARY_COG_DEFAULT,
        "friendly_cc": _FRIENDLY_CC_DEFAULT,
        "adversary_cc": _ADVERSARY_CC_DEFAULT,
        "friendly_cv": _FRIENDLY_CV_DEFAULT,
        "adversary_cv": _ADVERSARY_CV_DEFAULT,
        # Doctrine citations
        "doctrine_citations": [
            {"title": e.title, "content": e.content} for e in doctrine_entries
        ],
        # COAs
        "courses_of_action": coa_list_dicts,
        "recommended_coa_index": coa_result.recommended_coa_index,
        # Historical precedents
        "historical_precedents": [
            {"title": e.title, "content": e.content} for e in precedent_entries
        ],
        # RAG intelligence context (from strategy_agent retrieval)
        "rag_context": coa_result.courses_of_action[0].description
        if coa_result.rag_active and coa_result.courses_of_action
        else "",
        "rag_active": coa_result.rag_active,
        "rag_doc_count": coa_result.rag_doc_count,
        "model_used": coa_result.model_used,
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }

    # ── Step 7: Render via BriefGenerator ────────────────────────────────
    gen = BriefGenerator()
    content_md = gen.generate("war_council", ctx)

    # ── Step 8: Persist to sg_intelligence_briefs ─────────────────────────
    if save:
        try:
            saved = gen.save(
                brief_type="war_council",
                title=f"War Council — {theater.upper()} — {generated_at}",
                content_md=content_md,
                sio_confidence=None,
            )
            brief_id = saved["id"]
        except Exception as exc:
            logger.warning("Failed to persist war council brief: %s", exc)

    latency_ms = int((time.monotonic() - t0) * 1000)

    return {
        "brief_id": brief_id,
        "content_md": content_md,
        "rag_active": coa_result.rag_active,
        "rag_doc_count": coa_result.rag_doc_count,
        "doctrine_citations_count": len(doctrine_entries),
        "historical_precedents_count": len(precedent_entries),
        "coa_count": len(coa_result.courses_of_action),
        "latency_ms": latency_ms,
        "theater": theater,
        "generated_at": generated_at,
        "classification": _CLASSIFICATION,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="War Council Brief Generator — doctrine + RAG-enriched COAs"
    )
    parser.add_argument("--theater", required=True, help="Theater / AOR (e.g. taiwan, ukraine)")
    parser.add_argument("--scenario", required=True, help="Scenario description")
    parser.add_argument("--intent", default="", dest="commander_intent",
                        help="Commander's intent")
    parser.add_argument("--pmesii", default="", dest="pmesii_summary",
                        help="PMESII-PT summary")
    parser.add_argument("--no-save", action="store_true", help="Do not persist to DB")
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Output JSON summary instead of Markdown brief")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    result = generate_war_council_brief(
        theater=args.theater,
        scenario=args.scenario,
        commander_intent=args.commander_intent,
        pmesii_summary=args.pmesii_summary,
        save=not args.no_save,
    )

    if args.json_out:
        summary = {k: v for k, v in result.items() if k != "content_md"}
        print(json.dumps(summary, indent=2))
    else:
        print(result["content_md"])


if __name__ == "__main__":
    _main()
