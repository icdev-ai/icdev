#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Algorithmic Impact Assessor — Phase 49.

Conducts algorithmic impact assessments for AI systems as required by
OMB M-26-04 (M26-IMP-1). Evaluates rights impact, affected populations,
data sensitivity, reversibility, and human override availability.

ADR D320: Results stored in ai_ethics_reviews with review_type='impact_assessment'.

Usage:
    python tools/compliance/ai_impact_assessor.py --project-id proj-123 --ai-system "Fraud Detector" --json
    python tools/compliance/ai_impact_assessor.py --project-id proj-123 --summary --json
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

IMPACT_DIMENSIONS = [
    {
        "id": "IMP-RIGHTS",
        "title": "Rights-Impacting Classification",
        "description": "Does this AI system make or materially support decisions affecting individual rights, benefits, or access to services?",
        "weight": 0.25,
    },
    {
        "id": "IMP-POP",
        "title": "Affected Population Scale",
        "description": "How many individuals are affected by this AI system's decisions?",
        "weight": 0.15,
    },
    {
        "id": "IMP-DATA",
        "title": "Data Sensitivity",
        "description": "Does this system process sensitive data (PII, PHI, CUI, classified)?",
        "weight": 0.20,
    },
    {
        "id": "IMP-REV",
        "title": "Decision Reversibility",
        "description": "Can decisions made by this AI system be reversed or corrected?",
        "weight": 0.15,
    },
    {
        "id": "IMP-HUMAN",
        "title": "Human Override Availability",
        "description": "Is human-in-the-loop or human-on-the-loop oversight available?",
        "weight": 0.15,
    },
    {
        "id": "IMP-BIAS",
        "title": "Disparate Impact Risk",
        "description": "Could this system produce disparate outcomes across demographic groups?",
        "weight": 0.10,
    },
]


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ai_ethics_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            review_type TEXT NOT NULL,
            ai_system TEXT,
            findings TEXT,
            opt_out_policy INTEGER DEFAULT 0,
            legal_compliance_matrix INTEGER DEFAULT 0,
            pre_deployment_review INTEGER DEFAULT 0,
            reviewer TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def assess_impact(
    project_id: str,
    ai_system: str,
    dimension_responses: Optional[Dict[str, str]] = None,
    reviewer: str = "",
    db_path: Path = DB_PATH,
) -> Dict:
    """Run algorithmic impact assessment for an AI system.

    Args:
        dimension_responses: Optional dict mapping dimension IDs to
            'high', 'medium', 'low', or 'none'. If not provided,
            checks are inferred from existing DB data.
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        now = datetime.now(timezone.utc).isoformat()

        responses = dimension_responses or {}
        results = []
        score_sum = 0.0
        weight_sum = 0.0

        for dim in IMPACT_DIMENSIONS:
            dim_id = dim["id"]
            response = responses.get(dim_id, "")

            # Auto-assess from DB if no explicit response
            if not response:
                response = _auto_assess_dimension(conn, project_id, ai_system, dim_id)

            severity_score = {"high": 1.0, "medium": 0.6, "low": 0.3, "none": 0.0}.get(response, 0.5)
            weighted = severity_score * dim["weight"]
            score_sum += weighted
            weight_sum += dim["weight"]

            results.append({
                "id": dim_id,
                "title": dim["title"],
                "response": response or "not_assessed",
                "severity_score": round(severity_score, 2),
                "weighted_score": round(weighted, 3),
            })

        overall_risk = round(score_sum / weight_sum * 100, 1) if weight_sum > 0 else 0
        risk_level = "high" if overall_risk >= 70 else "medium" if overall_risk >= 40 else "low"

        assessment = {
            "project_id": project_id,
            "ai_system": ai_system,
            "assessment_date": now,
            "dimensions": results,
            "overall_risk_score": overall_risk,
            "risk_level": risk_level,
        }

        # Store as ethics review (D320)
        conn.execute(
            """INSERT INTO ai_ethics_reviews
               (project_id, review_type, ai_system, findings,
                pre_deployment_review, reviewer)
               VALUES (?, 'impact_assessment', ?, ?, 1, ?)""",
            (project_id, ai_system, json.dumps(assessment), reviewer),
        )
        conn.commit()

        return assessment
    finally:
        conn.close()


def _auto_assess_dimension(
    conn: sqlite3.Connection, project_id: str, ai_system: str, dim_id: str,
) -> str:
    """Infer dimension response from existing DB data."""
    try:
        if dim_id == "IMP-RIGHTS":
            row = conn.execute(
                """SELECT risk_level FROM ai_use_case_inventory
                   WHERE project_id = ? AND name = ?""",
                (project_id, ai_system),
            ).fetchone()
            if row:
                return {"high_impact": "high", "safety_impacting": "high"}.get(
                    row["risk_level"], "low")

        elif dim_id == "IMP-HUMAN":
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM ai_oversight_plans
                   WHERE project_id = ?""",
                (project_id,),
            ).fetchone()
            return "low" if row and row["cnt"] > 0 else "medium"

        elif dim_id == "IMP-BIAS":
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM fairness_assessments
                   WHERE project_id = ?""",
                (project_id,),
            ).fetchone()
            return "low" if row and row["cnt"] > 0 else "medium"

    except Exception:
        pass
    return ""


def get_impact_summary(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Get summary of impact assessments for a project."""
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        rows = conn.execute(
            """SELECT ai_system, findings, created_at FROM ai_ethics_reviews
               WHERE project_id = ? AND review_type = 'impact_assessment'
               ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()

        assessments = []
        for r in rows:
            findings = {}
            try:
                findings = json.loads(r["findings"]) if r["findings"] else {}
            except (json.JSONDecodeError, TypeError):
                pass
            assessments.append({
                "ai_system": r["ai_system"],
                "risk_level": findings.get("risk_level", "unknown"),
                "overall_risk_score": findings.get("overall_risk_score", 0),
                "created_at": r["created_at"],
            })

        return {
            "project_id": project_id,
            "total_assessments": len(assessments),
            "assessments": assessments,
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="AI Algorithmic Impact Assessor (Phase 49)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--ai-system", default="", help="AI system name to assess")
    parser.add_argument("--summary", action="store_true", help="Get assessment summary")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.summary:
            result = get_impact_summary(args.project_id, db)
        else:
            if not args.ai_system:
                print("ERROR: --ai-system required for assessment", file=sys.stderr)
                sys.exit(1)
            result = assess_impact(args.project_id, args.ai_system,
                                   reviewer=args.reviewer, db_path=db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.summary:
                print(f"Impact Assessments for {args.project_id}: {result['total_assessments']}")
            else:
                print(f"Impact Assessment: {result['ai_system']} — Risk: {result['risk_level']} ({result['overall_risk_score']}%)")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
