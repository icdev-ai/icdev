#!/usr/bin/env python3
# CUI // SP-CTI
"""R2: Scout Reflex — Competitive intelligence briefs for GovCon.

Wraps tools/govcon/award_tracker.py and tools/govcon/competitor_profiler.py
to generate daily competitor intelligence briefs.  Scanner-tier only
(zero Claude tokens).

Pipeline: Independent schedule (daily), not part of main chain.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.scout")

REPORTS_DIR = BASE_DIR / "data" / "proposal_genesis" / "briefs"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_air_gapped() -> bool:
    return os.environ.get("ICDEV_AIR_GAPPED", "").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# Award scanning wrapper
# ---------------------------------------------------------------------------


def _scan_awards() -> Dict[str, Any]:
    """Run award tracker scan, return results."""
    try:
        from tools.govcon.award_tracker import scan_awards

        return scan_awards()
    except Exception as exc:
        return {"status": "error", "message": str(exc), "new_awards": 0}


# ---------------------------------------------------------------------------
# Competitor profiling
# ---------------------------------------------------------------------------


def _get_top_competitors(limit: int = 10) -> List[Dict]:
    """Get top competitors by award count from DB."""
    try:
        from tools.govcon.competitor_profiler import get_leaderboard

        result = get_leaderboard(limit=limit)
        return result.get("leaderboard", [])
    except Exception:
        return []


def _profile_competitor(vendor_name: str) -> Dict[str, Any]:
    """Get detailed profile for a competitor."""
    try:
        from tools.govcon.competitor_profiler import profile_vendor

        return profile_vendor(vendor_name)
    except Exception:
        return {"vendor": vendor_name, "status": "error"}


# ---------------------------------------------------------------------------
# Opportunity overlap detection
# ---------------------------------------------------------------------------


def _find_competitor_overlaps() -> List[Dict]:
    """Find opportunities where known competitors also bid.

    Matches competitor names in govcon_awards against opportunities
    the user is tracking in proposal_opportunities.
    """
    conn = get_connection()
    overlaps = []
    try:
        # Get active opportunities
        opps = conn.execute(
            "SELECT id, title, naics_code, agency "
            "FROM proposal_opportunities "
            "WHERE status IN ('tracking', 'drafting', 'reviewing') "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()

        for opp in opps:
            naics = opp["naics_code"] or ""
            agency = opp["agency"] or ""
            if not naics and not agency:
                continue

            # Find competitors that have won awards in same NAICS/agency
            query_parts = []
            params = []
            if naics:
                query_parts.append("naics_code = ?")
                params.append(naics)
            if agency:
                query_parts.append("agency LIKE ?")
                params.append(f"%{agency}%")

            where = " OR ".join(query_parts)
            competitors = conn.execute(
                f"SELECT DISTINCT awardee_name, COUNT(*) as award_count "  # nosec B608 — where clauses from parameterized builder
                f"FROM govcon_awards WHERE {where} "
                f"GROUP BY awardee_name ORDER BY award_count DESC LIMIT 5",
                params,
            ).fetchall()

            if competitors:
                overlaps.append(
                    {
                        "opportunity_id": opp["id"],
                        "opportunity_title": opp["title"],
                        "naics": naics,
                        "agency": agency,
                        "likely_competitors": [
                            {"name": c["awardee_name"], "awards_in_space": c["award_count"]} for c in competitors
                        ],
                    }
                )
    except Exception:
        pass
    finally:
        conn.close()
    return overlaps


# ---------------------------------------------------------------------------
# Brief generation (deterministic, scanner-tier)
# ---------------------------------------------------------------------------


def _generate_brief(
    award_scan: Dict,
    leaderboard: List[Dict],
    overlaps: List[Dict],
) -> str:
    """Generate markdown competitor intelligence brief."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Competitor Intelligence Brief — {now}",
        "",
        "**CUI // SP-CTI**",
        "",
        "## Award Scan Summary",
        "",
    ]

    if award_scan.get("status") == "ok":
        lines.append(f"- **New awards discovered:** {award_scan.get('new_awards', 0)}")
        lines.append(f"- **Total fetched:** {award_scan.get('total_fetched', 0)}")
        lines.append(f"- **Lookback period:** {award_scan.get('lookback_days', 90)} days")
    elif award_scan.get("status") == "air_gapped":
        lines.append("- *Air-gapped mode — no external scanning performed*")
    else:
        lines.append(f"- *Scan error:* {award_scan.get('message', 'unknown')}")

    lines.append("")
    lines.append("## Competitor Leaderboard (Top 10)")
    lines.append("")

    if leaderboard:
        lines.append("| Rank | Vendor | Awards | Total Value | NAICS Diversity |")
        lines.append("|------|--------|--------|-------------|-----------------|")
        for entry in leaderboard[:10]:
            value = entry.get("total_value", 0)
            value_str = f"${value:,.0f}" if value else "$0"
            lines.append(
                f"| {entry.get('rank', '?')} "
                f"| {entry.get('vendor', '?')} "
                f"| {entry.get('awards', 0)} "
                f"| {value_str} "
                f"| {entry.get('naics_diversity', 0)} |"
            )
    else:
        lines.append("*No award data available yet.*")

    lines.append("")
    lines.append("## Competitive Overlap (Active Opportunities)")
    lines.append("")

    if overlaps:
        for ov in overlaps[:10]:
            lines.append(f"### {ov['opportunity_title'][:80]}")
            lines.append(f"- **NAICS:** {ov['naics']}  |  **Agency:** {ov['agency']}")
            for comp in ov.get("likely_competitors", []):
                lines.append(f"  - {comp['name']} ({comp['awards_in_space']} awards in space)")
            lines.append("")
    else:
        lines.append("*No competitive overlaps detected for active opportunities.*")

    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by Proposal Genesis R2 Scout — {now}*")

    return "\n".join(lines)


def _store_brief(brief_md: str) -> str:
    """Store brief to file and audit table."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    brief_path = REPORTS_DIR / f"intel_brief_{timestamp}.md"
    brief_path.write_text(brief_md, encoding="utf-8")

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                f"pgaudit-{uuid.uuid4().hex[:10]}",
                "brief_generated",
                "scout",
                "green",
                json.dumps({"file": str(brief_path), "length": len(brief_md)}),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_store_brief: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s", exc)
    finally:
        conn.close()

    return str(brief_path)


# ---------------------------------------------------------------------------
# Bid decision outcome tracking
# ---------------------------------------------------------------------------


def _match_awards_to_decisions() -> Dict[str, int]:
    """Match award notices against bid decisions to record outcomes.

    Checks govcon_awards for awards on opportunities we have bid decisions
    for.  When a match is found:
      - Records outcome in pg_bid_decision_outcomes (won if we won, lost otherwise)
      - Creates pg_win_loss_records for R13 Analyze to process

    Returns counts: {"outcomes_recorded": N, "win_loss_created": N}
    """
    conn = get_connection()
    outcomes_recorded = 0
    win_loss_created = 0
    try:
        # Find bid decisions that don't have outcomes yet
        pending = conn.execute("""
            SELECT bd.id as decision_id, bd.opportunity_id, bd.decision,
                   o.title, o.agency, o.naics_code, o.sol_number
            FROM pg_bid_decisions bd
            JOIN sam_gov_opportunities o ON o.id = bd.opportunity_id
            WHERE bd.decision = 'bid'
            AND bd.id NOT IN (
                SELECT bid_decision_id FROM pg_bid_decision_outcomes
            )
        """).fetchall()

        for row in pending:
            opp_id = row["opportunity_id"]
            sol_number = row["sol_number"] or ""
            agency = row["agency"] or ""

            # Look for matching award in govcon_awards
            # Match by solicitation number first, then agency+NAICS
            award = None
            if sol_number:
                award = conn.execute(
                    "SELECT awardee_name, award_amount, award_date FROM govcon_awards WHERE sol_number = %s LIMIT 1",
                    (sol_number,),
                ).fetchone()

            if not award and agency:
                # Fallback: match by agency + NAICS within recent timeframe
                naics = row["naics_code"] or ""
                if naics:
                    award = conn.execute(
                        "SELECT awardee_name, award_amount, award_date "
                        "FROM govcon_awards "
                        "WHERE agency LIKE %s AND naics_code = %s "
                        "AND award_date > datetime('now', '-90 days') "
                        "ORDER BY award_date DESC LIMIT 1",
                        (f"%{agency}%", naics),
                    ).fetchone()

            if not award:
                continue

            # Determine if we won or lost
            awardee = (award["awardee_name"] or "").lower()
            # Check if the awardee matches our org (simple heuristic)
            our_names = ["icdev", "intelligent certified", "our company"]
            we_won = any(n in awardee for n in our_names)
            outcome = "won" if we_won else "lost"

            # Record outcome in pg_bid_decision_outcomes
            now = _utcnow_iso()
            outcome_id = f"pgout-{uuid.uuid4().hex[:10]}"
            conn.execute(
                "INSERT INTO pg_bid_decision_outcomes "
                "(id, bid_decision_id, outcome, actual_award_date, "
                "award_amount, notes, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    outcome_id,
                    row["decision_id"],
                    outcome,
                    award["award_date"],
                    award["award_amount"],
                    f"Awarded to: {award['awardee_name']}",
                    now,
                ),
            )
            outcomes_recorded += 1

            # Create win/loss record for R13 Analyze
            wl_id = f"pgwl-{uuid.uuid4().hex[:10]}"
            competitor_name = award["awardee_name"] if not we_won else ""
            conn.execute(
                "INSERT INTO pg_win_loss_records "
                "(id, opportunity_id, outcome, competitor_name, "
                "competitor_strengths, our_strengths, our_weaknesses, "
                "lessons_learned, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    wl_id,
                    opp_id,
                    outcome,
                    competitor_name,
                    "",  # To be filled by human review
                    "",
                    "",
                    "",
                    now,
                ),
            )
            win_loss_created += 1

            # Audit
            conn.execute(
                "INSERT INTO pg_proposal_genesis_audit "
                "(id, event_type, reflex_name, risk_tier, opportunity_id, "
                "details, success, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"pgaudit-{uuid.uuid4().hex[:10]}",
                    "outcome_recorded",
                    "scout",
                    "green",
                    opp_id,
                    json.dumps(
                        {
                            "decision_id": row["decision_id"],
                            "outcome": outcome,
                            "awardee": award["awardee_name"],
                            "award_amount": award["award_amount"],
                        }
                    ),
                    1,
                    now,
                ),
            )

        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_match_awards_to_decisions: best-effort INSERT into pg_bid_decision_outcomes failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()

    return {
        "outcomes_recorded": outcomes_recorded,
        "win_loss_created": win_loss_created,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Scout Reflex (R2).

    Steps:
      1. Scan SAM.gov for new award notices (air-gap safe)
      2. Match awards to bid decisions → record outcomes + win/loss records
      3. Build competitor leaderboard from awards DB
      4. Detect competitive overlaps with active opportunities
      5. Generate markdown intel brief
      6. Store brief to data/proposal_genesis/briefs/

    Returns standard reflex result dict.
    """
    # Step 1: Scan awards (skip in air-gapped mode)
    if _is_air_gapped():
        award_result = {"status": "air_gapped", "new_awards": 0}
    else:
        award_result = _scan_awards()

    # Step 2: Match awards to bid decisions
    outcome_result = _match_awards_to_decisions()

    # Step 3: Get leaderboard
    leaderboard = _get_top_competitors(limit=10)

    # Step 4: Detect overlaps
    overlaps = _find_competitor_overlaps()

    # Step 5: Generate brief
    brief_md = _generate_brief(award_result, leaderboard, overlaps)

    # Step 6: Store brief
    brief_path = _store_brief(brief_md)

    briefs_generated = 1 if brief_md else 0

    return {
        "success": True,
        "metric_value": float(briefs_generated),
        "details": {
            "briefs_generated": briefs_generated,
            "brief_path": brief_path,
            "new_awards": award_result.get("new_awards", 0),
            "competitors_tracked": len(leaderboard),
            "overlaps_found": len(overlaps),
            "outcomes_recorded": outcome_result.get("outcomes_recorded", 0),
            "win_loss_created": outcome_result.get("win_loss_created", 0),
            "air_gapped": _is_air_gapped(),
        },
    }
