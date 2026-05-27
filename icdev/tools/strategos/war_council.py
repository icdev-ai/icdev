#!/usr/bin/env python3
# CUI // SP-CTI
"""War Council Engine — doctrine-grounded War Council strategic briefs (sg-rag-05).

Upgrades the sg-iw-10 War Council stub with:
  1. Clausewitz CoG passage from built-in doctrine corpus (offline-safe)
  2. Doctrine citations (FM 3-0, JP 3-0, etc.) scored by theater + scenario
  3. Historical precedent quotes scored by theater + keyword relevance
  4. LLM COA narratives enriched with retrieved doctrine context

Doctrine corpus is available without DB access; RAG via StrategyAgent adds
live sg_corpus_documents results when the DB is seeded.

Usage:
    from tools.strategos.war_council import WarCouncilEngine, WarCouncilRequest
    engine = WarCouncilEngine()
    brief = engine.generate(WarCouncilRequest(
        scenario="PLA amphibious assault on Taiwan",
        theater="taiwan",
    ))
    print(brief.content_md)

CLI:
    python tools/strategos/war_council.py --scenario "..." --theater taiwan --json
    python tools/strategos/war_council.py --scenario "..." --markdown
    python tools/strategos/war_council.py --scenario "..." --save
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, Undefined

from tools.strategos.doctrine_corpus import (
    DoctrineEntry,
    get_cog_passage,
    get_doctrine_citations,
    get_historical_precedents,
)
from tools.strategos.strategy_agent import (
    CourseOfAction,
    ScenarioContext,
    StrategyAgent,
    StrategyResult,
)

logger = get_logger("icdev.strategos.war_council")

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "tools" / "intelligence" / "templates"


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class WarCouncilRequest:
    """Input for the War Council Engine."""
    scenario: str
    theater: str = "unspecified"
    commander_intent: str = ""
    constraints: list[str] = field(default_factory=list)
    pmesii_vector: list[float] = field(default_factory=list)
    pmesii_summary: str = ""
    friendly_cog: str = ""
    friendly_cc: str = ""
    friendly_cv: str = ""
    adversary_cog: str = ""
    adversary_cc: str = ""
    adversary_cv: str = ""
    classification: str = "CUI // SP-CTI"
    prepared_by: str = "ICDEV Strategos / War Council Engine"
    save_to_db: bool = False


@dataclass
class WarCouncilBrief:
    """Rendered War Council brief."""
    brief_id: str
    content_md: str
    theater: str
    doctrine_citations: list[DoctrineEntry]
    historical_precedents: list[DoctrineEntry]
    strategy_result: StrategyResult
    generated_at: str
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        coas = self.strategy_result.courses_of_action
        return {
            "brief_id": self.brief_id,
            "theater": self.theater,
            "content_md": self.content_md,
            "content": self.content_md,          # alias for template compatibility
            "generated_at": self.generated_at,
            "error": self.error,
            # Full arrays for UI rendering
            "doctrine_citations": [
                {"reference": d.citation_line(), "excerpt": d.content[:300]}
                for d in self.doctrine_citations
            ],
            "historical_precedents": [
                {"title": h.title, "summary": h.content[:300]}
                for h in self.historical_precedents
            ],
            "courses_of_action": [
                {
                    "title": c.name,
                    "description": c.description,
                    "risk_level": c.risk_level,
                    "feasibility": c.feasibility,
                    "doctrine_basis": c.doctrine_basis,
                    "historical_analogy": c.historical_analogy,
                    "composite_score": c.composite_score,
                }
                for c in coas
            ],
            # Summary counts
            "doctrine_citations_count": len(self.doctrine_citations),
            "historical_precedents_count": len(self.historical_precedents),
            "coa_count": len(coas),
            "recommended_coa": coas[self.strategy_result.recommended_coa_index].name if coas else "",
            "rag_active": self.strategy_result.rag_active,
            "rag_doc_count": self.strategy_result.rag_doc_count,
            "model_used": self.strategy_result.model_used,
            "latency_ms": self.strategy_result.latency_ms,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ── Jinja2 env ─────────────────────────────────────────────────────────────────

class _LenientUndefined(Undefined):
    def __str__(self) -> str:
        return ""

    def __iter__(self):
        return iter([])

    def __bool__(self) -> bool:
        return False


def _make_env() -> Environment:
    return Environment(  # nosec B701 — templates are .md.j2 Markdown
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=_LenientUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


# ── War Council Engine ─────────────────────────────────────────────────────────

class WarCouncilEngine:
    """Generates doctrine-grounded War Council briefs.

    Step 1 — Doctrine retrieval from built-in corpus (offline-safe):
      - get_cog_passage() — Clausewitz CoG passage
      - get_doctrine_citations() — FM/JP doctrine scored by theater + scenario
      - get_historical_precedents() — historical cases scored by theater + keywords

    Step 2 — COA generation via StrategyAgent (RAG-enabled):
      - ScenarioContext.rag_context=None → StrategyAgent runs live RAG enrichment
      - Falls back to deterministic stub COAs if Ollama is unavailable

    Step 3 — COA enrichment:
      - Assigns doctrine_basis and historical_analogy to each COA dict

    Step 4 — Render war_council.md.j2 template with full doctrine + COA context
    """

    def __init__(self) -> None:
        self._env = _make_env()
        self._strategy_agent = StrategyAgent()

    def generate(self, req: WarCouncilRequest) -> WarCouncilBrief:
        brief_id = str(uuid.uuid4())
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        dtg = datetime.now(timezone.utc).strftime("%d%H%MZ %b %Y").upper()

        # Step 1 — Fetch doctrine + history from built-in offline corpus
        cog_passage = get_cog_passage(theater=req.theater)
        doctrine_citations = get_doctrine_citations(
            scenario=req.scenario,
            theater=req.theater,
            top_k=3,
        )
        historical_precedents = get_historical_precedents(
            scenario=req.scenario,
            theater=req.theater,
            top_k=2,
        )

        # Step 2 — Generate COAs via StrategyAgent; let it run live RAG enrichment
        # (rag_context=None triggers _enrich_with_rag inside generate_coas)
        scenario_ctx = ScenarioContext(
            scenario=req.scenario,
            theater=req.theater,
            commander_intent=req.commander_intent,
            constraints=req.constraints,
            pmesii_vector=req.pmesii_vector,
            rag_context=None,
        )
        strategy_result = self._strategy_agent.generate_coas(scenario_ctx)

        # Step 3 — Enrich COA dicts with doctrine_basis and historical_analogy
        coa_dicts = _enrich_coas(
            strategy_result.courses_of_action,
            doctrine_citations,
            historical_precedents,
        )

        # Step 4 — Build template context
        template_ctx: dict[str, Any] = {
            "brief_id": brief_id,
            "generated_at": generated_at,
            "dtg": dtg,
            "theater": req.theater,
            "prepared_by": req.prepared_by,
            "classification": req.classification,
            "commander_intent": req.commander_intent,
            "scenario": req.scenario,
            "pmesii_summary": req.pmesii_summary,
            # CoG analysis
            "cog_passage": cog_passage.content,
            "cog_analysis": _build_cog_analysis(req, strategy_result),
            "friendly_cog": req.friendly_cog,
            "friendly_cc": req.friendly_cc,
            "friendly_cv": req.friendly_cv,
            "adversary_cog": req.adversary_cog,
            "adversary_cc": req.adversary_cc,
            "adversary_cv": req.adversary_cv,
            # Doctrine citations from offline corpus
            "doctrine_citations": [
                {"title": d.title, "content": d.content}
                for d in doctrine_citations
            ],
            # COAs with doctrine/history enrichment
            "courses_of_action": coa_dicts,
            "recommended_coa_index": strategy_result.recommended_coa_index,
            # Historical precedents from offline corpus
            "historical_precedents": [
                {"title": h.title, "content": h.content}
                for h in historical_precedents
            ],
            # RAG intelligence context from StrategyAgent live enrichment
            "rag_context": scenario_ctx.rag_context or "",
            "rag_active": strategy_result.rag_active,
            "rag_doc_count": strategy_result.rag_doc_count,
            # Meta
            "model_used": strategy_result.model_used,
            "latency_ms": strategy_result.latency_ms,
        }

        try:
            template = self._env.get_template("war_council.md.j2")
            content_md = template.render(**template_ctx)
            error = ""
        except Exception as exc:
            logger.error("Template render failed: %s", exc)
            content_md = f"# WAR COUNCIL BRIEF\n\nTemplate render error: {exc}"
            error = str(exc)

        if req.save_to_db and not error:
            _save_brief(brief_id, req.theater, req.scenario, content_md, strategy_result)

        return WarCouncilBrief(
            brief_id=brief_id,
            content_md=content_md,
            theater=req.theater,
            doctrine_citations=doctrine_citations,
            historical_precedents=historical_precedents,
            strategy_result=strategy_result,
            generated_at=generated_at,
            error=error,
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _enrich_coas(
    coas: list[CourseOfAction],
    doctrine_citations: list[DoctrineEntry],
    historical_precedents: list[DoctrineEntry],
) -> list[dict[str, Any]]:
    """Convert COAs to dicts and assign doctrine_basis + historical_analogy."""
    result: list[dict[str, Any]] = []
    for i, coa in enumerate(coas):
        d = coa.to_dict()
        if doctrine_citations:
            dc = doctrine_citations[i % len(doctrine_citations)]
            d["doctrine_basis"] = dc.citation_line()
        else:
            d["doctrine_basis"] = ""
        if historical_precedents:
            hp = historical_precedents[i % len(historical_precedents)]
            d["historical_analogy"] = f"{hp.title}: {hp.content[:200]}..."
        else:
            d["historical_analogy"] = ""
        result.append(d)
    return result


def _build_cog_analysis(req: WarCouncilRequest, result: StrategyResult) -> str:
    """Build a brief CoG analysis string from request context and recommended COA."""
    parts: list[str] = []
    if req.adversary_cog:
        parts.append(f"Adversary CoG identified as {req.adversary_cog}.")
    if req.friendly_cog:
        parts.append(f"Friendly CoG is {req.friendly_cog}.")
    if result.courses_of_action:
        rec = result.courses_of_action[result.recommended_coa_index]
        parts.append(
            f"Recommended approach ({rec.name}) targets CoG with"
            f" {rec.risk_level.lower()} risk posture."
        )
    return " ".join(parts) if parts else ""


def _save_brief(
    brief_id: str,
    theater: str,
    scenario: str,
    content_md: str,
    result: StrategyResult,
) -> None:
    """Persist to sg_intelligence_briefs_war_council if present, else sg_intelligence_briefs."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                """INSERT INTO sg_intelligence_briefs_war_council
                   (id, theater, scenario_summary, content_md,
                    rag_active, rag_doc_count, model_used, latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    brief_id,
                    theater,
                    scenario[:200],
                    content_md,
                    int(result.rag_active),
                    result.rag_doc_count,
                    result.model_used,
                    result.latency_ms,
                    now,
                ),
            )
            conn.commit()
        except Exception:
            conn.execute(
                """INSERT OR IGNORE INTO sg_intelligence_briefs
                   (id, brief_type, title, content_md, sio_confidence,
                    analyst_reviewed, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (
                    brief_id,
                    "war_council",
                    f"War Council — {theater}",
                    content_md,
                    None,
                    now,
                ),
            )
            conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Failed to save brief to DB: %s", exc)


# ── Read path ─────────────────────────────────────────────────────────────────

def get_war_council_briefs(
    theater: str | None = None,
    limit: int = 20,
    conn=None,
) -> list[dict]:
    """Return recent war council briefs, optionally filtered by theater."""
    from tools.db.storage import get_connection as _gc

    _conn = conn or _gc()
    try:
        sql = "SELECT * FROM sg_intelligence_briefs_war_council"
        params: list = []
        if theater:
            sql += " WHERE theater = ?"
            params.append(theater)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur = _conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
    finally:
        if conn is None:
            _conn.close()


# ── Convenience wrapper ────────────────────────────────────────────────────────

def generate_war_council_brief(
    scenario: str,
    theater: str = "unspecified",
    commander_intent: str = "",
    pmesii_summary: str = "",
    adversary_cog: str = "",
    friendly_cog: str = "",
    save_to_db: bool = False,
) -> WarCouncilBrief:
    """Convenience wrapper — instantiates WarCouncilEngine and returns brief."""
    req = WarCouncilRequest(
        scenario=scenario,
        theater=theater,
        commander_intent=commander_intent,
        pmesii_summary=pmesii_summary,
        adversary_cog=adversary_cog,
        friendly_cog=friendly_cog,
        save_to_db=save_to_db,
    )
    return WarCouncilEngine().generate(req)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="War Council Engine — doctrine-grounded strategic briefs"
    )
    parser.add_argument("--scenario", required=True, help="Scenario description text")
    parser.add_argument("--theater", default="unspecified", help="Theater / AOR label")
    parser.add_argument("--intent", default="", help="Commander's intent")
    parser.add_argument("--pmesii-summary", default="", help="PMESII-PT assessment summary")
    parser.add_argument("--adversary-cog", default="", help="Adversary center of gravity")
    parser.add_argument("--friendly-cog", default="", help="Friendly center of gravity")
    parser.add_argument("--save", action="store_true", help="Save brief to DB")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output JSON summary")
    parser.add_argument("--markdown", action="store_true", help="Output rendered markdown")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    brief = generate_war_council_brief(
        scenario=args.scenario,
        theater=args.theater,
        commander_intent=args.intent,
        pmesii_summary=args.pmesii_summary,
        adversary_cog=args.adversary_cog,
        friendly_cog=args.friendly_cog,
        save_to_db=args.save,
    )

    if args.json_output:
        print(brief.to_json())
    elif args.markdown:
        print(brief.content_md)
    else:
        print("\n── War Council Brief ─────────────────────────────────────────────")
        print(f"Brief ID  : {brief.brief_id[:8].upper()}")
        print(f"Theater   : {brief.theater}")
        print(f"Doctrine  : {len(brief.doctrine_citations)} citations")
        print(f"Precedents: {len(brief.historical_precedents)} cases")
        print(
            f"RAG       : "
            f"{'ON (' + str(brief.strategy_result.rag_doc_count) + ' docs)' if brief.strategy_result.rag_active else 'OFFLINE'}"
        )
        print(f"Model     : {brief.strategy_result.model_used}")
        print(f"Latency   : {brief.strategy_result.latency_ms}ms")
        if brief.strategy_result.courses_of_action:
            rec_idx = brief.strategy_result.recommended_coa_index
            rec = brief.strategy_result.courses_of_action[rec_idx]
            print(f"Rec COA   : {rec.name} (score={rec.composite_score:.3f})")
        if brief.error:
            print(f"Error     : {brief.error}")


if __name__ == "__main__":
    _main()
