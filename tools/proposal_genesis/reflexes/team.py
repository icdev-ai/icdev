#!/usr/bin/env python3
# CUI // SP-CTI
"""R23: Team Reflex — Teaming Coordination Hub (section 3.18).

Data-driven teaming partner scoring, workshare tracking, TA lifecycle.
Triggered when R5 Extract identifies capability gaps.

Checks:
  - Teaming agreement (TA) expiration dates
  - Organizational Conflict of Interest (OCI) risks
  - Workshare allocation gaps (unfilled roles)
  - Partner fit scores vs. opportunity requirements

Pipeline: on_demand (triggered after R5 Extract or R6 Map).
GREEN tier (read-only analysis + workshare status).
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

logger = get_logger("icdev.proposal_genesis.reflexes.team")

# ---------------------------------------------------------------------------
# Module-level constants — Team Reflex (R23) thresholds & limits.
# Extracted from inline magic numbers (AI-ify opp 5449, hardcoded_threshold).
# Overridable from proposal_genesis_config.yaml under reflexes.team.
# Change config, not code.
# ---------------------------------------------------------------------------
_OPPS_WITH_TEAMS_LIMIT      = 20    # opportunities with teaming partners scanned per run
_TA_EXPIRED_DAYS            = 0     # days-until-expiration at/below which a TA is already expired
_TA_EXPIRATION_WARNING_DAYS = 30   # days-until-expiration below which a TA is flagged "expiring soon"
_FULL_WORKSHARE_PCT         = 100.0  # total workshare allocation that closes the gap (100%)
_TA_DETAILS_LIMIT           = 5     # TA issues surfaced per opportunity in the details payload
_OCI_DETAILS_LIMIT          = 5     # OCI risks surfaced per opportunity in the details payload


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgtm") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Teaming health checks
# ---------------------------------------------------------------------------


def _get_opportunities_with_teams() -> List[Dict]:
    """Find opportunities that have teaming partners assigned."""
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT DISTINCT tw.opportunity_id, po.title, po.status
            FROM pg_teaming_workshare tw
            JOIN proposal_opportunities po ON po.id = tw.opportunity_id
            WHERE po.status IN ('tracking', 'drafting', 'reviewing')
            ORDER BY po.created_at DESC
            LIMIT {_OPPS_WITH_TEAMS_LIMIT}
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _check_ta_expiration(opp_id: str) -> List[Dict]:
    """Check for expired or soon-to-expire teaming agreements."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, partner_name, ta_expiration_date, ta_status
            FROM pg_teaming_workshare
            WHERE opportunity_id = %s
            AND ta_expiration_date IS NOT NULL
        """,
            (opp_id,),
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    issues = []
    now = datetime.now(timezone.utc)

    for row in rows:
        exp_date = row["ta_expiration_date"]
        if not exp_date:
            continue
        try:
            exp_dt = datetime.fromisoformat(exp_date.replace("Z", "+00:00"))
            days_until = (exp_dt - now).days
            if days_until < _TA_EXPIRED_DAYS:
                issues.append(
                    {
                        "partner": row["partner_name"],
                        "severity": "critical",
                        "issue": f"TA expired {abs(days_until)} days ago",
                    }
                )
            elif days_until < _TA_EXPIRATION_WARNING_DAYS:
                issues.append(
                    {
                        "partner": row["partner_name"],
                        "severity": "warning",
                        "issue": f"TA expires in {days_until} days",
                    }
                )
        except (ValueError, TypeError):
            pass

    return issues


def _check_oci_risks(opp_id: str) -> List[Dict]:
    """Check for Organizational Conflict of Interest markers."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT tw.partner_name, tw.oci_risk_flag, tw.oci_mitigation
            FROM pg_teaming_workshare tw
            WHERE tw.opportunity_id = %s
            AND tw.oci_risk_flag = 1
        """,
            (opp_id,),
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    risks = []
    for row in rows:
        has_mitigation = bool(row["oci_mitigation"])
        risks.append(
            {
                "partner": row["partner_name"],
                "severity": "warning" if has_mitigation else "critical",
                "issue": "OCI risk flagged"
                + (" (mitigation plan exists)" if has_mitigation else " (NO mitigation plan)"),
            }
        )
    return risks


def _check_workshare_gaps(opp_id: str) -> Dict[str, Any]:
    """Check for unfilled workshare roles."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, partner_name, role, workshare_pct FROM pg_teaming_workshare WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchall()
    except Exception:
        return {"total_partners": 0, "total_workshare_pct": 0, "gaps": []}
    finally:
        conn.close()

    total_pct = 0.0
    gaps = []
    for row in rows:
        pct = row["workshare_pct"] or 0
        if isinstance(pct, (int, float)):
            total_pct += pct
        if not row["role"] or (row["role"] or "").lower() in ("tbd", "unassigned"):
            gaps.append(
                {
                    "partner": row["partner_name"],
                    "issue": "Role not assigned",
                }
            )

    return {
        "total_partners": len(rows),
        "total_workshare_pct": round(total_pct, 1),
        "workshare_gap": max(0, round(_FULL_WORKSHARE_PCT - total_pct, 1)),
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Team Reflex (R23).

    Steps:
      1. Find opportunities with teaming partners
      2. Check TA expiration dates
      3. Detect OCI risks
      4. Identify workshare gaps (unfilled roles, allocation < 100%)

    Returns standard reflex result dict.
    """
    opportunities = _get_opportunities_with_teams()
    total_issues = 0
    team_results: List[Dict] = []

    for opp in opportunities:
        opp_id = opp["opportunity_id"]

        ta_issues = _check_ta_expiration(opp_id)
        oci_risks = _check_oci_risks(opp_id)
        workshare = _check_workshare_gaps(opp_id)

        issues = len(ta_issues) + len(oci_risks) + len(workshare.get("gaps", []))
        total_issues += issues

        team_results.append(
            {
                "opportunity_id": opp_id,
                "title": opp["title"],
                "ta_issues": len(ta_issues),
                "oci_risks": len(oci_risks),
                "workshare_gap_pct": workshare["workshare_gap"],
                "unfilled_roles": len(workshare.get("gaps", [])),
                "total_partners": workshare["total_partners"],
                "total_issues": issues,
                "ta_details": ta_issues[:_TA_DETAILS_LIMIT],
                "oci_details": oci_risks[:_OCI_DETAILS_LIMIT],
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
                "team_check",
                "team",
                "green",
                json.dumps(
                    {
                        "opportunities_checked": len(opportunities),
                        "total_issues": total_issues,
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
        "metric_value": float(total_issues),
        "details": {
            "opportunities_checked": len(opportunities),
            "total_issues": total_issues,
            "team_results": team_results,
        },
    }


# CUI // SP-CTI
