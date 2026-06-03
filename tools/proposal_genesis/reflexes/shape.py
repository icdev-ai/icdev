#!/usr/bin/env python3
# CUI // SP-CTI
"""R3: Shape Reflex — Capture planning and teaming assessment.

Creates capture plans for promising opportunities, assesses teaming
partners for capability gap coverage, and generates initial positioning.
Scanner-tier only (zero Claude tokens).

Pipeline: Independent schedule (daily), not part of main chain.
"""

import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from proposal_genesis_config.yaml
# under reflexes.shape.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_FIT_STRONG_THRESHOLD   = 0.70  # score >= this → "strong_fit"
_FIT_GOOD_THRESHOLD     = 0.50  # score >= this → "good_fit"
_FIT_MARGINAL_THRESHOLD = 0.30  # score >= this → "marginal" (else "not_recommended")
_CAP_WEIGHT             = 0.40  # Capability overlap scoring weight
_CERT_WEIGHT            = 0.20  # Certification relevance scoring weight
_VEHICLE_WEIGHT         = 0.20  # Contract vehicle scoring weight
_SETASIDE_WEIGHT        = 0.20  # Set-aside alignment scoring weight
_CAP_OVERLAP_DIVISOR    = 0.30  # Capability score divisor (overlap / (total * divisor))
_CERT_SCORE_PER_MATCH   = 0.25  # Certification score increment per match
_VEHICLE_SCORE_PER_MATCH = 0.30 # Vehicle score increment per match
_SETASIDE_SCORE_PER_MATCH = 0.30 # Set-aside score increment per match
_OPP_ASSESS_LIMIT       = 10    # Max unassessed opportunities per run


def _compute_fit_thresholds(anomaly_cfg: "dict | None" = None) -> dict:
    """Compute adaptive partner fit thresholds from historical assessment distribution.

    Uses pg_teaming_assessments fit_score distribution to set tiers at:
      strong  = P75 (top quartile of observed scores)
      good    = P50 (median)
      marginal = P25 (bottom quartile still worth storing)

    Falls back to static defaults when fewer than min_samples rows exist or
    anomaly detection is disabled.
    """
    cfg = anomaly_cfg or {}
    if not cfg.get("enabled", True):
        return {
            "strong":   cfg.get("fallback_strong_threshold",   _FIT_STRONG_THRESHOLD),
            "good":     cfg.get("fallback_good_threshold",     _FIT_GOOD_THRESHOLD),
            "marginal": cfg.get("fallback_marginal_threshold", _FIT_MARGINAL_THRESHOLD),
            "computed": False,
        }

    min_samples = cfg.get("min_samples", 15)
    bounds      = cfg.get("adaptive_bounds", {})
    strong_floor   = float(bounds.get("strong_floor",   0.50))
    good_floor     = float(bounds.get("good_floor",     0.30))
    marginal_floor = float(bounds.get("marginal_floor", 0.10))

    conn = None
    try:
        conn = get_connection()
        # Use percentile_cont if available (PostgreSQL), else mean-based proxy
        try:
            row = conn.execute(
                "SELECT PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY fit_score) AS p75, "
                "PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY fit_score) AS p50, "
                "PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY fit_score) AS p25, "
                "COUNT(*) AS n FROM pg_teaming_assessments WHERE fit_score IS NOT NULL"
            ).fetchone()
        except Exception:
            # SQLite fallback: use mean ± fractions
            row = conn.execute(
                "SELECT AVG(fit_score) AS mean_f, COUNT(*) AS n "
                "FROM pg_teaming_assessments WHERE fit_score IS NOT NULL"
            ).fetchone()
            if row:
                n = row["n"] if isinstance(row, dict) else row[1]
                if n and n >= min_samples:
                    mean_f = float(row["mean_f"] if isinstance(row, dict) else row[0])
                    return {
                        "strong":   max(strong_floor,   round(mean_f + 0.15, 3)),
                        "good":     max(good_floor,     round(mean_f, 3)),
                        "marginal": max(marginal_floor, round(mean_f - 0.15, 3)),
                        "computed": True,
                    }
            row = None

        if row:
            n = row["n"] if isinstance(row, dict) else (row[3] if len(row) > 3 else 0)
            if n and n >= min_samples:
                p75 = float(row["p75"] if isinstance(row, dict) else row[0])
                p50 = float(row["p50"] if isinstance(row, dict) else row[1])
                p25 = float(row["p25"] if isinstance(row, dict) else row[2])
                return {
                    "strong":   max(strong_floor,   round(p75, 3)),
                    "good":     max(good_floor,     round(p50, 3)),
                    "marginal": max(marginal_floor, round(p25, 3)),
                    "computed": True,
                }
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return {
        "strong":   _FIT_STRONG_THRESHOLD,
        "good":     _FIT_GOOD_THRESHOLD,
        "marginal": _FIT_MARGINAL_THRESHOLD,
        "computed": False,
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Capture plan creation
# ---------------------------------------------------------------------------


def _get_opportunities_needing_plans() -> List[Dict]:
    """Find tracked opportunities without a capture plan."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT po.id, po.title, po.agency, po.naics_code,
                   po.response_deadline, po.estimated_value, po.status
            FROM proposal_opportunities po
            LEFT JOIN pg_capture_plans cp ON cp.opportunity_id = po.id
            WHERE po.status IN ('tracking', 'drafting')
            AND cp.id IS NULL
            ORDER BY po.response_deadline ASC
            LIMIT 10
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _create_capture_plan(opp: Dict) -> Optional[str]:
    """Create a skeleton capture plan for an opportunity.

    Deterministic template-based generation (no LLM).
    """
    conn = get_connection()
    plan_id = _generate_id("pgcp")
    now = _utcnow_iso()

    # Derive win strategy from opportunity attributes
    _ = opp.get("estimated_value", 0) or 0
    _ = opp.get("agency", "") or ""
    _ = opp.get("naics_code", "") or ""

    win_strategy = _derive_win_strategy(opp)
    discriminators = _derive_discriminators(opp)

    try:
        conn.execute(
            """
            INSERT INTO pg_capture_plans
                (id, opportunity_id, status, win_strategy, discriminators,
                 teaming_strategy, price_strategy, gate_reviews,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                plan_id,
                opp["id"],
                "draft",
                win_strategy,
                json.dumps(discriminators),
                "",  # Filled after teaming assessment
                "",  # Filled in later phases
                json.dumps({"pink_team": None, "red_team": None, "gold_team": None}),
                now,
                now,
            ),
        )

        # Create default capture activities
        activities = [
            ("Analyze RFP requirements", "requirements_analysis"),
            ("Identify teaming partners", "teaming"),
            ("Develop compliance matrix", "compliance"),
            ("Draft win themes", "strategy"),
            ("Assess competitive landscape", "competitive_analysis"),
        ]
        for desc, activity_type in activities:
            conn.execute(
                """
                INSERT INTO pg_capture_activities
                    (id, capture_plan_id, activity_type, description,
                     status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    _generate_id("pgact"),
                    plan_id,
                    activity_type,
                    desc,
                    "pending",
                    now,
                ),
            )

        # Audit
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _generate_id("pgaudit"),
                "capture_plan_created",
                "shape",
                "yellow",
                opp["id"],
                json.dumps({"plan_id": plan_id, "title": opp.get("title", "")}),
                1,
                now,
            ),
        )
        conn.commit()
        return plan_id
    except Exception:
        return None
    finally:
        conn.close()


def _derive_win_strategy(opp: Dict) -> str:
    """Generate deterministic win strategy from opportunity attributes."""
    title = (opp.get("title", "") or "").lower()
    agency = (opp.get("agency", "") or "").lower()

    strategies = []

    # Technical superiority signals
    tech_keywords = ["cloud", "cyber", "devsecops", "zero trust", "ai", "ml", "automation", "modernization", "digital"]
    if any(kw in title for kw in tech_keywords):
        strategies.append("Technical differentiation through proven automation and DevSecOps capabilities")

    # Compliance-heavy signals
    compliance_keywords = ["fedramp", "ato", "rmf", "nist", "cmmc", "stig", "compliance", "authorization"]
    if any(kw in title for kw in compliance_keywords):
        strategies.append("Compliance-first approach with accelerated ATO timeline")

    # DoD/IC signals
    dod_keywords = ["dod", "army", "navy", "air force", "space force", "defense", "intelligence", "nsa", "disa"]
    if any(kw in title or kw in agency for kw in dod_keywords):
        strategies.append("Mission understanding with cleared personnel and DoD past performance")

    if not strategies:
        strategies.append("Best-value approach emphasizing technical capability and past performance")

    return "; ".join(strategies)


def _derive_discriminators(opp: Dict) -> List[str]:
    """Identify discriminators from opportunity context."""
    title = (opp.get("title", "") or "").lower()
    discriminators = []

    keyword_discriminators = {
        "cloud": "FedRAMP-authorized cloud infrastructure",
        "cyber": "CMMC Level 2+ certified security operations",
        "devsecops": "Automated DevSecOps CI/CD with continuous ATO",
        "ai": "Responsible AI with NIST AI RMF compliance",
        "modernization": "Proven 7R migration methodology",
        "agile": "SAFe-certified agile delivery teams",
        "data": "End-to-end data engineering and analytics",
    }

    for kw, disc in keyword_discriminators.items():
        if kw in title:
            discriminators.append(disc)

    if not discriminators:
        discriminators.append("Rapid deployment with proven SDLC methodology")

    return discriminators


# ---------------------------------------------------------------------------
# Teaming assessment
# ---------------------------------------------------------------------------


def _get_partners() -> List[Dict]:
    """Get all active teaming partners from DB."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM pg_teaming_partners WHERE status IN ('active', 'prospect') ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_unassessed_opportunities() -> List[Dict]:
    """Find opportunities with capture plans but no teaming assessments."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT cp.opportunity_id, po.title, po.naics_code, po.agency
            FROM pg_capture_plans cp
            INNER JOIN proposal_opportunities po ON po.id = cp.opportunity_id
            LEFT JOIN pg_teaming_assessments ta ON ta.opportunity_id = cp.opportunity_id
            WHERE cp.status IN ('draft', 'active')
            AND ta.id IS NULL
            ORDER BY po.response_deadline ASC
            LIMIT {_OPP_ASSESS_LIMIT}
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _assess_partner_fit(opp: Dict, partner: Dict) -> Dict[str, Any]:
    """Assess partner fit for an opportunity.

    Uses deterministic keyword overlap + capability matching.
    """
    opp_title = (opp.get("title", "") or "").lower()
    _ = opp.get("naics_code", "") or ""

    partner_caps = (partner.get("capabilities", "") or "").lower()
    partner_certs = (partner.get("certifications", "") or "").lower()
    partner_vehicles = (partner.get("contract_vehicles", "") or "").lower()
    partner_set_asides = (partner.get("set_asides", "") or "").lower()

    score = 0.0
    gaps_filled = []
    risks = []

    # Capability keyword overlap (_CAP_WEIGHT)
    cap_keywords = set(re.findall(r"\b[a-z]{4,}\b", opp_title))
    partner_keywords = set(re.findall(r"\b[a-z]{4,}\b", partner_caps))
    if cap_keywords and partner_keywords:
        overlap = len(cap_keywords & partner_keywords)
        cap_score = min(1.0, overlap / max(1, len(cap_keywords) * _CAP_OVERLAP_DIVISOR))
        score += cap_score * _CAP_WEIGHT
        if overlap > 0:
            gaps_filled.append(f"Keyword overlap: {overlap} matching capabilities")

    # Certification relevance (_CERT_WEIGHT)
    cert_keywords = ["fedramp", "cmmc", "iso", "soc", "cleared", "secret", "top secret", "certified"]
    cert_matches = sum(1 for kw in cert_keywords if kw in partner_certs)
    cert_score = min(1.0, cert_matches * _CERT_SCORE_PER_MATCH)
    score += cert_score * _CERT_WEIGHT
    if cert_matches > 0:
        gaps_filled.append(f"{cert_matches} relevant certifications")

    # Contract vehicle match (_VEHICLE_WEIGHT)
    if partner_vehicles:
        vehicle_keywords = ["gwac", "bpa", "idiq", "gsa", "sewp", "ites", "alliant", "8a", "stars"]
        vehicle_matches = sum(1 for kw in vehicle_keywords if kw in partner_vehicles)
        vehicle_score = min(1.0, vehicle_matches * _VEHICLE_SCORE_PER_MATCH)
        score += vehicle_score * _VEHICLE_WEIGHT
        if vehicle_matches > 0:
            gaps_filled.append(f"{vehicle_matches} contract vehicle matches")

    # Set-aside alignment (_SETASIDE_WEIGHT)
    if partner_set_asides:
        sa_keywords = ["8a", "hubzone", "sdvosb", "wosb", "small business", "mentor-protege", "sdb"]
        sa_matches = sum(1 for kw in sa_keywords if kw in partner_set_asides)
        sa_score = min(1.0, sa_matches * _SETASIDE_SCORE_PER_MATCH)
        score += sa_score * _SETASIDE_WEIGHT
        if sa_matches > 0:
            gaps_filled.append(f"Set-aside: {', '.join(kw for kw in sa_keywords if kw in partner_set_asides)}")

    # Risk assessment
    if not partner_caps:
        risks.append("No capabilities documented")
    if partner.get("status") == "prospect":
        risks.append("Partner not yet confirmed (prospect status)")

    # Recommendation — thresholds are instance-level (patched per run from anomaly detection)
    if score >= _FIT_STRONG_THRESHOLD:
        recommendation = "strong_fit"
    elif score >= _FIT_GOOD_THRESHOLD:
        recommendation = "good_fit"
    elif score >= _FIT_MARGINAL_THRESHOLD:
        recommendation = "marginal"
    else:
        recommendation = "not_recommended"

    return {
        "fit_score": round(score, 3),
        "capability_gaps_filled": gaps_filled,
        "risk_assessment": risks,
        "recommendation": recommendation,
    }


def _store_assessment(opp_id: str, partner_id: str, assessment: Dict) -> Optional[str]:
    """Store teaming assessment in DB."""
    conn = get_connection()
    assessment_id = _generate_id("pgta")
    try:
        conn.execute(
            """
            INSERT INTO pg_teaming_assessments
                (id, opportunity_id, partner_id, fit_score,
                 capability_gaps_filled, risk_assessment,
                 recommendation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                assessment_id,
                opp_id,
                partner_id,
                assessment["fit_score"],
                json.dumps(assessment["capability_gaps_filled"]),
                json.dumps(assessment["risk_assessment"]),
                assessment["recommendation"],
                _utcnow_iso(),
            ),
        )
        conn.commit()
        return assessment_id
    except Exception:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Capture plan teaming strategy update
# ---------------------------------------------------------------------------


def _update_teaming_strategy(opp_id: str) -> None:
    """Update capture plan's teaming_strategy from assessment results."""
    conn = get_connection()
    try:
        assessments = conn.execute(
            "SELECT ta.*, tp.name as partner_name "
            "FROM pg_teaming_assessments ta "
            "INNER JOIN pg_teaming_partners tp ON tp.id = ta.partner_id "
            "WHERE ta.opportunity_id = ? "
            "ORDER BY ta.fit_score DESC",
            (opp_id,),
        ).fetchall()

        if not assessments:
            return

        strong = [a for a in assessments if a["recommendation"] == "strong_fit"]
        good = [a for a in assessments if a["recommendation"] == "good_fit"]

        strategy_parts = []
        if strong:
            names = ", ".join(a["partner_name"] for a in strong[:3])
            strategy_parts.append(f"Recommended prime/sub partners: {names}")
        if good:
            names = ", ".join(a["partner_name"] for a in good[:3])
            strategy_parts.append(f"Secondary options: {names}")

        strategy = "; ".join(strategy_parts) if strategy_parts else "No strong teaming matches found"

        conn.execute(
            "UPDATE pg_capture_plans SET teaming_strategy = ?, updated_at = ? WHERE opportunity_id = ?",
            (strategy, _utcnow_iso(), opp_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Shape Reflex (R3).

    Steps:
      1. Find opportunities needing capture plans → create skeleton plans
      2. Get active teaming partners
      3. Assess partner fit for unassessed opportunities
      4. Update capture plan teaming strategies
      5. Audit all decisions

    Returns standard reflex result dict.
    """
    # Compute adaptive fit thresholds from historical assessment distribution.
    anomaly_cfg = config.get("anomaly_detection", {})
    thresholds = _compute_fit_thresholds(anomaly_cfg)
    # Patch module-level globals so _assess_partner_fit() uses computed values.
    global _FIT_STRONG_THRESHOLD, _FIT_GOOD_THRESHOLD, _FIT_MARGINAL_THRESHOLD  # noqa: PLW0603
    _FIT_STRONG_THRESHOLD   = thresholds["strong"]
    _FIT_GOOD_THRESHOLD     = thresholds["good"]
    _FIT_MARGINAL_THRESHOLD = thresholds["marginal"]

    plans_created = 0
    assessments_made = 0
    errors = 0

    # Step 1: Create capture plans for new opportunities
    opps_needing_plans = _get_opportunities_needing_plans()
    for opp in opps_needing_plans:
        plan_id = _create_capture_plan(opp)
        if plan_id:
            plans_created += 1
        else:
            errors += 1

    # Step 2: Get partners
    partners = _get_partners()

    # Step 3: Assess teaming for unassessed opportunities
    unassessed = _get_unassessed_opportunities()
    for opp in unassessed:
        for partner in partners:
            assessment = _assess_partner_fit(opp, partner)
            # Only store if at least marginal fit
            if assessment["recommendation"] != "not_recommended":
                result = _store_assessment(opp["opportunity_id"], partner["id"], assessment)
                if result:
                    assessments_made += 1

        # Step 4: Update teaming strategy on capture plan
        _update_teaming_strategy(opp["opportunity_id"])

    total_updated = plans_created + assessments_made

    return {
        "success": True,
        "metric_value": float(total_updated),
        "details": {
            "plans_created": plans_created,
            "plans_updated": total_updated,
            "assessments_made": assessments_made,
            "partners_evaluated": len(partners),
            "opportunities_assessed": len(unassessed),
            "errors": errors,
        },
    }
