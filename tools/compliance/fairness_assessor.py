#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Fairness & Bias Assessor — OMB M-26-04 compliance evidence.

Focuses on compliance documentation evidence: are policies, processes,
and documentation in place for bias testing and fairness metrics?
Does NOT perform statistical bias testing on training data (D311).

Usage:
    python tools/compliance/fairness_assessor.py --project-id proj-123 --json
    python tools/compliance/fairness_assessor.py --project-id proj-123 --gate
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fairness_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            dimension TEXT NOT NULL,
            status TEXT DEFAULT 'not_assessed',
            evidence TEXT,
            score REAL DEFAULT 0.0,
            assessed_at TEXT DEFAULT (datetime('now')),
            classification TEXT DEFAULT 'CUI',
            UNIQUE(project_id, dimension)
        );
        CREATE INDEX IF NOT EXISTS idx_fairness_project
            ON fairness_assessments(project_id);
    """)
    conn.commit()


FAIRNESS_DIMENSIONS = [
    {
        "id": "FAIR-1",
        "title": "Bias Testing Policy",
        "description": "Documented policy for conducting bias testing before and during AI deployment",
        "check_type": "policy",
    },
    {
        "id": "FAIR-2",
        "title": "Fairness Metrics Definition",
        "description": "Fairness metrics selected and documented with rationale",
        "check_type": "documentation",
    },
    {
        "id": "FAIR-3",
        "title": "Disparity Analysis Process",
        "description": "Process for analyzing outcome disparities across demographic groups",
        "check_type": "process",
    },
    {
        "id": "FAIR-4",
        "title": "Protected Class Monitoring",
        "description": "Ongoing monitoring for disparate impact on protected classes",
        "check_type": "monitoring",
    },
    {
        "id": "FAIR-5",
        "title": "Bias Mitigation Measures",
        "description": "Documented mitigation measures for identified biases",
        "check_type": "remediation",
    },
    {
        "id": "FAIR-6",
        "title": "Human Review for Adverse Decisions",
        "description": "Human review process for AI decisions adversely affecting individuals",
        "check_type": "oversight",
    },
    {
        "id": "FAIR-7",
        "title": "Appeal Process Accessibility",
        "description": "Accessible appeal process for individuals affected by AI decisions",
        "check_type": "appeal",
    },
    {
        "id": "FAIR-8",
        "title": "Decision Explanation Capability",
        "description": "Ability to explain AI decision factors to affected individuals",
        "check_type": "explainability",
    },
]


def assess_fairness(
    project_id: str,
    project_dir: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> Dict:
    """Run fairness assessment checking documentation and process evidence."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        now = datetime.now(timezone.utc).isoformat()

        dimension_results = []
        satisfied_count = 0

        for dim in FAIRNESS_DIMENSIONS:
            status = "not_assessed"
            evidence = ""

            # DB-based checks
            if dim["check_type"] == "explainability":
                try:
                    for table in ["xai_assessments", "shap_attributions"]:
                        row = conn.execute(
                            f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = %s",  # nosec B608 -- table/column names are internal constants, not user input
                            (project_id,),
                        ).fetchone()
                        if row and row["cnt"] > 0:
                            status = "satisfied"
                            evidence = f"XAI/SHAP data available in {table}"
                            break
                except Exception:
                    pass

            elif dim["check_type"] == "monitoring":
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM ai_telemetry WHERE project_id = %s",
                        (project_id,),
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        status = "satisfied"
                        evidence = "AI telemetry monitoring active"
                except Exception:
                    pass

            elif dim["check_type"] == "policy":
                # FAIR-1: Bias testing policy via ai_ethics_reviews (Phase 49)
                try:
                    row = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_ethics_reviews
                           WHERE project_id = %s AND review_type = 'bias_testing_policy'""",
                        (project_id,),
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        status = "satisfied"
                        evidence = "Bias testing policy documented in ai_ethics_reviews"
                except Exception:
                    pass

            elif dim["check_type"] == "process":
                # FAIR-3: Disparity analysis via pre_deployment_review (Phase 49)
                try:
                    row = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_ethics_reviews
                           WHERE project_id = %s AND pre_deployment_review = 1""",
                        (project_id,),
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        status = "satisfied"
                        evidence = "Pre-deployment disparity review conducted"
                except Exception:
                    pass

            elif dim["check_type"] == "oversight":
                # FAIR-6: Human review via ai_oversight_plans (Phase 49)
                try:
                    row = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_oversight_plans
                           WHERE project_id = %s""",
                        (project_id,),
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        status = "satisfied"
                        evidence = "Human oversight plan registered"
                except Exception:
                    pass

            elif dim["check_type"] == "appeal":
                # FAIR-7: Appeal process via ai_accountability_appeals (Phase 49)
                try:
                    row = conn.execute(
                        """SELECT COUNT(*) as cnt FROM ai_accountability_appeals
                           WHERE project_id = %s""",
                        (project_id,),
                    ).fetchone()
                    if row and row["cnt"] > 0:
                        status = "satisfied"
                        evidence = "Appeal process registered in accountability system"
                except Exception:
                    pass

            # File-based checks
            if status == "not_assessed" and project_dir:
                project_path = Path(project_dir)
                keywords = {
                    "policy": ["bias", "fairness", "testing", "policy"],
                    "documentation": ["fairness", "metric", "demographic", "parity"],
                    "process": ["disparity", "analysis", "demographic", "outcome"],
                    "remediation": ["bias", "mitigation", "remediat"],
                    "oversight": ["human review", "human-in-the-loop", "oversight"],
                    "appeal": ["appeal", "redress", "grievance"],
                }
                kw_list = keywords.get(dim["check_type"], [])
                for f in project_path.rglob("*.md"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="ignore").lower()
                        if any(kw in content for kw in kw_list):
                            status = "partially_satisfied"
                            evidence = f"Related documentation found in {f.name}"
                            break
                    except Exception:
                        continue

            if status == "satisfied":
                satisfied_count += 1
            dimension_results.append(
                {
                    "id": dim["id"],
                    "title": dim["title"],
                    "status": status,
                    "evidence": evidence,
                }
            )

        total = len(FAIRNESS_DIMENSIONS)
        overall_score = round(satisfied_count / total * 100, 1) if total > 0 else 0

        assessment = {
            "project_id": project_id,
            "assessment_date": now,
            "total_dimensions": total,
            "satisfied": satisfied_count,
            "overall_score": overall_score,
            "dimensions": dimension_results,
        }

        # Store per-dimension rows (upsert)
        for dim_result in dimension_results:
            score_val = 1.0 if dim_result["status"] == "satisfied" else 0.0
            conn.execute(
                """INSERT INTO fairness_assessments
                   (project_id, dimension, status, evidence, score, assessed_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT(project_id, dimension) DO UPDATE SET
                       status=excluded.status,
                       evidence=excluded.evidence,
                       score=excluded.score,
                       assessed_at=excluded.assessed_at""",
                (
                    project_id,
                    dim_result["id"],
                    dim_result["status"],
                    dim_result["evidence"],
                    score_val,
                    now,
                ),
            )
        conn.commit()

        return assessment
    finally:
        conn.close()


def evaluate_gate(project_id: str, db_path: Path = DB_PATH) -> Dict:
    """Evaluate fairness gate."""
    conn = _get_connection(db_path)
    try:
        _ensure_table(conn)
        rows = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN status = 'satisfied' THEN 1 ELSE 0 END) as satisfied
               FROM fairness_assessments WHERE project_id = %s""",
            (project_id,),
        ).fetchone()

        if not rows or not rows["total"]:
            return {"pass": False, "reason": "No fairness assessment conducted"}

        total = rows["total"]
        satisfied = rows["satisfied"] or 0
        score = round(satisfied / total * 100, 1) if total > 0 else 0.0
        return {
            "pass": score >= 25.0,
            "score": score,
            "project_id": project_id,
        }
    finally:
        conn.close()


class FairnessAssessor:
    """Class-based wrapper around assess_fairness / evaluate_gate."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def assess_fairness(self, project_id: str, project_dir: Optional[str] = None) -> Dict:
        return assess_fairness(project_id, project_dir=project_dir, db_path=self.db_path)

    def evaluate_gate(self, project_id: str) -> Dict:
        return evaluate_gate(project_id, db_path=self.db_path)


def main():
    parser = argparse.ArgumentParser(description="Fairness & Bias Assessor (OMB M-26-04)")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-dir", help="Project directory for file-based checks")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    db = args.db_path or DB_PATH
    try:
        if args.gate:
            result = evaluate_gate(args.project_id, db)
        else:
            result = assess_fairness(args.project_id, args.project_dir, db)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.gate:
                print(f"Fairness Gate: {'PASS' if result['pass'] else 'FAIL'} ({result.get('score', 0)}%)")
            else:
                print(f"Fairness Assessment: {result['overall_score']}%")
                print(f"  {result['satisfied']}/{result['total_dimensions']} dimensions satisfied")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
