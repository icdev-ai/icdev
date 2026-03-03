#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Transparency Audit — cross-framework transparency assessment.

Runs all 4 AI transparency assessors, checks model/system cards,
AI inventory completeness, and produces a unified transparency report
with gap analysis. Designed for GAO auditors and ISSO review.

Usage:
    python tools/compliance/ai_transparency_audit.py --project-id proj-123 --json
    python tools/compliance/ai_transparency_audit.py --project-id proj-123 --human
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

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _safe_assess(assessor_class, project_id: str, project_dir: Optional[str], db_path: Path) -> Dict:
    """Safely run an assessor, returning error dict on failure."""
    try:
        assessor = assessor_class(db_path=db_path)
        return assessor.assess(project_id, project_dir=project_dir)
    except Exception as e:
        return {
            "framework_id": getattr(assessor_class, "FRAMEWORK_ID", "unknown"),
            "error": str(e),
            "status_counts": {},
            "coverage_pct": 0,
            "total_requirements": 0,
        }


def _safe_import_assess(module_name: str, class_name: str, project_id: str, project_dir: Optional[str], db_path: Path) -> Dict:
    """Import and run an assessor by name."""
    try:
        import importlib
        mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name)
        return _safe_assess(cls, project_id, project_dir, db_path)
    except Exception as e:
        return {"error": str(e), "coverage_pct": 0, "total_requirements": 0}


def _count_table(db_path: Path, table: str, project_id: str) -> int:
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def run_transparency_audit(
    project_id: str,
    project_dir: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> Dict:
    """Run comprehensive AI transparency audit."""
    now = datetime.now(timezone.utc).isoformat()

    # Run all 4 assessors
    assessments = {}
    assessor_map = [
        ("omb_m25_21_assessor", "OMBM2521Assessor", "OMB M-25-21"),
        ("omb_m26_04_assessor", "OMBM2604Assessor", "OMB M-26-04"),
        ("nist_ai_600_1_assessor", "NISTAI6001Assessor", "NIST AI 600-1"),
        ("gao_ai_assessor", "GAOAIAssessor", "GAO-21-519SP"),
    ]

    for module, cls_name, display_name in assessor_map:
        result = _safe_import_assess(module, cls_name, project_id, project_dir, db_path)
        fid = result.get("framework_id", module)
        assessments[fid] = {
            "name": display_name,
            "coverage_pct": result.get("coverage_pct", 0),
            "total": result.get("total_requirements", 0),
            "status_counts": result.get("status_counts", {}),
            "error": result.get("error"),
        }

    # Check documentation artifacts
    artifacts = {
        "model_cards": _count_table(db_path, "model_cards", project_id),
        "system_cards": _count_table(db_path, "system_cards", project_id),
        "ai_inventory": _count_table(db_path, "ai_use_case_inventory", project_id),
        "fairness_assessments": _count_table(db_path, "fairness_assessments", project_id),
        "confabulation_checks": _count_table(db_path, "confabulation_checks", project_id),
    }

    # Build GAO evidence summary
    try:
        from gao_evidence_builder import build_evidence
        gao_evidence = build_evidence(project_id, db_path)
        gao_coverage = gao_evidence.get("summary", {}).get("overall_coverage_pct", 0)
    except Exception:
        gao_coverage = 0

    # Identify gaps
    gaps = []
    if artifacts["model_cards"] == 0:
        gaps.append({
            "area": "Model Documentation",
            "gap": "No model cards generated",
            "framework": "OMB M-26-04",
            "priority": "high",
            "action": "Run: python tools/compliance/model_card_generator.py --project-id {pid} --model-name <name>".format(pid=project_id),
        })
    if artifacts["system_cards"] == 0:
        gaps.append({
            "area": "System Documentation",
            "gap": "No system card generated",
            "framework": "OMB M-26-04",
            "priority": "high",
            "action": "Run: python tools/compliance/system_card_generator.py --project-id {pid}".format(pid=project_id),
        })
    if artifacts["ai_inventory"] == 0:
        gaps.append({
            "area": "AI Inventory",
            "gap": "No AI components registered in inventory",
            "framework": "OMB M-25-21",
            "priority": "high",
            "action": "Run: python tools/compliance/ai_inventory_manager.py --project-id {pid} --register --name <name>".format(pid=project_id),
        })
    if artifacts["fairness_assessments"] == 0:
        gaps.append({
            "area": "Bias & Fairness",
            "gap": "No fairness assessment conducted",
            "framework": "OMB M-26-04",
            "priority": "medium",
            "action": "Run: python tools/compliance/fairness_assessor.py --project-id {pid}".format(pid=project_id),
        })
    if artifacts["confabulation_checks"] == 0:
        gaps.append({
            "area": "Confabulation Detection",
            "gap": "No confabulation checks recorded",
            "framework": "NIST AI 600-1",
            "priority": "medium",
            "action": "Run: python tools/security/confabulation_detector.py --project-id {pid} --check-output <text>".format(pid=project_id),
        })

    for fid, data in assessments.items():
        if data["coverage_pct"] < 50 and not data.get("error"):
            gaps.append({
                "area": data["name"],
                "gap": f"Coverage below 50% ({data['coverage_pct']}%)",
                "framework": data["name"],
                "priority": "high",
            })

    # Overall score
    assessment_scores = [d["coverage_pct"] for d in assessments.values() if not d.get("error")]
    overall_score = round(sum(assessment_scores) / len(assessment_scores), 1) if assessment_scores else 0

    artifact_score = sum(1 for v in artifacts.values() if v > 0) / len(artifacts) * 100

    combined_score = round((overall_score * 0.6 + artifact_score * 0.3 + gao_coverage * 0.1), 1)

    return {
        "audit_type": "AI Transparency & Accountability Audit",
        "classification": "CUI // SP-CTI",
        "project_id": project_id,
        "audit_date": now,
        "overall_transparency_score": combined_score,
        "framework_assessment_score": overall_score,
        "artifact_completeness_score": round(artifact_score, 1),
        "gao_evidence_coverage": gao_coverage,
        "assessments": assessments,
        "artifacts": artifacts,
        "gaps": gaps,
        "gap_count": len(gaps),
        "high_priority_gaps": sum(1 for g in gaps if g["priority"] == "high"),
        "recommendation": (
            "PASS — AI transparency requirements substantially met"
            if combined_score >= 70 and not any(g["priority"] == "high" for g in gaps)
            else "ACTION REQUIRED — Address high-priority gaps before audit"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description="AI Transparency Audit (Phase 48)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-dir", help="Project directory")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--human", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        result = run_transparency_audit(args.project_id, args.project_dir, db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("=" * 65)
            print("  AI Transparency & Accountability Audit")
            print(f"  Project: {args.project_id}")
            print("=" * 65)
            print(f"  Overall Score: {result['overall_transparency_score']}%")
            print(f"  Framework Assessments: {result['framework_assessment_score']}%")
            print(f"  Artifact Completeness: {result['artifact_completeness_score']}%")
            print(f"  GAO Evidence Coverage: {result['gao_evidence_coverage']}%")
            print()
            print("  Framework Results:")
            for fid, data in result["assessments"].items():
                status = f"{data['coverage_pct']}%" if not data.get("error") else "ERROR"
                print(f"    {data['name']}: {status}")
            print()
            print("  Artifacts:")
            for name, count in result["artifacts"].items():
                status = f"{count}" if count > 0 else "MISSING"
                print(f"    {name}: {status}")
            if result["gaps"]:
                print()
                print(f"  Gaps ({result['gap_count']}):")
                for gap in result["gaps"]:
                    print(f"    [{gap['priority'].upper()}] {gap['gap']}")
            print()
            print(f"  {result['recommendation']}")
            print("=" * 65)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
