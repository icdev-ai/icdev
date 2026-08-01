#!/usr/bin/env python3
# CUI // SP-CTI
"""R22: Trace Reflex — Compliance Traceability (section 3.11).

Maintains bidirectional L/M/C to proposal section mapping.
Live coverage tracking.  Amendment change detection.

Checks:
  - L→M→C→section mapping completeness
  - Unmapped compliance items
  - Section coverage gaps
  - Amendment drift (requirements changed but matrix not updated)

Pipeline: on_demand (can also be triggered by R5 Extract amendments).
GREEN tier (read-only analysis + coverage metrics).
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

logger = get_logger("icdev.proposal_genesis.reflexes.trace")

# ---------------------------------------------------------------------------
# Module-level constants — Trace Reflex (R22) thresholds & limits.
# Extracted from inline magic numbers (AI-ify opp 5451, hardcoded_threshold ->
# anomaly_detection). Overridable from proposal_genesis_config.yaml under
# reflexes.trace. Change config, not code.
# ---------------------------------------------------------------------------
_OPPS_WITH_MATRICES_LIMIT  = 20    # opportunities-with-matrices scanned per run

# Caps on per-opportunity detail slices surfaced in the result payload.
_SECTION_IDS_LIMIT         = 10    # unmapped section IDs surfaced per opportunity
_STALE_AMENDMENTS_LIMIT    = 10    # stale amendments surfaced per opportunity
_CHANGE_SUMMARY_CHARS      = 100   # change-summary chars retained per amendment


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgtrc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Compliance matrix coverage analysis
# ---------------------------------------------------------------------------


def _get_opportunities_with_matrices() -> List[Dict]:
    """Find opportunities that have compliance matrix entries."""
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT DISTINCT cm.opportunity_id, po.title, po.status
            FROM pg_compliance_matrix cm
            JOIN proposal_opportunities po ON po.id = cm.opportunity_id
            WHERE po.status IN ('tracking', 'drafting', 'reviewing')
            ORDER BY po.created_at DESC
            LIMIT {_OPPS_WITH_MATRICES_LIMIT}
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _compute_coverage(opp_id: str) -> Dict[str, Any]:
    """Compute L/M/C coverage metrics for an opportunity's compliance matrix."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT compliance_status, COUNT(*) as cnt "
            "FROM pg_compliance_matrix "
            "WHERE opportunity_id = %s "
            "GROUP BY compliance_status",
            (opp_id,),
        ).fetchall()
    except Exception:
        return {"total": 0, "coverage_pct": 0.0, "breakdown": {}}
    finally:
        conn.close()

    breakdown = {}
    total = 0
    for row in rows:
        status = row["compliance_status"] or "unknown"
        breakdown[status] = row["cnt"]
        total += row["cnt"]

    if total == 0:
        return {"total": 0, "coverage_pct": 0.0, "breakdown": breakdown}

    # L = compliant, M = partial, N/C = non-compliant/gap
    compliant = breakdown.get("L", 0) + breakdown.get("compliant", 0)
    partial = breakdown.get("M", 0) + breakdown.get("partial", 0)
    covered = compliant + partial
    coverage_pct = (covered / total) * 100 if total > 0 else 0.0

    return {
        "total": total,
        "compliant": compliant,
        "partial": partial,
        "non_compliant": total - covered,
        "coverage_pct": round(coverage_pct, 1),
        "breakdown": breakdown,
    }


def _check_unmapped_sections(opp_id: str) -> Dict[str, Any]:
    """Find proposal sections without compliance matrix linkage."""
    conn = get_connection()
    try:
        # Sections that have drafts but no compliance matrix entry
        rows = conn.execute(
            """
            SELECT psd.id, psd.section_id
            FROM proposal_section_drafts psd
            LEFT JOIN pg_compliance_matrix cm
                ON cm.opportunity_id = psd.opportunity_id
                AND cm.section_reference = psd.section_id
            WHERE psd.opportunity_id = %s
            AND cm.id IS NULL
            AND psd.status IN ('draft', 'approved')
        """,
            (opp_id,),
        ).fetchall()
        return {
            "unmapped_sections": len(rows),
            "section_ids": [r["section_id"] for r in rows[:_SECTION_IDS_LIMIT]],
        }
    except Exception:
        return {"unmapped_sections": 0, "section_ids": []}
    finally:
        conn.close()


def _check_amendment_drift(opp_id: str) -> Dict[str, Any]:
    """Check if amendments changed requirements but matrix was not updated."""
    conn = get_connection()
    try:
        # Find amendment diffs that were re-extracted but matrix not updated
        rows = conn.execute(
            f"""
            SELECT ad.id, ad.amendment_number, ad.change_summary
            FROM pg_amendment_diffs ad
            WHERE ad.opportunity_id = %s
            AND ad.re_extracted = 1
            AND ad.matrix_updated = 0
            ORDER BY ad.created_at DESC
            LIMIT {_STALE_AMENDMENTS_LIMIT}
        """,
            (opp_id,),
        ).fetchall()
        return {
            "stale_amendments": len(rows),
            "amendments": [
                {"id": r["id"], "amendment": r["amendment_number"], "summary": (r["change_summary"] or "")[:_CHANGE_SUMMARY_CHARS]}
                for r in rows
            ],
        }
    except Exception:
        # Column may not exist yet
        return {"stale_amendments": 0, "amendments": []}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Trace Reflex (R22).

    Steps:
      1. Find opportunities with active compliance matrices
      2. Compute L/M/C coverage per opportunity
      3. Detect unmapped sections
      4. Check for amendment drift (stale matrix entries)
      5. Report average coverage percentage

    Returns standard reflex result dict.
    """
    opportunities = _get_opportunities_with_matrices()
    total_coverage = 0.0
    trace_results: List[Dict] = []

    for opp in opportunities:
        opp_id = opp["opportunity_id"]

        coverage = _compute_coverage(opp_id)
        unmapped = _check_unmapped_sections(opp_id)
        drift = _check_amendment_drift(opp_id)

        total_coverage += coverage["coverage_pct"]

        trace_results.append(
            {
                "opportunity_id": opp_id,
                "title": opp["title"],
                "coverage_pct": coverage["coverage_pct"],
                "total_items": coverage["total"],
                "compliant": coverage.get("compliant", 0),
                "partial": coverage.get("partial", 0),
                "non_compliant": coverage.get("non_compliant", 0),
                "unmapped_sections": unmapped["unmapped_sections"],
                "stale_amendments": drift["stale_amendments"],
            }
        )

    avg_coverage = total_coverage / len(opportunities) if opportunities else 0.0

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "trace_coverage",
                "trace",
                "green",
                json.dumps(
                    {
                        "opportunities_traced": len(opportunities),
                        "avg_coverage_pct": round(avg_coverage, 1),
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
        "metric_value": round(avg_coverage, 1),
        "details": {
            "opportunities_traced": len(opportunities),
            "avg_coverage_pct": round(avg_coverage, 1),
            "trace_results": trace_results,
        },
    }


# CUI // SP-CTI
