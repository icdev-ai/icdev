#!/usr/bin/env python3
# CUI // SP-CTI
"""R15: Review Reflex — AI Color Team Review Simulator (section 3.12).

Simulates Shipley color team reviews using deterministic scoring.
Triggered after draft/polish completion or on-demand.

Color team mapping:
  - After storyboard  → Pink Team review
  - After first draft → Red Team review
  - After quality gate → Gold Team review

Pipeline: on_demand (triggered after R8 Polish).
GREEN tier (read-only analysis, writes to pg_proposal_quality_scores).
Scanner-tier only (zero Claude tokens — fully deterministic).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.review")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgrev") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Review type determination
# ---------------------------------------------------------------------------

REVIEW_TYPES = {
    "pink": {"label": "Pink Team", "focus": "storyboard_compliance", "threshold": 0.40},
    "red": {"label": "Red Team", "focus": "content_quality", "threshold": 0.60},
    "gold": {"label": "Gold Team", "focus": "final_readiness", "threshold": 0.80},
    "white": {"label": "White Team", "focus": "compliance_check", "threshold": 0.90},
}

# ---------------------------------------------------------------------------
# Module-level constants — review scoring thresholds, overridable from
# proposal_genesis_config.yaml under reflexes.review.  Change config, not code.
# ---------------------------------------------------------------------------
_SHALL_RESPONSE_MIN_WORDS  = 50    # word count above which shall-response is expected
_SPECIFICITY_MIN_WORDS     = 100   # word count above which quantitative evidence is expected
_ISSUE_PENALTY             = 0.20  # score deduction per compliance issue
_PAST_PERF_BONUS           = 0.10  # score boost for past-performance references
_MIN_SECTION_CHARS         = 20    # minimum section length (chars) to score
_CRITICAL_SCORE_THRESHOLD  = 0.30  # score below which a finding is "critical" (else "major")
_DRAFTS_FETCH_LIMIT        = 30    # max drafts fetched per review run
_FINDING_DETAILS_LIMIT     = 20    # max findings stored in finding_details array


def _determine_review_type(opp_status: str, has_quality_scores: bool) -> str:
    """Determine appropriate color review based on opportunity status."""
    if has_quality_scores:
        return "gold"
    if opp_status in ("drafting",):
        return "red"
    return "pink"


# ---------------------------------------------------------------------------
# Deterministic review scoring
# ---------------------------------------------------------------------------


def _score_section_compliance(text: str) -> Dict[str, Any]:
    """Score section for compliance with RFP requirements (deterministic)."""
    import re

    issues = []
    word_count = len(text.split())

    # Check for shall/must response patterns
    shall_responses = len(re.findall(r"\b(?:we will|we shall|our approach)\b", text, re.I))
    if word_count > _SHALL_RESPONSE_MIN_WORDS and shall_responses == 0:
        issues.append("No shall/must response language detected")

    # Check for specificity (numbers, dates, metrics)
    specifics = len(re.findall(r"\b\d+[\.\d]*\s*(?:%|percent|days|hours|years)\b", text, re.I))
    if word_count > _SPECIFICITY_MIN_WORDS and specifics == 0:
        issues.append("No quantitative evidence (numbers, dates, metrics)")

    # Check for past performance references
    perf_refs = len(re.findall(r"\b(?:previously|delivered|completed|demonstrated|proven|awarded)\b", text, re.I))

    score = max(0.0, 1.0 - len(issues) * _ISSUE_PENALTY)
    if perf_refs > 0:
        score = min(1.0, score + _PAST_PERF_BONUS)

    return {"score": round(score, 2), "issues": issues, "specifics_count": specifics}


def _simulate_review(opp_id: str, review_type: str) -> Dict[str, Any]:
    """Run a simulated color team review on all drafts for an opportunity."""
    conn = get_connection()
    try:
        drafts = conn.execute(
            "SELECT id, section_text, section_id FROM proposal_section_drafts "
            "WHERE opportunity_id = %s AND status IN ('draft', 'approved') "
            f"ORDER BY created_at DESC LIMIT {_DRAFTS_FETCH_LIMIT}",
            (opp_id,),
        ).fetchall()
    except Exception:
        return {"status": "error", "findings": 0}
    finally:
        conn.close()

    if not drafts:
        return {"status": "no_drafts", "findings": 0}

    config = REVIEW_TYPES.get(review_type, REVIEW_TYPES["red"])
    findings = []
    total_score = 0.0

    for draft in drafts:
        text = draft["section_text"] or ""
        if len(text.strip()) < _MIN_SECTION_CHARS:
            findings.append(
                {
                    "draft_id": draft["id"],
                    "severity": "critical",
                    "finding": f"Section has insufficient content (< {_MIN_SECTION_CHARS} chars)",
                }
            )
            continue

        result = _score_section_compliance(text)
        total_score += result["score"]

        if result["score"] < config["threshold"]:
            severity = "critical" if result["score"] < _CRITICAL_SCORE_THRESHOLD else "major"
            for issue in result["issues"]:
                findings.append(
                    {
                        "draft_id": draft["id"],
                        "severity": severity,
                        "finding": issue,
                        "score": result["score"],
                    }
                )

    avg_score = total_score / len(drafts) if drafts else 0.0

    # Audit the review
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                f"color_review_{review_type}",
                "review",
                "green",
                opp_id,
                json.dumps(
                    {
                        "review_type": review_type,
                        "sections_reviewed": len(drafts),
                        "findings_count": len(findings),
                        "avg_score": round(avg_score, 3),
                    }
                ),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_simulate_review: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()

    return {
        "status": "completed",
        "review_type": review_type,
        "review_label": config["label"],
        "sections_reviewed": len(drafts),
        "findings": len(findings),
        "avg_score": round(avg_score, 3),
        "finding_details": findings[:_FINDING_DETAILS_LIMIT],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Review Reflex (R15).

    Steps:
      1. Find opportunities with status='drafting' or 'polishing'
      2. Determine appropriate review type per opportunity
      3. Simulate color team review with deterministic scoring
      4. Audit findings

    Returns standard reflex result dict.
    """
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT po.id, po.title, po.status
            FROM proposal_opportunities po
            WHERE po.status IN ('tracking', 'drafting', 'reviewing')
            ORDER BY po.created_at DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    total_findings = 0
    review_results: List[Dict] = []

    for row in rows:
        # Check if quality scores exist (determines gold vs red/pink)
        conn = get_connection()
        try:
            qs_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM pg_proposal_quality_scores WHERE opportunity_id = %s",
                (row["id"],),
            ).fetchone()
            has_quality = (qs_row["cnt"] or 0) > 0 if qs_row else False
        except Exception:
            has_quality = False
        finally:
            conn.close()

        review_type = _determine_review_type(row["status"], has_quality)
        result = _simulate_review(row["id"], review_type)
        total_findings += result.get("findings", 0)

        review_results.append(
            {
                "opportunity_id": row["id"],
                "title": row["title"],
                "review_type": result.get("review_type", review_type),
                "findings": result.get("findings", 0),
                "avg_score": result.get("avg_score", 0),
            }
        )

    return {
        "success": True,
        "metric_value": float(total_findings),
        "details": {
            "opportunities_reviewed": len(rows),
            "total_findings": total_findings,
            "review_results": review_results,
        },
    }


# CUI // SP-CTI
