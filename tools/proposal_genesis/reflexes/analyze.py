#!/usr/bin/env python3
# CUI // SP-CTI
"""R13: Analyze Reflex — Win/loss analysis + lessons learned.

Scans completed opportunities (won/lost/cancelled) and generates
structured win/loss records with categorized lessons learned.
Feeds insights back into the scoring model via D-PG-9 calibration.

Pipeline: weekly Sun 20:00 (independent schedule).
GREEN tier (read-only analysis, writes to pg_win_loss_* tables).
Scanner-tier LLM only (zero Claude tokens — fully deterministic).
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

logger = get_logger("icdev.proposal_genesis.reflexes.analyze")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Module-level constants — win/loss analysis thresholds, overridable from
# proposal_genesis_config.yaml under reflexes.analyze.  Change config, not code.
# ---------------------------------------------------------------------------
_STRENGTH_THRESHOLD     = 0.60   # dimension score ≥ this → strength
_WEAKNESS_THRESHOLD     = 0.40   # dimension score < this → weakness
_CAP_FIT_WEAKNESS       = 0.50   # capability_fit below this → capability-gap lesson
_COMPLIANCE_WEAKNESS    = 0.50   # compliance_readiness below this → compliance lesson
_RESOURCE_WEAKNESS      = 0.50   # resource_availability below this → staffing lesson
_QUALITY_FLOOR          = 65     # avg draft quality below this (out of 100) → quality lesson
_COMPETITOR_FETCH_LIMIT = 5      # max recent competitor awards fetched
_MAX_ANALYSES_PER_RUN   = 20     # max opportunities analyzed per run


# ---------------------------------------------------------------------------
# Lesson categories and keyword detection
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    "technical": [
        "technical",
        "technology",
        "architecture",
        "software",
        "hardware",
        "integration",
        "api",
        "platform",
        "cloud",
        "infrastructure",
        "cybersecurity",
        "encryption",
        "zero trust",
    ],
    "management": [
        "management",
        "schedule",
        "timeline",
        "milestone",
        "team",
        "resource",
        "staffing",
        "leadership",
        "communication",
        "risk",
        "project",
        "program",
    ],
    "pricing": [
        "price",
        "cost",
        "budget",
        "lpta",
        "best value",
        "competitive",
        "rate",
        "discount",
        "profit",
        "margin",
        "fee",
    ],
    "past_performance": [
        "past performance",
        "cpars",
        "reference",
        "experience",
        "track record",
        "prior work",
        "contract history",
    ],
    "compliance": [
        "compliance",
        "fedramp",
        "cmmc",
        "nist",
        "ato",
        "stig",
        "fips",
        "cui",
        "clearance",
        "security",
        "audit",
    ],
    "staffing": [
        "staffing",
        "personnel",
        "resume",
        "key personnel",
        "labor",
        "sme",
        "subject matter",
        "workforce",
        "hire",
        "recruit",
    ],
}


def _classify_category(text: str) -> str:
    """Classify a lesson into a category using keyword matching."""
    text_lower = text.lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        scores[cat] = sum(1 for kw in keywords if kw in text_lower)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


# ---------------------------------------------------------------------------
# Find analyzable opportunities
# ---------------------------------------------------------------------------


def _get_completed_opportunities() -> List[Dict]:
    """Find opportunities with outcomes but no win/loss record."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT o.id, o.title, o.agency, o.naics_code,
                   o.set_aside, o.status, o.sol_number,
                   bd.decision, bd.win_probability, bd.score_breakdown,
                   bd.id as bid_decision_id,
                   bdo.outcome, bdo.award_amount, bdo.notes,
                   bdo.id as outcome_id
            FROM sam_gov_opportunities o
            JOIN pg_bid_decisions bd ON bd.opportunity_id = o.id
            JOIN pg_bid_decision_outcomes bdo ON bdo.bid_decision_id = bd.id
            WHERE bdo.outcome IN ('won', 'lost', 'no_award', 'cancelled')
            AND o.id NOT IN (
                SELECT opportunity_id FROM pg_win_loss_records
            )
            ORDER BY bdo.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_competitor_data(opportunity_id: str) -> Optional[Dict]:
    """Get competitor intel for this opportunity from scout data."""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT vendor_name, award_amount, award_date
            FROM govcon_awards
            WHERE naics_code IN (
                SELECT naics_code FROM sam_gov_opportunities
                WHERE id = %s
            )
            ORDER BY award_date DESC LIMIT {_COMPETITOR_FETCH_LIMIT}
        """,
            (opportunity_id,),
        ).fetchall()
        if rows:
            return {
                "competitors": [dict(r) for r in rows],
                "top_competitor": rows[0]["vendor_name"],
            }
        return None
    except Exception:
        return None
    finally:
        conn.close()


def _get_draft_quality(opportunity_id: str) -> Optional[Dict]:
    """Get quality score data for drafts on this opportunity."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT composite_score, grammar_score, readability_score,
                   tone_score, plagiarism_score, ai_detection_score
            FROM pg_proposal_quality_scores
            WHERE opportunity_id = %s
            ORDER BY created_at DESC
        """,
            (opportunity_id,),
        ).fetchall()
        if not rows:
            return None
        avg_composite = sum(r["composite_score"] for r in rows) / len(rows)
        return {
            "avg_composite": round(avg_composite, 1),
            "draft_count": len(rows),
        }
    except Exception:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Generate analysis
# ---------------------------------------------------------------------------


def _analyze_opportunity(opp: Dict) -> Dict:
    """Generate win/loss analysis for a completed opportunity.

    Returns dict with strengths, weaknesses, lessons, competitor data.
    """
    outcome = opp.get("outcome", "")
    decision = opp.get("decision", "")
    win_prob = opp.get("win_probability", 0)
    score_raw = opp.get("score_breakdown")
    opp_id = opp.get("id", "")

    # Parse score breakdown
    dimensions = {}
    if score_raw:
        try:
            dimensions = json.loads(score_raw)
        except (json.JSONDecodeError, TypeError):
            dimensions = {}

    # Identify strengths and weaknesses from dimension scores
    strengths = []
    weaknesses = []
    for dim, score in sorted(dimensions.items(), key=lambda x: x[1], reverse=True):
        label = dim.replace("_", " ").title()
        if score >= _STRENGTH_THRESHOLD:
            strengths.append(f"{label} ({score:.0%})")
        elif score < _WEAKNESS_THRESHOLD:
            weaknesses.append(f"{label} ({score:.0%})")

    # Competitor data
    comp_data = _get_competitor_data(opp_id)
    competitor_name = ""
    competitor_strengths = ""
    if comp_data and outcome == "lost":
        competitor_name = comp_data.get("top_competitor", "Unknown")
        competitor_strengths = f"Active in NAICS with {len(comp_data['competitors'])} recent awards"

    # Quality data
    quality = _get_draft_quality(opp_id)

    # Generate lessons
    lessons = []

    # Lesson from decision accuracy
    if outcome == "won" and decision == "bid":
        lessons.append(
            {
                "category": "management",
                "lesson": (
                    f"Bid decision was correct (win prob {win_prob:.0%}). "
                    f"Scoring model calibrated well for this opportunity type."
                ),
                "actionable": True,
            }
        )
    elif outcome == "lost" and decision == "bid":
        lessons.append(
            {
                "category": "management",
                "lesson": (
                    f"Bid decision overestimated win probability ({win_prob:.0%}). "
                    f"Review scoring dimensions for this NAICS/agency combination."
                ),
                "actionable": True,
            }
        )
    elif outcome == "won" and decision == "no_bid":
        lessons.append(
            {
                "category": "management",
                "lesson": (
                    "Missed winning opportunity — no-bid decision was premature. "
                    "Consider lowering no-bid threshold for similar opportunities."
                ),
                "actionable": True,
            }
        )

    # Lesson from capability gaps
    cap_fit = dimensions.get("capability_fit", 0)
    if cap_fit < _CAP_FIT_WEAKNESS and outcome == "lost":
        lessons.append(
            {
                "category": "technical",
                "lesson": (f"Low capability fit ({cap_fit:.0%}). Invest in capability development for this domain."),
                "actionable": True,
            }
        )

    # Lesson from quality
    if quality and quality["avg_composite"] < _QUALITY_FLOOR and outcome == "lost":
        lessons.append(
            {
                "category": "technical",
                "lesson": (
                    f"Low draft quality ({quality['avg_composite']:.0f}/100). "
                    f"Improve proposal writing process and knowledge base."
                ),
                "actionable": True,
            }
        )

    # Lesson from compliance
    comp_readiness = dimensions.get("compliance_readiness", 0)
    if comp_readiness < _COMPLIANCE_WEAKNESS:
        lessons.append(
            {
                "category": "compliance",
                "lesson": (
                    f"Low compliance readiness ({comp_readiness:.0%}). "
                    f"Strengthen compliance posture for targeted frameworks."
                ),
                "actionable": True,
            }
        )

    # Lesson from staffing/resources
    resource = dimensions.get("resource_availability", 0)
    if resource < _RESOURCE_WEAKNESS:
        lessons.append(
            {
                "category": "staffing",
                "lesson": (
                    f"Low resource availability ({resource:.0%}). Improve capture planning and early team allocation."
                ),
                "actionable": True,
            }
        )

    # Ensure at least one lesson
    if not lessons:
        lessons.append(
            {
                "category": "other",
                "lesson": (f"Opportunity {outcome}. No specific corrective actions identified from available data."),
                "actionable": False,
            }
        )

    return {
        "outcome": outcome,
        "our_strengths": "; ".join(strengths) if strengths else "None identified",
        "our_weaknesses": "; ".join(weaknesses) if weaknesses else "None identified",
        "competitor_name": competitor_name,
        "competitor_strengths": competitor_strengths,
        "lessons": lessons,
        "quality_data": quality,
    }


# ---------------------------------------------------------------------------
# Store analysis results
# ---------------------------------------------------------------------------


def _store_win_loss_record(opportunity_id: str, analysis: Dict) -> Optional[str]:
    """Write win/loss record to pg_win_loss_records."""
    conn = get_connection()
    rec_id = _generate_id("pgwl")
    now = _utcnow_iso()
    try:
        conn.execute(
            "INSERT INTO pg_win_loss_records "
            "(id, opportunity_id, outcome, competitor_name, "
            "competitor_strengths, our_strengths, our_weaknesses, "
            "lessons_learned, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                rec_id,
                opportunity_id,
                analysis["outcome"],
                analysis["competitor_name"],
                analysis["competitor_strengths"],
                analysis["our_strengths"],
                analysis["our_weaknesses"],
                json.dumps([item["lesson"] for item in analysis["lessons"]]),
                now,
            ),
        )
        conn.commit()
        return rec_id
    except Exception:
        return None
    finally:
        conn.close()


def _store_lessons(win_loss_id: str, lessons: List[Dict]) -> int:
    """Store categorized lessons in pg_win_loss_lessons."""
    conn = get_connection()
    stored = 0
    now = _utcnow_iso()
    try:
        for lesson in lessons:
            conn.execute(
                "INSERT INTO pg_win_loss_lessons "
                "(id, win_loss_id, category, lesson, actionable, "
                "applied, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    _generate_id("pglesson"),
                    win_loss_id,
                    lesson["category"],
                    lesson["lesson"],
                    1 if lesson.get("actionable", False) else 0,
                    0,
                    now,
                ),
            )
            stored += 1
        conn.commit()
        return stored
    except Exception:
        return stored
    finally:
        conn.close()


def _audit_analyze(event_type: str, opportunity_id: Optional[str], details: Dict, success: bool) -> None:
    """Log analyze event to audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                event_type,
                "analyze",
                "green",
                opportunity_id,
                json.dumps(details),
                1 if success else 0,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_audit_analyze: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Analyze Reflex (R13).

    Steps:
      1. Find completed opportunities with outcomes but no analysis
      2. Generate win/loss analysis with dimension breakdown
      3. Extract categorized lessons learned
      4. Store records in pg_win_loss_records + pg_win_loss_lessons
      5. Audit all analysis decisions

    Returns standard reflex result dict.
    """
    max_analyses = config.get("max_analyses_per_run", _MAX_ANALYSES_PER_RUN)

    completed = _get_completed_opportunities()
    analyzed = 0
    total_lessons = 0
    analysis_results = []

    for opp in completed[:max_analyses]:
        opp_id = opp.get("id", "")
        analysis = _analyze_opportunity(opp)

        rec_id = _store_win_loss_record(opp_id, analysis)
        if rec_id:
            lesson_count = _store_lessons(rec_id, analysis["lessons"])
            analyzed += 1
            total_lessons += lesson_count

            analysis_results.append(
                {
                    "opportunity_id": opp_id,
                    "outcome": analysis["outcome"],
                    "record_id": rec_id,
                    "lessons_count": lesson_count,
                }
            )

        _audit_analyze(
            f"win_loss_analyzed_{analysis['outcome']}",
            opp_id,
            {
                "record_id": rec_id,
                "outcome": analysis["outcome"],
                "lessons_count": len(analysis["lessons"]),
                "strengths": analysis["our_strengths"],
                "weaknesses": analysis["our_weaknesses"],
            },
            success=rec_id is not None,
        )

    return {
        "success": True,
        "metric_value": float(analyzed),
        "details": {
            "opportunities_completed": len(completed),
            "analyses_completed": analyzed,
            "total_lessons": total_lessons,
            "outcomes": {
                "won": sum(1 for r in analysis_results if r["outcome"] == "won"),
                "lost": sum(1 for r in analysis_results if r["outcome"] == "lost"),
                "other": sum(1 for r in analysis_results if r["outcome"] not in ("won", "lost")),
            },
            "analyses": analysis_results,
        },
    }
