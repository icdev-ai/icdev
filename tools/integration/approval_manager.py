#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Approval workflow manager for RICOAS integration.

Manages approval workflows for requirements packages, COA selection,
boundary acceptance, and deployment gates.

Usage:
    # Submit for approval
    python tools/integration/approval_manager.py --session-id sess-abc \\
        --submit --approval-type requirements_package \\
        --submitted-by "analyst-agent" \\
        --reviewers '["isso@dod.mil","pm@dod.mil"]' --json

    # Review an approval
    python tools/integration/approval_manager.py --approval-id appr-abc \\
        --review --reviewer "isso@dod.mil" --decision approved \\
        --rationale "Requirements meet IL5 standards" --json

    # List pending approvals
    python tools/integration/approval_manager.py --pending --json

    # List pending for specific reviewer
    python tools/integration/approval_manager.py --pending \\
        --reviewer "isso@dod.mil" --json

    # List approvals for a session
    python tools/integration/approval_manager.py --session-id sess-abc --list --json

    # Get single approval details
    python tools/integration/approval_manager.py --approval-id appr-abc --get --json
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Valid approval types (must match DB CHECK constraint)
_CANONICAL_TYPES = {
    "requirements_package",
    "coa_selection",
    "boundary_impact_acceptance",
    "decomposition_approval",
    "pi_commitment",
}

# CLI aliases that resolve to canonical types
_TYPE_ALIASES = {
    "boundary_acceptance": "boundary_impact_acceptance",
    "deployment_gate": "pi_commitment",
}

VALID_DECISIONS = {"approved", "rejected", "conditional"}

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="appr"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_approval_type(raw_type):
    """Resolve CLI aliases to DB-valid approval types."""
    if raw_type in _TYPE_ALIASES:
        return _TYPE_ALIASES[raw_type]
    if raw_type in _CANONICAL_TYPES:
        return raw_type
    return raw_type


# ---------------------------------------------------------------------------
# submit_for_approval
# ---------------------------------------------------------------------------

def submit_for_approval(session_id, approval_type, submitted_by, reviewers,
                        conditions=None, db_path=None):
    """Submit a workflow item for approval.

    Args:
        session_id: Intake session identifier.
        approval_type: One of requirements_package, coa_selection,
            boundary_impact_acceptance, decomposition_approval, pi_commitment.
        submitted_by: Actor submitting (e.g. "analyst-agent").
        reviewers: List of reviewer identifiers.
        conditions: Optional dict of approval conditions.
        db_path: Override database path.

    Returns:
        dict with approval_id, approval_type, status, reviewers.
    """
    conn = _get_connection(db_path)
    try:
        # Resolve approval type alias
        resolved_type = _resolve_approval_type(approval_type)

        # Get project_id from session
        session_row = conn.execute(
            "SELECT project_id FROM intake_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not session_row:
            return {"error": f"Session not found: {session_id}"}

        project_id = session_row["project_id"]
        approval_id = _generate_id("appr")
        now = _now()

        # Ensure reviewers is a list
        if isinstance(reviewers, str):
            try:
                reviewers = json.loads(reviewers)
            except (json.JSONDecodeError, TypeError):
                reviewers = [reviewers]

        conn.execute(
            """INSERT INTO approval_workflows
               (id, session_id, project_id, approval_type, status,
                submitted_by, submitted_at, reviewers, current_reviewer,
                conditions, classification, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, session_id, project_id, resolved_type, "pending",
             submitted_by, now, json.dumps(reviewers),
             reviewers[0] if reviewers else None,
             json.dumps(conditions) if conditions else None,
             "CUI", now, now),
        )
        conn.commit()

        log_event(
            event_type="approval_submitted",
            actor=submitted_by,
            action=f"Submitted {resolved_type} for approval",
            project_id=project_id,
            details={
                "approval_id": approval_id,
                "approval_type": resolved_type,
                "reviewers": reviewers,
            },
        )

        return {
            "approval_id": approval_id,
            "session_id": session_id,
            "project_id": project_id,
            "approval_type": resolved_type,
            "status": "pending",
            "submitted_by": submitted_by,
            "reviewers": reviewers,
            "submitted_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# review_approval
# ---------------------------------------------------------------------------

def review_approval(approval_id, reviewer, decision, rationale, db_path=None):
    """Review and decide on an approval workflow.

    Args:
        approval_id: Approval workflow identifier.
        reviewer: Reviewer making the decision.
        decision: One of approved, rejected, conditional.
        rationale: Justification for the decision.
        db_path: Override database path.

    Returns:
        dict with approval_id, decision, reviewer, status.
    """
    if decision not in VALID_DECISIONS:
        return {
            "error": f"Invalid decision '{decision}'. Must be one of: {', '.join(sorted(VALID_DECISIONS))}",
        }

    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT id, session_id, project_id, approval_type, status,
                      reviewers, approval_chain
               FROM approval_workflows WHERE id = ?""",
            (approval_id,),
        ).fetchone()
        if not row:
            return {"error": f"Approval not found: {approval_id}"}

        if row["status"] not in ("pending", "in_review"):
            return {
                "error": f"Approval {approval_id} is already {row['status']}",
            }

        reviewers = json.loads(row["reviewers"] or "[]")
        if reviewer not in reviewers:
            return {
                "error": f"Reviewer '{reviewer}' is not in the reviewer list: {reviewers}",
            }

        now = _now()

        # Map decision to workflow status
        status_map = {
            "approved": "approved",
            "rejected": "rejected",
            "conditional": "conditional",
        }
        new_status = status_map[decision]

        # Build approval chain
        existing_chain = json.loads(row["approval_chain"] or "[]")
        existing_chain.append({
            "reviewer": reviewer,
            "decision": decision,
            "rationale": rationale,
            "decided_at": now,
        })

        conn.execute(
            """UPDATE approval_workflows
               SET status = ?, current_reviewer = NULL,
                   decision_rationale = ?, decided_at = ?,
                   approval_chain = ?, updated_at = ?
               WHERE id = ?""",
            (new_status, rationale, now,
             json.dumps(existing_chain), now, approval_id),
        )
        conn.commit()

        # Audit event type based on decision
        event_type = {
            "approved": "approval_approved",
            "rejected": "approval_rejected",
            "conditional": "approval_reviewed",
        }.get(decision, "approval_reviewed")

        log_event(
            event_type=event_type,
            actor=reviewer,
            action=f"{decision.capitalize()} {row['approval_type']} ({approval_id})",
            project_id=row["project_id"],
            details={
                "approval_id": approval_id,
                "decision": decision,
                "rationale": rationale,
            },
        )

        return {
            "approval_id": approval_id,
            "session_id": row["session_id"],
            "project_id": row["project_id"],
            "approval_type": row["approval_type"],
            "decision": decision,
            "reviewer": reviewer,
            "rationale": rationale,
            "status": new_status,
            "decided_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_pending
# ---------------------------------------------------------------------------

def get_pending(project_id=None, reviewer=None, db_path=None):
    """List pending approval workflows.

    Args:
        project_id: Filter by project (optional).
        reviewer: Filter by reviewer (optional).
        db_path: Override database path.

    Returns:
        dict with pending approvals list.
    """
    conn = _get_connection(db_path)
    try:
        query = """SELECT id, session_id, project_id, approval_type, status,
                          submitted_by, submitted_at, reviewers, current_reviewer,
                          conditions
                   FROM approval_workflows
                   WHERE status IN ('pending', 'in_review')"""
        params = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)

        query += " ORDER BY submitted_at ASC"

        rows = conn.execute(query, params).fetchall()

        pending = []
        for r in rows:
            r_dict = dict(r)
            reviewers_list = json.loads(r_dict["reviewers"] or "[]")

            # Filter by reviewer if specified
            if reviewer and reviewer not in reviewers_list:
                continue

            pending.append({
                "approval_id": r_dict["id"],
                "session_id": r_dict["session_id"],
                "project_id": r_dict["project_id"],
                "approval_type": r_dict["approval_type"],
                "status": r_dict["status"],
                "submitted_by": r_dict["submitted_by"],
                "submitted_at": r_dict["submitted_at"],
                "reviewers": reviewers_list,
                "current_reviewer": r_dict["current_reviewer"],
                "conditions": json.loads(r_dict["conditions"] or "null"),
            })

        return {
            "total_pending": len(pending),
            "filter_project": project_id,
            "filter_reviewer": reviewer,
            "pending": pending,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_approval
# ---------------------------------------------------------------------------

def get_approval(approval_id, db_path=None):
    """Get details of a single approval workflow.

    Args:
        approval_id: Approval workflow identifier.
        db_path: Override database path.

    Returns:
        dict with full approval details.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT id, session_id, project_id, approval_type, status,
                      submitted_by, submitted_at, reviewers, current_reviewer,
                      approval_chain, related_coa_id, conditions,
                      decision_rationale, decided_at, classification,
                      created_at, updated_at
               FROM approval_workflows WHERE id = ?""",
            (approval_id,),
        ).fetchone()
        if not row:
            return {"error": f"Approval not found: {approval_id}"}

        r = dict(row)
        return {
            "approval_id": r["id"],
            "session_id": r["session_id"],
            "project_id": r["project_id"],
            "approval_type": r["approval_type"],
            "status": r["status"],
            "submitted_by": r["submitted_by"],
            "submitted_at": r["submitted_at"],
            "reviewers": json.loads(r["reviewers"] or "[]"),
            "current_reviewer": r["current_reviewer"],
            "approval_chain": json.loads(r["approval_chain"] or "[]"),
            "related_coa_id": r["related_coa_id"],
            "conditions": json.loads(r["conditions"] or "null"),
            "decision_rationale": r["decision_rationale"],
            "decided_at": r["decided_at"],
            "classification": r["classification"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# list_approvals
# ---------------------------------------------------------------------------

def list_approvals(session_id, db_path=None):
    """List all approvals for a session.

    Args:
        session_id: Intake session identifier.
        db_path: Override database path.

    Returns:
        dict with approvals list.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT id, session_id, project_id, approval_type, status,
                      submitted_by, submitted_at, reviewers, decided_at,
                      decision_rationale
               FROM approval_workflows
               WHERE session_id = ?
               ORDER BY submitted_at DESC""",
            (session_id,),
        ).fetchall()

        approvals = []
        for r in rows:
            r_dict = dict(r)
            approvals.append({
                "approval_id": r_dict["id"],
                "session_id": r_dict["session_id"],
                "project_id": r_dict["project_id"],
                "approval_type": r_dict["approval_type"],
                "status": r_dict["status"],
                "submitted_by": r_dict["submitted_by"],
                "submitted_at": r_dict["submitted_at"],
                "reviewers": json.loads(r_dict["reviewers"] or "[]"),
                "decided_at": r_dict["decided_at"],
                "decision_rationale": r_dict["decision_rationale"],
            })

        # Summary counts
        status_counts = {}
        for a in approvals:
            status_counts[a["status"]] = status_counts.get(a["status"], 0) + 1

        return {
            "session_id": session_id,
            "total_approvals": len(approvals),
            "status_counts": status_counts,
            "approvals": approvals,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Approval workflow manager for ICDEV RICOAS"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Actions
    parser.add_argument("--submit", action="store_true", help="Submit for approval")
    parser.add_argument("--review", action="store_true", help="Review an approval")
    parser.add_argument("--pending", action="store_true", help="List pending approvals")
    parser.add_argument("--get", action="store_true", help="Get approval details")
    parser.add_argument("--list", action="store_true", help="List session approvals")

    # Identifiers
    parser.add_argument("--session-id", help="Intake session ID")
    parser.add_argument("--approval-id", help="Approval workflow ID")
    parser.add_argument("--project-id", help="Project ID (for pending filter)")

    # Submit args
    parser.add_argument("--approval-type", help="Approval type")
    parser.add_argument("--submitted-by", help="Who is submitting")
    parser.add_argument("--reviewers", help="Reviewer list (JSON array)")
    parser.add_argument("--conditions", help="Approval conditions (JSON)")

    # Review args
    parser.add_argument("--reviewer", help="Reviewer identity")
    parser.add_argument("--decision", choices=["approved", "rejected", "conditional"],
                        help="Approval decision")
    parser.add_argument("--rationale", help="Decision rationale")

    args = parser.parse_args()

    result = None

    if args.submit:
        if not args.session_id or not args.approval_type or not args.submitted_by or not args.reviewers:
            parser.error("--submit requires --session-id, --approval-type, --submitted-by, and --reviewers")
        conditions = json.loads(args.conditions) if args.conditions else None
        result = submit_for_approval(
            session_id=args.session_id,
            approval_type=args.approval_type,
            submitted_by=args.submitted_by,
            reviewers=args.reviewers,
            conditions=conditions,
        )
    elif args.review:
        if not args.approval_id or not args.reviewer or not args.decision or not args.rationale:
            parser.error("--review requires --approval-id, --reviewer, --decision, and --rationale")
        result = review_approval(
            approval_id=args.approval_id,
            reviewer=args.reviewer,
            decision=args.decision,
            rationale=args.rationale,
        )
    elif args.pending:
        result = get_pending(
            project_id=args.project_id,
            reviewer=args.reviewer,
        )
    elif args.get:
        if not args.approval_id:
            parser.error("--get requires --approval-id")
        result = get_approval(approval_id=args.approval_id)
    elif args.list:
        if not args.session_id:
            parser.error("--list requires --session-id")
        result = list_approvals(session_id=args.session_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
