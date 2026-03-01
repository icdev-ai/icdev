# CUI // SP-CTI
# ICDEV GovCon Compliance Populator — Phase 59 (D365)
# Auto-populate L/M/N compliance matrix from capability coverage scores.

"""
Compliance Populator — auto-mark compliance matrix for proposals.

Coverage thresholds (from govcon_config.yaml):
    >= 0.80  →  L (compliant)
    0.40–0.79 → M (partial)
    < 0.40   →  N (non-compliant / gap)

Works with existing proposal_compliance_items table from GovProposal.

Usage:
    python tools/govcon/compliance_populator.py --populate --opp-id <id> --json
    python tools/govcon/compliance_populator.py --summary --opp-id <id> --json
    python tools/govcon/compliance_populator.py --export-matrix --opp-id <id> --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))


# ── helpers ───────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(conn, action, details="", actor="compliance_populator"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), _now(), "govcon.compliance_matrix", actor, action, details, "govcon"),
        )
    except Exception:
        pass


# ── compliance matrix population ──────────────────────────────────────

def populate_compliance_matrix(opportunity_id):
    """Auto-populate L/M/N compliance matrix for an opportunity.

    For each shall statement:
    1. Get best capability coverage from capability_mapper
    2. Map to L/M/N grade
    3. Store/update in proposal_compliance_items (if table exists)
    4. Return full matrix
    """
    from tools.govcon.capability_mapper import get_compliance_matrix

    matrix_result = get_compliance_matrix(opportunity_id)

    if matrix_result.get("status") != "ok":
        return matrix_result

    conn = _get_db()

    # Check if proposal_compliance_items table exists (from GovProposal)
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='proposal_compliance_items'"
    ).fetchone()

    populated = 0
    if has_table:
        # Find the proposal section for this opportunity
        for item in matrix_result.get("matrix", []):
            try:
                # Insert or update compliance item
                existing = conn.execute(
                    "SELECT id FROM proposal_compliance_items WHERE requirement_text = ? AND section_id IN "
                    "(SELECT id FROM proposal_sections WHERE opportunity_id = ?)",
                    (item["statement"][:200], opportunity_id),
                ).fetchone()

                if not existing:
                    conn.execute(
                        "INSERT INTO proposal_compliance_items "
                        "(id, section_id, requirement_id, requirement_text, compliance_status, "
                        "compliance_notes, evidence_reference, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "",  # Will be linked when section is created
                            item.get("shall_id", ""),
                            item["statement"][:500],
                            item["grade"],
                            f"Auto-populated: {item['best_capability']} (score={item['coverage_score']:.2f})",
                            item.get("evidence", "")[:500],
                            _now(), _now(),
                        ),
                    )
                    populated += 1
            except Exception:
                pass

    _audit(conn, "populate_matrix",
           f"Opportunity {opportunity_id}: L={matrix_result['L_compliant']} M={matrix_result['M_partial']} N={matrix_result['N_gap']}")
    conn.commit()
    conn.close()

    matrix_result["populated_items"] = populated
    return matrix_result


def get_summary(opportunity_id):
    """Get compliance summary for an opportunity."""
    from tools.govcon.capability_mapper import get_compliance_matrix

    matrix_result = get_compliance_matrix(opportunity_id)
    if matrix_result.get("status") != "ok":
        return matrix_result

    # Domain breakdown
    domains = {}
    for item in matrix_result.get("matrix", []):
        d = item.get("domain", "unknown")
        if d not in domains:
            domains[d] = {"L": 0, "M": 0, "N": 0, "total": 0}
        domains[d][item["grade"]] += 1
        domains[d]["total"] += 1

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "overall": {
            "total": matrix_result["total_requirements"],
            "L": matrix_result["L_compliant"],
            "M": matrix_result["M_partial"],
            "N": matrix_result["N_gap"],
            "compliance_rate": matrix_result.get("compliance_rate", 0),
        },
        "by_domain": domains,
        "bid_recommendation": _bid_recommendation(matrix_result),
    }


def _bid_recommendation(matrix_result):
    """Generate bid/no-bid recommendation based on compliance coverage."""
    total = matrix_result.get("total_requirements", 0)
    if total == 0:
        return {"decision": "insufficient_data", "reason": "No requirements extracted"}

    l_rate = matrix_result["L_compliant"] / total
    n_rate = matrix_result["N_gap"] / total

    if l_rate >= 0.70 and n_rate <= 0.10:
        return {
            "decision": "strong_bid",
            "reason": f"{l_rate:.0%} compliant, only {n_rate:.0%} gaps. Strong capability alignment.",
            "confidence": "high",
        }
    elif l_rate >= 0.50 and n_rate <= 0.25:
        return {
            "decision": "bid_with_gaps",
            "reason": f"{l_rate:.0%} compliant, {n_rate:.0%} gaps. Address gaps via teaming or enhancement.",
            "confidence": "medium",
        }
    elif l_rate >= 0.30:
        return {
            "decision": "conditional_bid",
            "reason": f"Only {l_rate:.0%} compliant. Significant gaps. Consider teaming partner.",
            "confidence": "low",
        }
    else:
        return {
            "decision": "no_bid",
            "reason": f"Only {l_rate:.0%} compliant with {n_rate:.0%} gaps. Poor alignment.",
            "confidence": "high",
        }


def export_matrix(opportunity_id):
    """Export compliance matrix in tabular format."""
    from tools.govcon.capability_mapper import get_compliance_matrix

    matrix_result = get_compliance_matrix(opportunity_id)
    if matrix_result.get("status") != "ok":
        return matrix_result

    # Format as exportable table
    rows = []
    for i, item in enumerate(matrix_result.get("matrix", []), 1):
        rows.append({
            "row": i,
            "requirement": item["statement"],
            "domain": item["domain"],
            "type": item["statement_type"],
            "grade": item["grade"],
            "capability": item["best_capability"],
            "score": item["coverage_score"],
            "evidence": item["evidence"],
        })

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "headers": ["#", "Requirement", "Domain", "Type", "L/M/N", "Capability", "Score", "Evidence"],
        "rows": rows,
        "summary": {
            "L": matrix_result["L_compliant"],
            "M": matrix_result["M_partial"],
            "N": matrix_result["N_gap"],
            "rate": matrix_result.get("compliance_rate", 0),
        },
    }


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovCon Compliance Populator (D365)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--populate", action="store_true", help="Auto-populate compliance matrix")
    group.add_argument("--summary", action="store_true", help="Compliance summary with bid recommendation")
    group.add_argument("--export-matrix", action="store_true", help="Export matrix in tabular format")

    parser.add_argument("--opp-id", required=True, help="Opportunity ID")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    if args.populate:
        result = populate_compliance_matrix(args.opp_id)
    elif args.summary:
        result = get_summary(args.opp_id)
    elif args.export_matrix:
        result = export_matrix(args.opp_id)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        print(f"\n{'=' * 60}")
        if "overall" in result:
            o = result["overall"]
            print(f"  Compliance Summary — {args.opp_id}")
            print(f"  L={o['L']} M={o['M']} N={o['N']}  Rate={o['compliance_rate']:.0%}")
            if "bid_recommendation" in result:
                rec = result["bid_recommendation"]
                print(f"\n  Recommendation: {rec['decision'].upper()}")
                print(f"  Reason: {rec['reason']}")
        elif "rows" in result:
            for row in result["rows"]:
                g = row["grade"]
                icon = {"L": "✅", "M": "⚠️", "N": "❌"}.get(g, "?")
                print(f"  {icon} [{g}] {row['requirement'][:50]:50s} → {row['capability'][:25]}")
        print()
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
