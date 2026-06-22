# CUI // SP-CTI
"""GovCon Discovery Reflex — 24h PM role continuous discovery against SAM.gov.

Fetches latest SAM.gov opportunities from govcon DB tables (if available),
runs the product_manager role's run_continuous_discovery step against them,
and returns a summary of opportunities analyzed and insights surfaced.

Cadence: 24 hours (registered in genesis_config.yaml under nova reflexes).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

IMPLEMENTATION_STATUS = "stub"


def _fetch_govcon_opportunities(conn: Any) -> list[dict]:
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, title, agency, naics_code, posted_date, description "
            "FROM sam_gov_opportunities "
            "ORDER BY posted_date DESC "
            "LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _run_continuous_discovery(opportunity: dict) -> dict | None:
    try:
        from tools.llm.router import LLMRouter, LLMRequest

        router = LLMRouter()
        prompt = (
            f"Run continuous discovery (OST framing) on this SAM.gov opportunity.\n"
            f"Title: {opportunity.get('title', '')}\n"
            f"Agency: {opportunity.get('agency', '')}\n"
            f"NAICS: {opportunity.get('naics_code', '')}\n"
            f"Synopsis: {opportunity.get('description', '')}\n\n"
            f"Return: opportunity statement, key assumptions to test, "
            f"stakeholder personas, and a bid/no-bid signal (go/no-go)."
        )
        req = LLMRequest(function="agent_product_manager", prompt=prompt, max_tokens=512)
        response = router.invoke("agent_product_manager", req)
        return {
            "opportunity_id": opportunity.get("id"),
            "title": opportunity.get("title", ""),
            "insight": response.content if hasattr(response, "content") else str(response),
        }
    except Exception as exc:
        return {
            "opportunity_id": opportunity.get("id"),
            "title": opportunity.get("title", ""),
            "insight": None,
            "error": str(exc)[:200],
        }


def run(ctx: dict, conn: Any) -> dict:
    opportunities = _fetch_govcon_opportunities(conn)

    if not opportunities:
        return {
            "opportunities_analyzed": 0,
            "insights": [],
        }

    insights: list[dict] = []
    for opp in opportunities:
        result = _run_continuous_discovery(opp)
        if result:
            insights.append(result)

    try:
        from tools.nova.souls.product_manager_soul import add_premortem_pattern

        for insight in insights:
            if insight.get("insight"):
                add_premortem_pattern(f"govcon_discovery:{insight.get('opportunity_id', 'unknown')}")
    except Exception:
        pass

    return {
        "opportunities_analyzed": len(insights),
        "insights": insights,
    }
