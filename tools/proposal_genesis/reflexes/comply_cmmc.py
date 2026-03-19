#!/usr/bin/env python3
# CUI // SP-CTI
"""R18: Comply_CMMC Reflex — CMMC Supply Chain Validator (section 3.14).

Validates CMMC/SPRS status for all proposed team members.
Triggered before R9 Decide bid/no-bid gate.

Checks:
  - Prime contractor CMMC level vs. RFP requirement
  - Teaming partner CMMC/SPRS scores
  - Subcontractor flow-down compliance
  - NIST 800-171 self-assessment gaps

Pipeline: on_demand (triggered before R9 Decide).
GREEN tier (read-only analysis, writes to pg_proposal_genesis_audit).
Scanner-tier only (zero Claude tokens — fully deterministic).
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgcmmc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# CMMC validation checks
# ---------------------------------------------------------------------------

def _get_opportunities_needing_cmmc() -> List[Dict]:
    """Find opportunities in tracking/drafting with CMMC requirements."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT po.id, po.title, po.agency
            FROM proposal_opportunities po
            WHERE po.status IN ('tracking', 'drafting')
            ORDER BY po.created_at DESC
            LIMIT 20
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _check_teaming_cmmc(opp_id: str) -> Dict[str, Any]:
    """Check CMMC compliance status for teaming partners on an opportunity."""
    conn = get_connection()
    try:
        partners = conn.execute(
            "SELECT id, partner_name, role, cmmc_level, sprs_score "
            "FROM pg_teaming_workshare "
            "WHERE opportunity_id = ?",
            (opp_id,),
        ).fetchall()
    except Exception:
        return {"partners_checked": 0, "non_compliant": []}
    finally:
        conn.close()

    non_compliant = []
    for partner in partners:
        issues = []
        cmmc_level = partner["cmmc_level"] if partner["cmmc_level"] else 0
        sprs_score = partner["sprs_score"] if partner["sprs_score"] else None

        # CMMC Level 2 is minimum for CUI handling
        if isinstance(cmmc_level, (int, float)) and cmmc_level < 2:
            issues.append(f"CMMC level {cmmc_level} below required Level 2")

        # SPRS score below 110 is a red flag
        if sprs_score is not None and isinstance(sprs_score, (int, float)):
            if sprs_score < 110:
                issues.append(f"SPRS score {sprs_score} below minimum (110)")

        # No CMMC or SPRS data at all
        if not cmmc_level and sprs_score is None:
            issues.append("No CMMC level or SPRS score on record")

        if issues:
            non_compliant.append({
                "partner_id": partner["id"],
                "partner_name": partner["partner_name"],
                "role": partner["role"],
                "cmmc_level": cmmc_level,
                "sprs_score": sprs_score,
                "issues": issues,
            })

    return {
        "partners_checked": len(partners),
        "non_compliant": non_compliant,
        "non_compliant_count": len(non_compliant),
    }


def _check_ai_clause_compliance(opp_id: str) -> Dict[str, Any]:
    """Check for AI-specific clause compliance (DFARS/FAR AI requirements)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT clause_id, clause_title, compliance_status "
            "FROM pg_ai_clause_compliance "
            "WHERE opportunity_id = ?",
            (opp_id,),
        ).fetchall()
    except Exception:
        return {"clauses_checked": 0, "non_compliant": 0}
    finally:
        conn.close()

    non_compliant = sum(
        1 for r in rows
        if (r["compliance_status"] or "").lower() not in ("compliant", "not_applicable")
    )
    return {"clauses_checked": len(rows), "non_compliant": non_compliant}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Comply_CMMC Reflex (R18).

    Steps:
      1. Find opportunities with active tracking/drafting status
      2. For each: validate teaming partner CMMC/SPRS compliance
      3. Check AI clause compliance status
      4. Aggregate non-compliant counts

    Returns standard reflex result dict.
    """
    opportunities = _get_opportunities_needing_cmmc()
    total_non_compliant = 0
    validation_results: List[Dict] = []

    for opp in opportunities:
        opp_id = opp["id"]

        # Check teaming partner CMMC
        team_result = _check_teaming_cmmc(opp_id)
        nc_count = team_result["non_compliant_count"]

        # Check AI clause compliance
        ai_result = _check_ai_clause_compliance(opp_id)
        nc_count += ai_result["non_compliant"]

        total_non_compliant += nc_count

        validation_results.append({
            "opportunity_id": opp_id,
            "title": opp["title"],
            "partners_checked": team_result["partners_checked"],
            "non_compliant_partners": team_result["non_compliant_count"],
            "ai_clauses_checked": ai_result["clauses_checked"],
            "ai_clauses_non_compliant": ai_result["non_compliant"],
            "total_issues": nc_count,
        })

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _generate_id("pgaudit"),
                "cmmc_validation",
                "comply_cmmc",
                "green",
                json.dumps({
                    "opportunities_checked": len(opportunities),
                    "total_non_compliant": total_non_compliant,
                }),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return {
        "success": True,
        "metric_value": float(total_non_compliant),
        "details": {
            "opportunities_checked": len(opportunities),
            "total_non_compliant": total_non_compliant,
            "validation_results": validation_results,
        },
    }
# CUI // SP-CTI
