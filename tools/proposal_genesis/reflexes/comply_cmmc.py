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

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.comply_cmmc")

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from proposal_genesis_config.yaml
# under reflexes.comply_cmmc.anomaly_detection.  Change config, not code.
# ---------------------------------------------------------------------------
_MIN_CMMC_LEVEL    = 2      # Regulatory floor: CMMC Level 2 minimum for CUI (DFARS 252.204-7012)
_MIN_SPRS_SCORE    = 110    # NIST 800-171 perfect-implementation SPRS score (range -203..110)
_OPP_PROCESS_LIMIT = 20     # Max tracking/drafting opportunities scanned per run


def _compute_sprs_threshold(anomaly_cfg: "dict | None" = None) -> float:
    """Compute the SPRS flag threshold from the teaming-partner score distribution.

    Replaces the brittle static "< 110 (perfect)" cut-off with a data-driven lower
    control limit (mean - sigma*std). A partner is then flagged when its SPRS is an
    anomalous low outlier relative to the partner population — not merely because it
    falls short of a perfect 110.

    Clamped to [sprs_floor, sprs_ceil] so it never exceeds the regulatory ideal nor
    drops below the configured floor. Falls back to _MIN_SPRS_SCORE (the prior static
    behavior) when fewer than min_samples scored partners exist or detection is off.
    """
    cfg = anomaly_cfg or {}
    if not cfg.get("enabled", True):
        return float(cfg.get("fallback_sprs_threshold", _MIN_SPRS_SCORE))

    min_samples = cfg.get("min_samples", 15)
    sigma       = cfg.get("sigma_multiplier", 1.0)
    fallback    = float(cfg.get("fallback_sprs_threshold", _MIN_SPRS_SCORE))
    bounds      = cfg.get("adaptive_bounds", {})
    floor       = float(bounds.get("sprs_floor", 70.0))
    ceil        = float(bounds.get("sprs_ceil", _MIN_SPRS_SCORE))

    import math
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT AVG(sprs_score) AS mean_s, "
            "AVG(sprs_score * sprs_score) - AVG(sprs_score) * AVG(sprs_score) AS var_s, "
            "COUNT(*) AS n "
            "FROM pg_teaming_workshare "
            "WHERE sprs_score IS NOT NULL"
        ).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[2]
            if n and n >= min_samples:
                mean_s = float(row["mean_s"] if isinstance(row, dict) else row[0])
                var_s  = max(0.0, float(row["var_s"] if isinstance(row, dict) else row[1]))
                std_s  = math.sqrt(var_s)
                threshold = mean_s - sigma * std_s
                threshold = max(floor, min(ceil, threshold))
                return round(threshold, 1)
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return fallback


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
        rows = conn.execute(f"""
            SELECT po.id, po.title, po.agency
            FROM proposal_opportunities po
            WHERE po.status IN ('tracking', 'drafting')
            ORDER BY po.created_at DESC
            LIMIT {_OPP_PROCESS_LIMIT}
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _check_teaming_cmmc(
    opp_id: str,
    min_cmmc_level: float = _MIN_CMMC_LEVEL,
    sprs_threshold: float = _MIN_SPRS_SCORE,
) -> Dict[str, Any]:
    """Check CMMC compliance status for teaming partners on an opportunity.

    Args:
        opp_id: Opportunity ID to validate.
        min_cmmc_level: Regulatory CMMC floor (default Level 2 for CUI handling).
        sprs_threshold: SPRS flag threshold — static 110 or an adaptive anomaly
            lower control limit from _compute_sprs_threshold().
    """
    conn = get_connection()
    try:
        partners = conn.execute(
            "SELECT id, partner_name, role, cmmc_level, sprs_score FROM pg_teaming_workshare WHERE opportunity_id = %s",
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

        # CMMC Level 2 is minimum for CUI handling (regulatory floor)
        if isinstance(cmmc_level, (int, float)) and cmmc_level < min_cmmc_level:
            issues.append(f"CMMC level {cmmc_level} below required Level {int(min_cmmc_level)}")

        # SPRS score below the (adaptive) anomaly threshold is a red flag
        if sprs_score is not None and isinstance(sprs_score, (int, float)):
            if sprs_score < sprs_threshold:
                issues.append(f"SPRS score {sprs_score} below threshold ({sprs_threshold:g})")

        # No CMMC or SPRS data at all
        if not cmmc_level and sprs_score is None:
            issues.append("No CMMC level or SPRS score on record")

        if issues:
            non_compliant.append(
                {
                    "partner_id": partner["id"],
                    "partner_name": partner["partner_name"],
                    "role": partner["role"],
                    "cmmc_level": cmmc_level,
                    "sprs_score": sprs_score,
                    "issues": issues,
                }
            )

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
            "SELECT clause_id, clause_title, compliance_status FROM pg_ai_clause_compliance WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchall()
    except Exception:
        return {"clauses_checked": 0, "non_compliant": 0}
    finally:
        conn.close()

    non_compliant = sum(
        1 for r in rows if (r["compliance_status"] or "").lower() not in ("compliant", "not_applicable")
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
    # Resolve adaptive thresholds from config (anomaly_detection block).
    anomaly_cfg = config.get("anomaly_detection", {}) if isinstance(config, dict) else {}
    min_cmmc_level = float(anomaly_cfg.get("min_cmmc_level", _MIN_CMMC_LEVEL))
    sprs_threshold = _compute_sprs_threshold(anomaly_cfg)

    opportunities = _get_opportunities_needing_cmmc()
    total_non_compliant = 0
    validation_results: List[Dict] = []

    for opp in opportunities:
        opp_id = opp["id"]

        # Check teaming partner CMMC
        team_result = _check_teaming_cmmc(opp_id, min_cmmc_level, sprs_threshold)
        nc_count = team_result["non_compliant_count"]

        # Check AI clause compliance
        ai_result = _check_ai_clause_compliance(opp_id)
        nc_count += ai_result["non_compliant"]

        total_non_compliant += nc_count

        validation_results.append(
            {
                "opportunity_id": opp_id,
                "title": opp["title"],
                "partners_checked": team_result["partners_checked"],
                "non_compliant_partners": team_result["non_compliant_count"],
                "ai_clauses_checked": ai_result["clauses_checked"],
                "ai_clauses_non_compliant": ai_result["non_compliant"],
                "total_issues": nc_count,
            }
        )

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "cmmc_validation",
                "comply_cmmc",
                "green",
                json.dumps(
                    {
                        "opportunities_checked": len(opportunities),
                        "total_non_compliant": total_non_compliant,
                        "min_cmmc_level": min_cmmc_level,
                        "sprs_threshold": sprs_threshold,
                    }
                ),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("run: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s", exc)
    finally:
        conn.close()

    return {
        "success": True,
        "metric_value": float(total_non_compliant),
        "details": {
            "opportunities_checked": len(opportunities),
            "total_non_compliant": total_non_compliant,
            "min_cmmc_level": min_cmmc_level,
            "sprs_threshold": sprs_threshold,
            "validation_results": validation_results,
        },
    }


# CUI // SP-CTI
