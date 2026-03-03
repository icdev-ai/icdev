#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Accountability Manager â Phase 49 central coordinator.

Manages human oversight plans, CAIO designation, appeals, ethics reviews,
and reassessment scheduling. Provides accountability evidence for OMB M-25-21,
M-26-04, GAO-21-519SP, and NIST AI 600-1 assessors.

ADR D317: Single coordinator tool consolidating 13 accountability gaps.

Usage:
    python tools/compliance/accountability_manager.py --project-id proj-123 --summary --json
    python tools/compliance/accountability_manager.py --project-id proj-123 --register-oversight --plan-name "Human Oversight Plan v1" --json
    python tools/compliance/accountability_manager.py --project-id proj-123 --designate-caio --name "Jane Smith" --role CAIO --json
    python tools/compliance/accountability_manager.py --project-id proj-123 --file-appeal --appellant "John Doe" --ai-system "Fraud Detector" --json
    python tools/compliance/accountability_manager.py --project-id proj-123 --submit-ethics-review --review-type bias_testing_policy --json
    python tools/compliance/accountability_manager.py --project-id proj-123 --schedule-reassessment --ai-system "Classifier" --frequency annual --json
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

VALID_REVIEW_TYPES = (
    "bias_testing_policy", "impact_assessment", "ethics_framework",
    "legal_compliance", "pre_deployment", "annual_review", "other",
)
VALID_FREQUENCIES = ("quarterly", "semi_annual", "annual", "biennial")
VALID_APPEAL_STATUSES = ("submitted", "under_review", "resolved", "dismissed")
VALID_APPROVAL_STATUSES = ("draft", "submitted", "approved", "rejected")


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ai_oversight_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        plan_name TEXT NOT NULL,
        description TEXT DEFAULT '',
        approval_status TEXT DEFAULT 'draft',
        created_by TEXT DEFAULT '',
        approved_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_caio_registry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT DEFAULT 'CAIO',
        organization TEXT DEFAULT '',
        appointment_date TEXT DEFAULT (datetime('now')),
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_accountability_appeals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        appellant TEXT NOT NULL,
        ai_system TEXT NOT NULL,
        grievance TEXT DEFAULT '',
        status TEXT DEFAULT 'submitted',
        resolution TEXT DEFAULT '',
        filed_at TEXT DEFAULT (datetime('now')),
        resolved_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ai_ethics_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        review_type TEXT NOT NULL,
        summary TEXT DEFAULT '',
        findings TEXT DEFAULT '',
        recommendation TEXT DEFAULT '',
        status TEXT DEFAULT 'submitted',
        submitted_at TEXT DEFAULT (datetime('now')),
        reviewed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ai_reassessment_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        ai_system TEXT NOT NULL,
        frequency TEXT DEFAULT 'annual',
        last_assessed TEXT,
        next_due TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()


def _audit_log(conn: sqlite3.Connection, project_id: str, event_type: str,
               actor: str, action: str) -> None:
    """Write to audit_trail if it exists."""
    try:
        conn.execute(
            "INSERT INTO audit_trail (project_id, event_type, actor, action, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, event_type, actor, action,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # audit_trail table may not exist yet


def register_oversight_plan(conn: sqlite3.Connection, project_id: str,
                            plan_name: str, description: str = "",
                            created_by: str = "") -> Dict:
    """Register a human oversight plan for an AI system."""
    cur = conn.execute(
        "INSERT INTO ai_oversight_plans (project_id, plan_name, description, created_by) "
        "VALUES (?, ?, ?, ?)",
        (project_id, plan_name, description, created_by),
    )
    conn.commit()
    plan_id = cur.lastrowid
    _audit_log(conn, project_id, "accountability.oversight_plan",
               created_by or "system", f"Registered oversight plan: {plan_name}")
    return {
        "plan_id": plan_id,
        "project_id": project_id,
        "plan_name": plan_name,
        "approval_status": "draft",
        "created_by": created_by,
    }


def designate_caio(conn: sqlite3.Connection, project_id: str,
                   name: str, role: str = "CAIO",
                   organization: str = "") -> Dict:
    """Designate a Chief AI Officer or responsible individual."""
    cur = conn.execute(
        "INSERT INTO ai_caio_registry (project_id, name, role, organization) "
        "VALUES (?, ?, ?, ?)",
        (project_id, name, role, organization),
    )
    conn.commit()
    _audit_log(conn, project_id, "accountability.caio_designation",
               name, f"Designated {role}: {name}")
    return {
        "caio_id": cur.lastrowid,
        "project_id": project_id,
        "name": name,
        "role": role,
        "status": "active",
    }


def file_appeal(conn: sqlite3.Connection, project_id: str,
                appellant: str, ai_system: str,
                grievance: str = "") -> Dict:
    """File an appeal against an AI system decision."""
    cur = conn.execute(
        "INSERT INTO ai_accountability_appeals "
        "(project_id, appellant, ai_system, grievance) VALUES (?, ?, ?, ?)",
        (project_id, appellant, ai_system, grievance),
    )
    conn.commit()
    _audit_log(conn, project_id, "accountability.appeal_filed",
               appellant, f"Appeal filed against {ai_system}")
    return {
        "appeal_id": cur.lastrowid,
        "project_id": project_id,
        "appellant": appellant,
        "ai_system": ai_system,
        "status": "submitted",
    }


def resolve_appeal(conn: sqlite3.Connection, appeal_id: int,
                   resolution: str, status: str = "resolved") -> Dict:
    """Resolve a pending appeal."""
    if status not in VALID_APPEAL_STATUSES:
        return {"error": f"Invalid status. Must be one of {VALID_APPEAL_STATUSES}"}
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE ai_accountability_appeals SET resolution=?, status=?, resolved_at=? "
        "WHERE id=?",
        (resolution, status, now, appeal_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM ai_accountability_appeals WHERE id=?",
                       (appeal_id,)).fetchone()
    if not row:
        return {"error": f"Appeal {appeal_id} not found"}
    _audit_log(conn, row["project_id"], "accountability.appeal_resolved",
               "system", f"Appeal {appeal_id} resolved as {status}")
    return dict(row)


def submit_ethics_review(conn: sqlite3.Connection, project_id: str,
                         review_type: str, summary: str = "",
                         findings: str = "",
                         recommendation: str = "") -> Dict:
    """Submit an ethics review for an AI project."""
    if review_type not in VALID_REVIEW_TYPES:
        return {"error": f"Invalid review_type. Must be one of {VALID_REVIEW_TYPES}"}
    cur = conn.execute(
        "INSERT INTO ai_ethics_reviews "
        "(project_id, review_type, summary, findings, recommendation) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, review_type, summary, findings, recommendation),
    )
    conn.commit()
    _audit_log(conn, project_id, "accountability.ethics_review",
               "system", f"Ethics review submitted: {review_type}")
    return {
        "review_id": cur.lastrowid,
        "project_id": project_id,
        "review_type": review_type,
        "status": "submitted",
    }


def schedule_reassessment(conn: sqlite3.Connection, project_id: str,
                          ai_system: str, frequency: str = "annual",
                          last_assessed: Optional[str] = None) -> Dict:
    """Schedule periodic reassessment for an AI system."""
    if frequency not in VALID_FREQUENCIES:
        return {"error": f"Invalid frequency. Must be one of {VALID_FREQUENCIES}"}
    from datetime import timedelta
    freq_days = {"quarterly": 90, "semi_annual": 182, "annual": 365, "biennial": 730}
    base = last_assessed or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        base_dt = datetime.strptime(base, "%Y-%m-%d")
    except ValueError:
        base_dt = datetime.now(timezone.utc)
    next_due = (base_dt + timedelta(days=freq_days[frequency])).strftime("%Y-%m-%d")
    cur = conn.execute(
        "INSERT INTO ai_reassessment_schedule "
        "(project_id, ai_system, frequency, last_assessed, next_due) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, ai_system, frequency, base, next_due),
    )
    conn.commit()
    _audit_log(conn, project_id, "accountability.reassessment_scheduled",
               "system", f"Reassessment scheduled for {ai_system}: {frequency}")
    return {
        "schedule_id": cur.lastrowid,
        "project_id": project_id,
        "ai_system": ai_system,
        "frequency": frequency,
        "next_due": next_due,
    }


def get_accountability_summary(conn: sqlite3.Connection,
                               project_id: str) -> Dict:
    """Return a consolidated accountability summary for a project."""
    plans = conn.execute(
        "SELECT * FROM ai_oversight_plans WHERE project_id=?",
        (project_id,),
    ).fetchall()
    caios = conn.execute(
        "SELECT * FROM ai_caio_registry WHERE project_id=?",
        (project_id,),
    ).fetchall()
    appeals = conn.execute(
        "SELECT * FROM ai_accountability_appeals WHERE project_id=?",
        (project_id,),
    ).fetchall()
    reviews = conn.execute(
        "SELECT * FROM ai_ethics_reviews WHERE project_id=?",
        (project_id,),
    ).fetchall()
    schedules = conn.execute(
        "SELECT * FROM ai_reassessment_schedule WHERE project_id=?",
        (project_id,),
    ).fetchall()
    open_appeals = [dict(a) for a in appeals if a["status"] in ("submitted", "under_review")]
    return {
        "project_id": project_id,
        "oversight_plans": len(plans),
        "caio_designations": len(caios),
        "total_appeals": len(appeals),
        "open_appeals": len(open_appeals),
        "ethics_reviews": len(reviews),
        "reassessment_schedules": len(schedules),
        "plans": [dict(p) for p in plans],
        "caios": [dict(c) for c in caios],
        "appeals": [dict(a) for a in appeals],
        "reviews": [dict(r) for r in reviews],
        "schedules": [dict(s) for s in schedules],
        "compliance_references": [
            "OMB M-25-21 (AI Governance)",
            "OMB M-26-04 (AI Risk Management)",
            "GAO-21-519SP (AI Accountability Framework)",
            "NIST AI 600-1 (AI Risk Management)",
            "EO 14110 (Safe, Secure, Trustworthy AI)",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI Accountability Manager â Phase 49",
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--db-path", type=str, default=None)
    # Actions
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--register-oversight", action="store_true")
    parser.add_argument("--designate-caio", action="store_true")
    parser.add_argument("--file-appeal", action="store_true")
    parser.add_argument("--resolve-appeal-id", type=int, default=None)
    parser.add_argument("--submit-ethics-review", action="store_true")
    parser.add_argument("--schedule-reassessment", action="store_true")
    # Detail args
    parser.add_argument("--plan-name", type=str, default="")
    parser.add_argument("--description", type=str, default="")
    parser.add_argument("--created-by", type=str, default="")
    parser.add_argument("--name", type=str, default="")
    parser.add_argument("--role", type=str, default="CAIO")
    parser.add_argument("--organization", type=str, default="")
    parser.add_argument("--appellant", type=str, default="")
    parser.add_argument("--ai-system", type=str, default="")
    parser.add_argument("--grievance", type=str, default="")
    parser.add_argument("--resolution", type=str, default="")
    parser.add_argument("--appeal-status", type=str, default="resolved")
    parser.add_argument("--review-type", type=str, default="")
    parser.add_argument("--findings", type=str, default="")
    parser.add_argument("--recommendation", type=str, default="")
    parser.add_argument("--frequency", type=str, default="annual")
    parser.add_argument("--last-assessed", type=str, default=None)
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    conn = _get_connection(db_path)
    _ensure_tables(conn)

    result: Dict = {}
    if args.summary:
        result = get_accountability_summary(conn, args.project_id)
    elif args.register_oversight:
        result = register_oversight_plan(
            conn, args.project_id, args.plan_name,
            args.description, args.created_by,
        )
    elif args.designate_caio:
        result = designate_caio(
            conn, args.project_id, args.name,
            args.role, args.organization,
        )
    elif args.file_appeal:
        result = file_appeal(
            conn, args.project_id, args.appellant,
            args.ai_system, args.grievance,
        )
    elif args.resolve_appeal_id is not None:
        result = resolve_appeal(
            conn, args.resolve_appeal_id,
            args.resolution, args.appeal_status,
        )
    elif args.submit_ethics_review:
        result = submit_ethics_review(
            conn, args.project_id, args.review_type,
            args.description, args.findings, args.recommendation,
        )
    elif args.schedule_reassessment:
        result = schedule_reassessment(
            conn, args.project_id, args.ai_system,
            args.frequency, args.last_assessed,
        )
    else:
        result = {"error": "No action specified. Use --summary, --register-oversight, etc."}

    conn.close()

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
