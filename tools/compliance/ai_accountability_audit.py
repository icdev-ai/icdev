#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Accountability Audit — cross-framework accountability assessment.

Checks all accountability evidence across OMB M-25-21, M-26-04, GAO-21-519SP,
and NIST AI 600-1. Produces unified accountability score with gap analysis.

Complements ai_transparency_audit.py (Phase 48) by covering the accountability
half: human oversight, appeals, CAIO designation, incident response, ethics
reviews, and reassessment schedules.

Usage:
    python tools/compliance/ai_accountability_audit.py --project-id proj-123 --json
    python tools/compliance/ai_accountability_audit.py --project-id proj-123 --human
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Accountability checks mapped to framework requirements
ACCOUNTABILITY_CHECKS = [
    {
        "id": "ACC-1",
        "title": "Human Oversight Plan",
        "frameworks": ["M25-OVR-1"],
        "table": "ai_oversight_plans",
        "query": "SELECT COUNT(*) as cnt FROM ai_oversight_plans WHERE project_id = ?",
        "severity": "high",
        "action": "python tools/compliance/accountability_manager.py --project-id {pid} --register-oversight --plan-name 'Human Oversight Plan'",
    },
    {
        "id": "ACC-2",
        "title": "Approved Oversight Plan",
        "frameworks": ["M25-OVR-1"],
        "table": "ai_oversight_plans",
        "query": "SELECT COUNT(*) as cnt FROM ai_oversight_plans WHERE project_id = ? AND approval_status = 'approved'",
        "severity": "high",
        "action": "Approve an existing oversight plan (set approval_status='approved')",
    },
    {
        "id": "ACC-3",
        "title": "Appeal Process Registered",
        "frameworks": ["M25-OVR-3", "M26-REV-2", "FAIR-7"],
        "table": "ai_accountability_appeals",
        "query": "SELECT COUNT(*) as cnt FROM ai_accountability_appeals WHERE project_id = ?",
        "severity": "high",
        "action": "python tools/compliance/accountability_manager.py --project-id {pid} --file-appeal --appellant 'Test' --ai-system 'System'",
    },
    {
        "id": "ACC-4",
        "title": "CAIO/Responsible Official Designated",
        "frameworks": ["M25-OVR-4"],
        "table": "ai_caio_registry",
        "query": "SELECT COUNT(*) as cnt FROM ai_caio_registry WHERE project_id = ?",
        "severity": "high",
        "action": "python tools/compliance/accountability_manager.py --project-id {pid} --designate-caio --name 'Name' --role CAIO",
    },
    {
        "id": "ACC-5",
        "title": "Responsible Official on Inventory Items",
        "frameworks": ["M25-INV-2"],
        "table": "ai_use_case_inventory",
        "query": "SELECT COUNT(*) as cnt FROM ai_use_case_inventory WHERE project_id = ? AND responsible_official IS NOT NULL AND responsible_official != ''",
        "severity": "medium",
        "action": "Update AI inventory items with responsible_official field",
    },
    {
        "id": "ACC-6",
        "title": "Reassessment Schedule Defined",
        "frameworks": ["M25-INV-3", "GAO-MON-4"],
        "table": "ai_reassessment_schedule",
        "query": "SELECT COUNT(*) as cnt FROM ai_reassessment_schedule WHERE project_id = ?",
        "severity": "medium",
        "action": "python tools/compliance/ai_reassessment_scheduler.py --project-id {pid} --create --ai-system 'System' --frequency annual",
    },
    {
        "id": "ACC-7",
        "title": "No Overdue Reassessments",
        "frameworks": ["M25-INV-3", "GAO-MON-4"],
        "table": "ai_reassessment_schedule",
        "query": "SELECT COUNT(*) as cnt FROM ai_reassessment_schedule WHERE project_id = ? AND next_due < date('now')",
        "severity": "high",
        "invert": True,  # count > 0 = FAIL
        "action": "Complete overdue reassessments via ai_reassessment_scheduler.py --complete",
    },
    {
        "id": "ACC-8",
        "title": "AI Incident Response Process",
        "frameworks": ["M25-RISK-4", "GAO-MON-3"],
        "table": "ai_incident_log",
        "query": "SELECT COUNT(*) as cnt FROM ai_incident_log WHERE project_id = ?",
        "severity": "medium",
        "action": "python tools/compliance/ai_incident_response.py --project-id {pid} --log --type other --description 'Incident response process test'",
    },
    {
        "id": "ACC-9",
        "title": "No Unresolved Critical Incidents",
        "frameworks": ["M25-RISK-4", "GAO-MON-3"],
        "table": "ai_incident_log",
        "query": "SELECT COUNT(*) as cnt FROM ai_incident_log WHERE project_id = ? AND severity = 'critical' AND status NOT IN ('resolved', 'closed')",
        "severity": "high",
        "invert": True,
        "action": "Resolve critical AI incidents",
    },
    {
        "id": "ACC-10",
        "title": "Ethics Review Conducted",
        "frameworks": ["GAO-GOV-3"],
        "table": "ai_ethics_reviews",
        "query": "SELECT COUNT(*) as cnt FROM ai_ethics_reviews WHERE project_id = ?",
        "severity": "medium",
        "action": "python tools/compliance/accountability_manager.py --project-id {pid} --submit-ethics-review --review-type ethics_framework",
    },
    {
        "id": "ACC-11",
        "title": "Legal Compliance Matrix",
        "frameworks": ["GAO-GOV-2"],
        "table": "ai_ethics_reviews",
        "query": "SELECT COUNT(*) as cnt FROM ai_ethics_reviews WHERE project_id = ? AND legal_compliance_matrix = 1",
        "severity": "medium",
        "action": "python tools/compliance/accountability_manager.py --project-id {pid} --submit-ethics-review --review-type legal_compliance --legal-compliance-matrix",
    },
    {
        "id": "ACC-12",
        "title": "Opt-Out Policy Documented",
        "frameworks": ["M26-REV-3"],
        "table": "ai_ethics_reviews",
        "query": "SELECT COUNT(*) as cnt FROM ai_ethics_reviews WHERE project_id = ? AND opt_out_policy = 1",
        "severity": "medium",
        "action": "python tools/compliance/accountability_manager.py --project-id {pid} --submit-ethics-review --review-type other --opt-out-policy",
    },
    {
        "id": "ACC-13",
        "title": "Impact Assessment Conducted",
        "frameworks": ["M26-IMP-1"],
        "table": "ai_ethics_reviews",
        "query": "SELECT COUNT(*) as cnt FROM ai_ethics_reviews WHERE project_id = ? AND review_type = 'impact_assessment'",
        "severity": "high",
        "action": "python tools/compliance/ai_impact_assessor.py --project-id {pid} --ai-system 'System'",
    },
]


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def run_accountability_audit(
    project_id: str, db_path: Path = DB_PATH,
) -> Dict:
    """Run comprehensive AI accountability audit."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_connection(db_path)

    results = []
    passed = 0
    failed = 0
    gaps = []

    try:
        for check in ACCOUNTABILITY_CHECKS:
            status = "not_assessed"
            count = 0
            try:
                row = conn.execute(check["query"], (project_id,)).fetchone()
                count = row["cnt"] if row else 0

                if check.get("invert"):
                    status = "pass" if count == 0 else "fail"
                else:
                    status = "pass" if count > 0 else "fail"
            except Exception:
                status = "error"

            if status == "pass":
                passed += 1
            else:
                failed += 1
                gaps.append({
                    "check_id": check["id"],
                    "title": check["title"],
                    "severity": check["severity"],
                    "frameworks": check["frameworks"],
                    "action": check.get("action", "").format(pid=project_id),
                })

            results.append({
                "check_id": check["id"],
                "title": check["title"],
                "status": status,
                "frameworks": check["frameworks"],
                "severity": check["severity"],
                "count": count,
            })
    finally:
        conn.close()

    total = len(ACCOUNTABILITY_CHECKS)
    score = round(passed / total * 100, 1) if total > 0 else 0
    high_gaps = sum(1 for g in gaps if g["severity"] == "high")

    return {
        "audit_type": "AI Accountability Audit",
        "classification": "CUI // SP-CTI",
        "project_id": project_id,
        "audit_date": now,
        "accountability_score": score,
        "total_checks": total,
        "passed": passed,
        "failed": failed,
        "results": results,
        "gaps": gaps,
        "gap_count": len(gaps),
        "high_priority_gaps": high_gaps,
        "recommendation": (
            "PASS — AI accountability requirements substantially met"
            if score >= 70 and high_gaps == 0
            else "ACTION REQUIRED — Address accountability gaps before audit"
        ),
    }


def get_accountability_gaps(
    project_id: str, db_path: Path = DB_PATH,
) -> Dict:
    """Get accountability gaps only."""
    audit = run_accountability_audit(project_id, db_path)
    return {
        "project_id": project_id,
        "gaps": audit["gaps"],
        "gap_count": audit["gap_count"],
        "high_priority_gaps": audit["high_priority_gaps"],
        "accountability_score": audit["accountability_score"],
    }


def main():
    parser = argparse.ArgumentParser(description="AI Accountability Audit (Phase 49)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--gaps-only", action="store_true", help="Show gaps only")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--human", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.gaps_only:
            result = get_accountability_gaps(args.project_id, db)
        else:
            result = run_accountability_audit(args.project_id, db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("=" * 65)
            print("  AI Accountability Audit")
            print(f"  Project: {args.project_id}")
            print("=" * 65)
            print(f"  Accountability Score: {result.get('accountability_score', 0)}%")
            if "passed" in result:
                print(f"  Checks: {result['passed']}/{result['total_checks']} passed")
            print(f"  Gaps: {result['gap_count']} ({result['high_priority_gaps']} high)")
            if result.get("gaps"):
                print()
                for gap in result["gaps"]:
                    print(f"  [{gap['severity'].upper()}] {gap['title']}")
                    if gap.get("action"):
                        print(f"    Fix: {gap['action']}")
            print()
            print(f"  {result.get('recommendation', '')}")
            print("=" * 65)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
