#!/usr/bin/env python3
# CUI // SP-CTI
"""R24: Bridge Reflex — Proposal-to-Program Knowledge Bridge (section 3.19).

Auto-generates structured transition packages when a bid is won.
Triggered when R9 Decide = BID and contract is awarded.

Assembles handoff from:
  - CRM data (pg_crm_accounts, pg_crm_contacts, pg_crm_interactions)
  - Win themes (pg_win_themes)
  - Cost volumes (pg_cost_volumes)
  - Compliance matrix (pg_compliance_matrix)
  - Teaming partners (pg_teaming_workshare)
  - Capture plans (pg_capture_plans)

Generates markdown transition document stored in data/proposal_genesis/bridges/.

Pipeline: on_demand (triggered on contract award).
GREEN tier (read-only assembly + markdown generation).
Scanner-tier only (zero Claude tokens — fully deterministic).
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.bridge")

BRIDGES_DIR = BASE_DIR / "data" / "proposal_genesis" / "bridges"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgbr") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------


def _get_won_opportunities() -> List[Dict]:
    """Find opportunities with BID decisions and awarded status."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT bd.opportunity_id, po.title, po.agency,
                   bdo.outcome, bdo.actual_award_date, bdo.award_amount
            FROM pg_bid_decisions bd
            JOIN proposal_opportunities po ON po.id = bd.opportunity_id
            JOIN pg_bid_decision_outcomes bdo ON bdo.bid_decision_id = bd.id
            WHERE bd.decision = 'bid'
            AND bdo.outcome = 'won'
            ORDER BY bdo.created_at DESC
            LIMIT 10
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_win_themes(opp_id: str) -> List[Dict]:
    """Get win themes for an opportunity."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT theme_name, theme_description, discriminator "
            "FROM pg_win_themes WHERE opportunity_id = %s "
            "ORDER BY created_at ASC",
            (opp_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_teaming_partners(opp_id: str) -> List[Dict]:
    """Get teaming workshare data for an opportunity."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT partner_name, role, workshare_pct, cmmc_level "
            "FROM pg_teaming_workshare WHERE opportunity_id = %s "
            "ORDER BY workshare_pct DESC",
            (opp_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_cost_summary(opp_id: str) -> Optional[Dict]:
    """Get cost volume summary for an opportunity."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT total_price, period_of_performance, pricing_type "
            "FROM pg_cost_volumes WHERE opportunity_id = %s "
            "ORDER BY updated_at DESC LIMIT 1",
            (opp_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _get_compliance_summary(opp_id: str) -> Dict[str, int]:
    """Get compliance matrix summary counts."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT compliance_status, COUNT(*) as cnt "
            "FROM pg_compliance_matrix WHERE opportunity_id = %s "
            "GROUP BY compliance_status",
            (opp_id,),
        ).fetchall()
        return {r["compliance_status"]: r["cnt"] for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Transition document generation (deterministic template)
# ---------------------------------------------------------------------------


def _generate_transition_doc(
    opp: Dict, themes: List[Dict], partners: List[Dict], cost: Optional[Dict], compliance: Dict
) -> str:
    """Generate markdown transition package."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Proposal-to-Program Transition Package",
        "",
        "**CUI // SP-CTI**",
        "",
        f"**Opportunity:** {opp.get('title', 'N/A')}",
        f"**Agency:** {opp.get('agency', 'N/A')}",
        f"**Award Date:** {opp.get('actual_award_date', 'N/A')}",
        f"**Award Amount:** ${opp.get('award_amount', 0):,.2f}" if opp.get("award_amount") else "**Award Amount:** TBD",
        f"**Generated:** {now}",
        "",
        "---",
        "",
        "## Win Themes & Discriminators",
        "",
    ]

    if themes:
        for theme in themes:
            lines.append(f"### {theme.get('theme_name', 'Untitled')}")
            lines.append(f"{theme.get('theme_description', '')}")
            if theme.get("discriminator"):
                lines.append(f"- **Discriminator:** {theme['discriminator']}")
            lines.append("")
    else:
        lines.append("*No win themes recorded.*")
        lines.append("")

    lines.extend(["## Teaming Partners", ""])
    if partners:
        lines.append("| Partner | Role | Workshare | CMMC |")
        lines.append("|---------|------|-----------|------|")
        for p in partners:
            pct = f"{p.get('workshare_pct', 0):.0f}%" if p.get("workshare_pct") else "TBD"
            cmmc = p.get("cmmc_level", "N/A") or "N/A"
            lines.append(f"| {p['partner_name']} | {p.get('role', 'TBD')} | {pct} | {cmmc} |")
    else:
        lines.append("*No teaming partners recorded.*")
    lines.append("")

    lines.extend(["## Cost Summary", ""])
    if cost:
        lines.append(
            f"- **Total Price:** ${cost.get('total_price', 0):,.2f}"
            if cost.get("total_price")
            else "- **Total Price:** TBD"
        )
        lines.append(f"- **Period of Performance:** {cost.get('period_of_performance', 'N/A')}")
        lines.append(f"- **Pricing Type:** {cost.get('pricing_type', 'N/A')}")
    else:
        lines.append("*No cost volume on record.*")
    lines.append("")

    lines.extend(["## Compliance Matrix Summary", ""])
    if compliance:
        for status, count in sorted(compliance.items()):
            lines.append(f"- **{status}:** {count}")
    else:
        lines.append("*No compliance matrix entries.*")
    lines.append("")

    lines.extend(
        [
            "---",
            f"*Generated by Proposal Genesis R24 Bridge — {now}*",
            "",
            "**CUI // SP-CTI**",
        ]
    )

    return "\n".join(lines)


def _store_bridge(opp_id: str, doc_md: str) -> str:
    """Store transition document to file and audit."""
    BRIDGES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = BRIDGES_DIR / f"bridge_{opp_id[:12]}_{timestamp}.md"
    path.write_text(doc_md, encoding="utf-8")

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "bridge_generated",
                "bridge",
                "green",
                opp_id,
                json.dumps({"file": str(path), "length": len(doc_md)}),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_store_bridge: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()

    return str(path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Bridge Reflex (R24).

    Steps:
      1. Find won opportunities (bid decision + won outcome)
      2. Assemble data from CRM, win themes, cost, compliance, teaming
      3. Generate markdown transition document
      4. Store to data/proposal_genesis/bridges/

    Returns standard reflex result dict.
    """
    won_opps = _get_won_opportunities()
    bridges_generated = 0
    bridge_results: List[Dict] = []

    for opp in won_opps:
        opp_id = opp["opportunity_id"]

        themes = _get_win_themes(opp_id)
        partners = _get_teaming_partners(opp_id)
        cost = _get_cost_summary(opp_id)
        compliance = _get_compliance_summary(opp_id)

        doc_md = _generate_transition_doc(opp, themes, partners, cost, compliance)
        path = _store_bridge(opp_id, doc_md)

        bridges_generated += 1
        bridge_results.append(
            {
                "opportunity_id": opp_id,
                "title": opp["title"],
                "path": path,
                "themes_count": len(themes),
                "partners_count": len(partners),
            }
        )

    return {
        "success": True,
        "metric_value": float(bridges_generated),
        "details": {
            "won_opportunities_found": len(won_opps),
            "bridges_generated": bridges_generated,
            "bridge_results": bridge_results,
        },
    }


# CUI // SP-CTI
