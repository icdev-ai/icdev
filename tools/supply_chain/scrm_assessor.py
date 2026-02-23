#!/usr/bin/env python3
# CUI // SP-CTI
"""NIST 800-161 Supply Chain Risk Management (SCRM) Assessor.

Scores vendors across 6 SCRM dimensions (provenance, integrity, dependency,
substitutability, access control, incident history), computes weighted risk,
maps to NIST 800-161 controls, and stores results in scrm_assessments.

Tables used:
  - supply_chain_vendors      (read vendor data)
  - supply_chain_dependencies (read dependency counts)
  - scrm_assessments          (write assessment results)

CLI:
  python tools/supply_chain/scrm_assessor.py --project-id <id> --vendor-id <id> --json
  python tools/supply_chain/scrm_assessor.py --project-id <id> --aggregate --json
  python tools/supply_chain/scrm_assessor.py --project-id <id> --prohibited --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Country risk tiers for provenance scoring
FIVE_EYES = {"US", "USA", "GB", "GBR", "UK", "CA", "CAN", "AU", "AUS", "NZ", "NZL"}
ALLIED = {"DE", "DEU", "FR", "FRA", "JP", "JPN", "KR", "KOR", "IL", "ISR",
          "IT", "ITA", "NL", "NLD", "NO", "NOR", "DK", "DNK", "SE", "SWE",
          "FI", "FIN", "ES", "ESP", "PL", "POL", "CZ", "CZE", "BE", "BEL",
          "PT", "PRT", "AT", "AUT", "IE", "IRL", "LU", "LUX", "EE", "EST",
          "LV", "LVA", "LT", "LTU", "TW", "TWN", "SG", "SGP"}
ADVERSARY = {"CN", "CHN", "RU", "RUS", "IR", "IRN", "KP", "PRK", "CU", "CUB",
             "SY", "SYR", "VE", "VEN", "BY", "BLR", "MM", "MMR"}

# Dimension weights for overall score (sum = 1.0)
WEIGHTS = {
    "provenance": 0.25,
    "integrity": 0.20,
    "dependency": 0.20,
    "substitutability": 0.10,
    "access_control": 0.15,
    "incident_history": 0.10,
}

# NIST 800-161 controls applicable by risk dimension
NIST_161_CONTROLS = {
    "provenance": ["SR-1", "SR-3", "SR-5", "SR-6", "SR-11"],
    "integrity": ["SR-4", "SR-10", "SR-11", "SI-7"],
    "dependency": ["SR-2", "SR-3", "SR-5", "RA-3(1)", "RA-9"],
    "substitutability": ["SR-2", "SR-3", "CP-8"],
    "access_control": ["SR-3", "SR-5", "AC-20", "SA-9"],
    "incident_history": ["SR-6", "SR-8", "IR-6"],
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Return a sqlite3 connection with Row factory."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _log_audit(conn, project_id, event_type, action, details):
    """Append-only audit trail entry."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, event_type, "icdev-supply-chain-agent", action,
             json.dumps(details) if isinstance(details, dict) else str(details),
             "CUI"),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def _score_provenance(country_of_origin):
    """Score based on country of origin risk (0-10, higher = safer).

    Five Eyes = 10, Allied = 7, Unknown/Other = 4, Adversary = 1.
    """
    if not country_of_origin:
        return 4.0
    c = country_of_origin.upper().strip()
    if c in FIVE_EYES:
        return 10.0
    if c in ALLIED:
        return 7.0
    if c in ADVERSARY:
        return 1.0
    return 4.0


def _score_integrity(section_889_status):
    """Score based on Section 889 compliance (0-10).

    compliant = 10, exempt = 8, under_review = 5, prohibited = 0.
    """
    mapping = {
        "compliant": 10.0,
        "exempt": 8.0,
        "under_review": 5.0,
        "prohibited": 0.0,
    }
    return mapping.get(section_889_status, 5.0)


def _score_dependency(conn, project_id, vendor_id):
    """Score based on how many components depend on this vendor (0-10).

    Fewer dependencies = higher score (less concentration risk).
    0 deps = 10, 1-2 = 8, 3-5 = 6, 6-10 = 4, 11+ = 2.
    """
    # Count dependencies linked to this vendor via metadata
    count = 0
    rows = conn.execute(
        """SELECT metadata FROM supply_chain_dependencies
           WHERE project_id = ? AND metadata IS NOT NULL""",
        (project_id,),
    ).fetchall()
    for r in rows:
        try:
            meta = json.loads(r["metadata"])
            if meta.get("vendor_id") == vendor_id:
                count += 1
        except (json.JSONDecodeError, TypeError):
            pass

    # Also count direct vendor-type dependencies
    count += conn.execute(
        """SELECT COUNT(*) FROM supply_chain_dependencies
           WHERE project_id = ?
             AND ((source_type = 'vendor' AND source_id = ?)
               OR (target_type = 'vendor' AND target_id = ?))""",
        (project_id, vendor_id, vendor_id),
    ).fetchone()[0]

    if count == 0:
        return 10.0
    if count <= 2:
        return 8.0
    if count <= 5:
        return 6.0
    if count <= 10:
        return 4.0
    return 2.0


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def assess_vendor(project_id, vendor_id, db_path=None):
    """Perform a NIST 800-161 SCRM assessment on a single vendor.

    Scores 6 dimensions, computes weighted overall score, maps to risk tier,
    identifies applicable NIST 800-161 controls, generates recommendations,
    and stores the assessment in scrm_assessments.

    Returns:
        dict with assessment_id, vendor_name, overall_score, risk_tier,
        dimension_scores, nist_161_controls, recommendations.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM supply_chain_vendors WHERE id = ? AND project_id = ?",
            (vendor_id, project_id),
        ).fetchone()
        if not row:
            raise ValueError(
                f"Vendor '{vendor_id}' not found in project '{project_id}'.")
        vendor = dict(row)

        # Compute dimension scores
        provenance = _score_provenance(vendor.get("country_of_origin"))
        integrity = _score_integrity(vendor.get("section_889_status"))
        dependency = _score_dependency(conn, project_id, vendor_id)
        substitutability = 5.0   # placeholder: requires manual assessment
        access_control = 5.0     # placeholder: requires access review data
        incident_history = 7.0   # placeholder: requires incident feed

        scores = {
            "provenance": provenance,
            "integrity": integrity,
            "dependency": dependency,
            "substitutability": substitutability,
            "access_control": access_control,
            "incident_history": incident_history,
        }

        # Weighted average
        overall = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)
        overall = round(overall, 2)

        # Map to risk tier
        if overall >= 8.0:
            risk_tier = "low"
        elif overall >= 6.0:
            risk_tier = "moderate"
        elif overall >= 4.0:
            risk_tier = "high"
        else:
            risk_tier = "critical"

        # Map to likelihood/impact for DB storage
        likelihood_map = {"low": "very_low", "moderate": "low",
                          "high": "moderate", "critical": "very_high"}
        impact_map = {"low": "low", "moderate": "moderate",
                      "high": "high", "critical": "very_high"}
        likelihood = likelihood_map.get(risk_tier, "moderate")
        impact = impact_map.get(risk_tier, "moderate")

        # Collect applicable NIST 800-161 controls
        all_controls = set()
        for dim, score in scores.items():
            if score < 7.0:
                all_controls.update(NIST_161_CONTROLS.get(dim, []))
        controls_list = sorted(all_controls)

        # Generate recommendations
        recommendations = []
        if provenance < 4.0:
            recommendations.append(
                "CRITICAL: Vendor originates from adversary nation. "
                "Evaluate alternatives immediately per EO 13873 / Section 889.")
        elif provenance < 7.0:
            recommendations.append(
                "Vendor provenance is non-allied. Conduct enhanced due diligence "
                "and consider supply chain diversification.")
        if integrity < 5.0:
            recommendations.append(
                "Section 889 non-compliance detected. Initiate remediation or "
                "vendor replacement per NDAA Section 889.")
        if dependency < 5.0:
            recommendations.append(
                "High concentration risk: many components depend on this vendor. "
                "Identify alternate suppliers.")
        if overall < 4.0:
            recommendations.append(
                "Overall SCRM risk is CRITICAL. Escalate to ISSO/AO for "
                "risk acceptance or vendor replacement decision.")
        if not recommendations:
            recommendations.append(
                "Vendor risk is within acceptable thresholds. "
                "Continue periodic reassessment per review cadence.")

        # Determine risk category for DB
        risk_category = None
        if provenance < 4.0:
            risk_category = "foreign_control"
        elif integrity < 5.0:
            risk_category = "counterfeit"
        elif dependency < 5.0:
            risk_category = "single_source"

        # Store assessment
        assessment_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Build mitigations text from recommendations
        mitigations = json.dumps(recommendations)

        conn.execute(
            """INSERT INTO scrm_assessments
               (id, project_id, vendor_id, assessment_type,
                risk_category, risk_score, likelihood, impact,
                mitigations, residual_risk, nist_161_controls,
                assessed_by, assessed_at, next_assessment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (assessment_id, project_id, vendor_id, "vendor",
             risk_category, overall, likelihood, impact,
             mitigations, risk_tier, json.dumps(controls_list),
             "icdev-supply-chain-agent", now,
             None),
        )
        conn.commit()

        # Update vendor risk tier
        conn.execute(
            """UPDATE supply_chain_vendors
               SET scrm_risk_tier = ?, last_assessed = ?, updated_at = ?
               WHERE id = ?""",
            (risk_tier, now, now, vendor_id),
        )
        conn.commit()

        _log_audit(conn, project_id, "scrm_assessed",
                   f"SCRM assessment for vendor {vendor.get('vendor_name', vendor_id)}",
                   {"assessment_id": assessment_id, "overall_score": overall,
                    "risk_tier": risk_tier})

        return {
            "assessment_id": assessment_id,
            "vendor_id": vendor_id,
            "vendor_name": vendor.get("vendor_name"),
            "overall_score": overall,
            "risk_tier": risk_tier,
            "dimension_scores": scores,
            "nist_161_controls": controls_list,
            "recommendations": recommendations,
        }
    finally:
        conn.close()


def assess_project(project_id, db_path=None):
    """Aggregate SCRM assessment across all vendors for a project.

    Assesses each vendor individually (if not already assessed recently),
    then aggregates risk distribution and top risks.

    Returns:
        dict with project_id, vendor_count, risk_distribution, top_risks,
        nist_161_coverage.
    """
    conn = _get_connection(db_path)
    try:
        vendors = conn.execute(
            "SELECT * FROM supply_chain_vendors WHERE project_id = ?",
            (project_id,),
        ).fetchall()

        if not vendors:
            return {
                "project_id": project_id,
                "vendor_count": 0,
                "risk_distribution": {"low": 0, "moderate": 0, "high": 0, "critical": 0},
                "top_risks": [],
                "nist_161_coverage": [],
            }
    finally:
        conn.close()

    # Assess each vendor
    assessments = []
    for v in vendors:
        vendor = dict(v)
        try:
            result = assess_vendor(project_id, vendor["id"], db_path)
            assessments.append(result)
        except Exception as exc:
            print(f"Warning: could not assess vendor {vendor.get('vendor_name')}: "
                  f"{exc}", file=sys.stderr)

    # Aggregate
    dist = {"low": 0, "moderate": 0, "high": 0, "critical": 0}
    all_controls = set()
    for a in assessments:
        tier = a.get("risk_tier", "moderate")
        dist[tier] = dist.get(tier, 0) + 1
        all_controls.update(a.get("nist_161_controls", []))

    # Sort by risk (worst first)
    tier_order = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
    top_risks = sorted(assessments,
                       key=lambda x: (tier_order.get(x.get("risk_tier"), 9),
                                      -x.get("overall_score", 0)))

    # Limit top risks to top 10
    top_risks_summary = []
    for a in top_risks[:10]:
        top_risks_summary.append({
            "vendor_id": a["vendor_id"],
            "vendor_name": a.get("vendor_name"),
            "risk_tier": a["risk_tier"],
            "overall_score": a["overall_score"],
            "top_recommendation": (a.get("recommendations", ["N/A"])[0]
                                   if a.get("recommendations") else "N/A"),
        })

    return {
        "project_id": project_id,
        "vendor_count": len(vendors),
        "assessed_count": len(assessments),
        "risk_distribution": dist,
        "top_risks": top_risks_summary,
        "nist_161_coverage": sorted(all_controls),
    }


def get_prohibited_vendors(project_id, db_path=None):
    """Find vendors with section_889_status = 'prohibited'.

    Returns:
        dict with project_id, count, and list of prohibited vendors
        with remediation guidance.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM supply_chain_vendors
               WHERE project_id = ? AND section_889_status = 'prohibited'
               ORDER BY vendor_name""",
            (project_id,),
        ).fetchall()

        prohibited = []
        for r in rows:
            d = dict(r)
            d["remediation_guidance"] = (
                f"Vendor '{d.get('vendor_name', 'N/A')}' is prohibited under "
                "NDAA Section 889. Immediate action required: "
                "(1) Cease new procurements from this vendor. "
                "(2) Develop transition plan to compliant alternative. "
                "(3) Report to Contracting Officer within 10 business days. "
                "(4) Document risk acceptance if operational necessity requires "
                "continued use during transition period."
            )
            prohibited.append(d)

        result = {
            "project_id": project_id,
            "prohibited_count": len(prohibited),
            "prohibited_vendors": prohibited,
        }

        if prohibited:
            _log_audit(conn, project_id, "supply_chain_risk_escalated",
                       f"Found {len(prohibited)} prohibited vendor(s)",
                       {"vendor_ids": [v["id"] for v in prohibited]})

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="NIST 800-161 SCRM Assessor (RICOAS)")
    parser.add_argument("--project-id", required=True,
                        help="Project identifier")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    parser.add_argument("--vendor-id",
                        help="Assess a specific vendor")
    parser.add_argument("--aggregate", action="store_true",
                        help="Aggregate SCRM across all vendors")
    parser.add_argument("--prohibited", action="store_true",
                        help="Find Section 889 prohibited vendors")

    args = parser.parse_args()

    try:
        result = None

        if args.vendor_id:
            result = assess_vendor(args.project_id, args.vendor_id)

        elif args.aggregate:
            result = assess_project(args.project_id)

        elif args.prohibited:
            result = get_prohibited_vendors(args.project_id)

        else:
            parser.print_help()
            sys.exit(0)

        if result is not None:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                _print_human(result)

    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_human(data):
    """Pretty-print result dict for human consumption."""
    if "dimension_scores" in data:
        print(f"SCRM Assessment: {data.get('vendor_name', data.get('vendor_id'))}")
        print(f"  Assessment ID: {data['assessment_id']}")
        print(f"  Overall Score: {data['overall_score']}/10")
        print(f"  Risk Tier: {data['risk_tier'].upper()}")
        print("  Dimension Scores:")
        for dim, score in data["dimension_scores"].items():
            bar = "#" * int(score) + "." * (10 - int(score))
            print(f"    {dim:20s} [{bar}] {score}/10")
        if data.get("nist_161_controls"):
            print(f"  NIST 800-161 Controls: {', '.join(data['nist_161_controls'])}")
        print("  Recommendations:")
        for r in data.get("recommendations", []):
            print(f"    - {r}")

    elif "risk_distribution" in data:
        print(f"Project SCRM Summary: {data['project_id']}")
        print(f"  Vendors: {data['vendor_count']}  Assessed: {data.get('assessed_count', '?')}")
        dist = data["risk_distribution"]
        print("  Risk Distribution:")
        print(f"    Critical: {dist.get('critical', 0)}")
        print(f"    High:     {dist.get('high', 0)}")
        print(f"    Moderate: {dist.get('moderate', 0)}")
        print(f"    Low:      {dist.get('low', 0)}")
        if data.get("top_risks"):
            print("  Top Risks:")
            for r in data["top_risks"]:
                print(f"    [{r['risk_tier'].upper()}] {r.get('vendor_name', r['vendor_id'])} "
                      f"(score={r['overall_score']})")

    elif "prohibited_vendors" in data:
        print(f"Prohibited Vendors for {data['project_id']}: "
              f"{data['prohibited_count']}")
        for v in data["prohibited_vendors"]:
            print(f"  - {v.get('vendor_name', v['id'])} "
                  f"({v.get('country_of_origin', '?')})")
            print(f"    {v.get('remediation_guidance', '')}")

    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
