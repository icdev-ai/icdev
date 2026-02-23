#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Review Queue — Human review workflows for cross-tenant asset sharing.

Manages the review lifecycle for marketplace assets that require ISSO or
security officer approval before publishing to the central vetted catalog.

Supports submit, assign, complete, list, get, and escalate operations with
full audit trail (NIST AU compliant).

Usage:
    # Submit an asset version for review
    python tools/marketplace/review_queue.py --submit \\
        --asset-id "asset-abc" --version-id "ver-abc" --json

    # Assign a reviewer
    python tools/marketplace/review_queue.py --assign \\
        --review-id "rev-abc" --reviewer-id "isso@dod.mil" \\
        --reviewer-role isso --json

    # Complete a review (approve)
    python tools/marketplace/review_queue.py --review \\
        --review-id "rev-abc" --reviewer-id "isso@dod.mil" \\
        --decision approved --rationale "Passed all security gates" --json

    # Complete a review (reject)
    python tools/marketplace/review_queue.py --review \\
        --review-id "rev-abc" --reviewer-id "isso@dod.mil" \\
        --decision rejected --rationale "CUI markings missing" --json

    # Complete a review (conditional approval)
    python tools/marketplace/review_queue.py --review \\
        --review-id "rev-abc" --reviewer-id "isso@dod.mil" \\
        --decision conditional --rationale "Needs dependency pin" \\
        --conditions '{"required": ["Pin openssl to 3.1.x"]}' --json

    # List pending reviews
    python tools/marketplace/review_queue.py --pending --json

    # List pending reviews for a specific reviewer
    python tools/marketplace/review_queue.py --pending \\
        --reviewer-id "isso@dod.mil" --json

    # Get review details
    python tools/marketplace/review_queue.py --get \\
        --review-id "rev-abc" --json

    # Escalate a stale review
    python tools/marketplace/review_queue.py --escalate \\
        --review-id "rev-abc" \\
        --escalation-reason "Review pending >5 days, blocking release" --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Graceful import of catalog_manager for status updates and promotion
try:
    from tools.marketplace.catalog_manager import update_status, promote_to_central
    _HAS_CATALOG = True
except ImportError:
    _HAS_CATALOG = False

    def update_status(asset_id, status, db_path=None):
        """Fallback: direct DB update when catalog_manager unavailable."""
        path = db_path or DB_PATH
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                "UPDATE marketplace_assets SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), asset_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {"asset_id": asset_id, "status": status}

    def promote_to_central(asset_id, db_path=None):
        """Fallback: direct DB update when catalog_manager unavailable."""
        path = db_path or DB_PATH
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                "UPDATE marketplace_assets SET catalog_tier = 'central_vetted', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), asset_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {"asset_id": asset_id, "catalog_tier": "central_vetted"}

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_DECISIONS = {"approved", "rejected", "conditional"}
VALID_REVIEWER_ROLES = {"isso", "security_officer", "tenant_admin", "platform_admin"}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _gen_id(prefix="rev"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, project_id=None, details=None):
    """Write an audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                project_id=project_id,
                details=details,
                db_path=DB_PATH,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# submit_review
# ---------------------------------------------------------------------------

def submit_review(asset_id, version_id, db_path=None):
    """Submit an asset version for human review.

    Creates a new review record with decision='pending' and sets the
    asset status to 'review'.

    Args:
        asset_id: Marketplace asset identifier.
        version_id: Marketplace version identifier.
        db_path: Override database path.

    Returns:
        dict with review_id, asset_id, version_id, and status.
    """
    conn = _get_db(db_path)
    try:
        # Verify asset exists
        asset_row = conn.execute(
            "SELECT id, name, status, publisher_tenant_id FROM marketplace_assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        if not asset_row:
            return {"error": f"Asset not found: {asset_id}"}

        # Verify version exists and belongs to asset
        version_row = conn.execute(
            "SELECT id, version FROM marketplace_versions WHERE id = ? AND asset_id = ?",
            (version_id, asset_id),
        ).fetchone()
        if not version_row:
            return {"error": f"Version not found: {version_id} for asset {asset_id}"}

        review_id = _gen_id("rev")
        now = _now()

        conn.execute(
            """INSERT INTO marketplace_reviews
               (id, asset_id, version_id, decision, submitted_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (review_id, asset_id, version_id, now),
        )
        conn.commit()
    finally:
        conn.close()

    # Update asset status to review
    update_status(asset_id, "review", db_path)

    _audit(
        event_type="marketplace_review_submitted",
        actor="marketplace-review-queue",
        action=f"Submitted asset {asset_id} version {version_id} for review",
        details={
            "review_id": review_id,
            "asset_id": asset_id,
            "version_id": version_id,
            "asset_name": dict(asset_row).get("name", ""),
        },
    )

    return {
        "review_id": review_id,
        "asset_id": asset_id,
        "version_id": version_id,
        "decision": "pending",
        "submitted_at": now,
    }


# ---------------------------------------------------------------------------
# assign_reviewer
# ---------------------------------------------------------------------------

def assign_reviewer(review_id, reviewer_id, reviewer_role, db_path=None):
    """Assign an ISSO or security officer to review an asset.

    Args:
        review_id: Review record identifier.
        reviewer_id: Reviewer identity (email or user ID).
        reviewer_role: One of isso, security_officer, tenant_admin, platform_admin.
        db_path: Override database path.

    Returns:
        dict with review_id, reviewer_id, reviewer_role, and status.
    """
    if reviewer_role not in VALID_REVIEWER_ROLES:
        return {
            "error": f"Invalid reviewer_role '{reviewer_role}'. "
                     f"Must be one of: {', '.join(sorted(VALID_REVIEWER_ROLES))}",
        }

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, decision FROM marketplace_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        if not row:
            return {"error": f"Review not found: {review_id}"}

        if row["decision"] != "pending":
            return {"error": f"Review {review_id} is already {row['decision']}"}

        _now()
        conn.execute(
            """UPDATE marketplace_reviews
               SET reviewer_id = ?, reviewer_role = ?
               WHERE id = ?""",
            (reviewer_id, reviewer_role, review_id),
        )
        conn.commit()
    finally:
        conn.close()

    _audit(
        event_type="marketplace_reviewer_assigned",
        actor="marketplace-review-queue",
        action=f"Assigned {reviewer_role} '{reviewer_id}' to review {review_id}",
        details={
            "review_id": review_id,
            "reviewer_id": reviewer_id,
            "reviewer_role": reviewer_role,
        },
    )

    return {
        "review_id": review_id,
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role,
        "decision": "pending",
    }


# ---------------------------------------------------------------------------
# complete_review
# ---------------------------------------------------------------------------

def complete_review(review_id, reviewer_id, decision, rationale,
                    conditions=None, scan_results_reviewed=True,
                    code_reviewed=True, compliance_reviewed=True,
                    db_path=None):
    """Complete a review with a decision.

    - approved: asset status -> 'published', promoted to central_vetted
    - rejected: asset status -> 'draft'
    - conditional: asset status stays 'review' with conditions recorded

    Args:
        review_id: Review record identifier.
        reviewer_id: Reviewer making the decision.
        decision: One of approved, rejected, conditional.
        rationale: Justification for the decision.
        conditions: Optional dict of conditions (for conditional approval).
        scan_results_reviewed: Whether scan results were reviewed (default True).
        code_reviewed: Whether code was reviewed (default True).
        compliance_reviewed: Whether compliance was reviewed (default True).
        db_path: Override database path.

    Returns:
        dict with review_id, decision, asset status, and details.
    """
    if decision not in VALID_DECISIONS:
        return {
            "error": f"Invalid decision '{decision}'. "
                     f"Must be one of: {', '.join(sorted(VALID_DECISIONS))}",
        }

    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, asset_id, version_id, decision as current_decision FROM marketplace_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        if not row:
            return {"error": f"Review not found: {review_id}"}

        if row["current_decision"] not in ("pending",):
            return {"error": f"Review {review_id} is already {row['current_decision']}"}

        asset_id = row["asset_id"]
        version_id = row["version_id"]
        now = _now()

        # Update review record
        conn.execute(
            """UPDATE marketplace_reviews
               SET reviewer_id = ?, decision = ?, rationale = ?,
                   conditions = ?, scan_results_reviewed = ?,
                   code_reviewed = ?, compliance_reviewed = ?,
                   reviewed_at = ?
               WHERE id = ?""",
            (
                reviewer_id, decision, rationale,
                json.dumps(conditions) if conditions else None,
                1 if scan_results_reviewed else 0,
                1 if code_reviewed else 0,
                1 if compliance_reviewed else 0,
                now, review_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Update asset status based on decision
    if decision == "approved":
        update_status(asset_id, "published", db_path)
        promote_to_central(asset_id, db_path)
        asset_status = "published"
        catalog_tier = "central_vetted"
    elif decision == "rejected":
        update_status(asset_id, "draft", db_path)
        asset_status = "draft"
        catalog_tier = "tenant_local"
    else:
        # conditional — stays in review with conditions
        update_status(asset_id, "review", db_path)
        asset_status = "review"
        catalog_tier = "tenant_local"

    _audit(
        event_type="marketplace_review_completed",
        actor=reviewer_id,
        action=f"Review {review_id} {decision}: {rationale}",
        details={
            "review_id": review_id,
            "asset_id": asset_id,
            "version_id": version_id,
            "decision": decision,
            "rationale": rationale,
            "conditions": conditions,
            "scan_results_reviewed": scan_results_reviewed,
            "code_reviewed": code_reviewed,
            "compliance_reviewed": compliance_reviewed,
        },
    )

    return {
        "review_id": review_id,
        "asset_id": asset_id,
        "version_id": version_id,
        "decision": decision,
        "rationale": rationale,
        "conditions": conditions,
        "asset_status": asset_status,
        "catalog_tier": catalog_tier,
        "reviewer_id": reviewer_id,
        "reviewed_at": now,
    }


# ---------------------------------------------------------------------------
# list_pending
# ---------------------------------------------------------------------------

def list_pending(reviewer_id=None, db_path=None):
    """List pending reviews with full asset and version context.

    Joins marketplace_reviews with marketplace_assets and marketplace_versions
    to provide complete context for each pending review.

    Args:
        reviewer_id: Optional filter by assigned reviewer.
        db_path: Override database path.

    Returns:
        dict with pending review list and count.
    """
    conn = _get_db(db_path)
    try:
        query = """
            SELECT
                r.id AS review_id,
                r.asset_id,
                r.version_id,
                r.reviewer_id,
                r.reviewer_role,
                r.decision,
                r.submitted_at,
                a.name AS asset_name,
                a.asset_type,
                a.slug,
                a.impact_level,
                a.classification,
                a.publisher_tenant_id,
                a.publisher_user,
                v.version,
                v.changelog,
                v.sha256_hash
            FROM marketplace_reviews r
            JOIN marketplace_assets a ON r.asset_id = a.id
            JOIN marketplace_versions v ON r.version_id = v.id
            WHERE r.decision = 'pending'
        """
        params = []

        if reviewer_id:
            query += " AND r.reviewer_id = ?"
            params.append(reviewer_id)

        query += " ORDER BY r.submitted_at ASC"

        rows = conn.execute(query, params).fetchall()

        pending = []
        for r in rows:
            pending.append({
                "review_id": r["review_id"],
                "asset_id": r["asset_id"],
                "version_id": r["version_id"],
                "reviewer_id": r["reviewer_id"],
                "reviewer_role": r["reviewer_role"],
                "decision": r["decision"],
                "submitted_at": r["submitted_at"],
                "asset_name": r["asset_name"],
                "asset_type": r["asset_type"],
                "slug": r["slug"],
                "impact_level": r["impact_level"],
                "classification": r["classification"],
                "publisher_tenant_id": r["publisher_tenant_id"],
                "publisher_user": r["publisher_user"],
                "version": r["version"],
                "changelog": r["changelog"],
                "sha256_hash": r["sha256_hash"],
            })

        return {
            "total_pending": len(pending),
            "filter_reviewer": reviewer_id,
            "pending": pending,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_review
# ---------------------------------------------------------------------------

def get_review(review_id, db_path=None):
    """Get full review details including scan results.

    Args:
        review_id: Review record identifier.
        db_path: Override database path.

    Returns:
        dict with full review details, asset info, and scan results.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            """SELECT r.*,
                      a.name AS asset_name, a.asset_type, a.slug,
                      a.impact_level, a.classification,
                      a.publisher_tenant_id, a.publisher_user, a.catalog_tier,
                      v.version, v.changelog, v.sha256_hash, v.file_size_bytes
               FROM marketplace_reviews r
               JOIN marketplace_assets a ON r.asset_id = a.id
               JOIN marketplace_versions v ON r.version_id = v.id
               WHERE r.id = ?""",
            (review_id,),
        ).fetchone()

        if not row:
            return {"error": f"Review not found: {review_id}"}

        r = dict(row)

        # Parse conditions JSON
        conditions = None
        if r.get("conditions"):
            try:
                conditions = json.loads(r["conditions"])
            except (json.JSONDecodeError, TypeError):
                conditions = r["conditions"]

        # Fetch scan results for this version
        scans = conn.execute(
            """SELECT gate_name, status, findings_count,
                      critical_count, high_count, medium_count, low_count,
                      details, scanned_at
               FROM marketplace_scan_results
               WHERE asset_id = ? AND version_id = ?
               ORDER BY scanned_at DESC""",
            (r["asset_id"], r["version_id"]),
        ).fetchall()

        scan_results = []
        for s in scans:
            s_dict = dict(s)
            if s_dict.get("details"):
                try:
                    s_dict["details"] = json.loads(s_dict["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            scan_results.append(s_dict)

        return {
            "review_id": r["id"],
            "asset_id": r["asset_id"],
            "version_id": r["version_id"],
            "reviewer_id": r.get("reviewer_id"),
            "reviewer_role": r.get("reviewer_role"),
            "decision": r["decision"],
            "rationale": r.get("rationale"),
            "conditions": conditions,
            "scan_results_reviewed": bool(r.get("scan_results_reviewed")),
            "code_reviewed": bool(r.get("code_reviewed")),
            "compliance_reviewed": bool(r.get("compliance_reviewed")),
            "submitted_at": r["submitted_at"],
            "reviewed_at": r.get("reviewed_at"),
            "asset_name": r["asset_name"],
            "asset_type": r["asset_type"],
            "slug": r["slug"],
            "impact_level": r["impact_level"],
            "classification": r["classification"],
            "publisher_tenant_id": r["publisher_tenant_id"],
            "publisher_user": r["publisher_user"],
            "catalog_tier": r["catalog_tier"],
            "version": r["version"],
            "changelog": r["changelog"],
            "sha256_hash": r["sha256_hash"],
            "file_size_bytes": r.get("file_size_bytes"),
            "scan_results": scan_results,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# escalate_review
# ---------------------------------------------------------------------------

def escalate_review(review_id, escalation_reason, db_path=None):
    """Escalate a stale or blocked review.

    Records the escalation in the audit trail and flags the review
    for priority attention.

    Args:
        review_id: Review record identifier.
        escalation_reason: Justification for escalation.
        db_path: Override database path.

    Returns:
        dict with review_id, escalation details, and status.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            """SELECT id, asset_id, version_id, reviewer_id, reviewer_role,
                      decision, submitted_at
               FROM marketplace_reviews WHERE id = ?""",
            (review_id,),
        ).fetchone()
        if not row:
            return {"error": f"Review not found: {review_id}"}

        if row["decision"] != "pending":
            return {"error": f"Review {review_id} is already {row['decision']}, cannot escalate"}

        now = _now()

        # Calculate days pending
        submitted_at = row["submitted_at"]
        try:
            submitted_dt = datetime.strptime(submitted_at, "%Y-%m-%dT%H:%M:%SZ")
            days_pending = (datetime.now(timezone.utc) - submitted_dt).days
        except (ValueError, TypeError):
            days_pending = -1

    finally:
        conn.close()

    _audit(
        event_type="marketplace_review_escalated",
        actor="marketplace-review-queue",
        action=f"Escalated review {review_id}: {escalation_reason}",
        details={
            "review_id": review_id,
            "asset_id": row["asset_id"],
            "version_id": row["version_id"],
            "reviewer_id": row["reviewer_id"],
            "reviewer_role": row["reviewer_role"],
            "escalation_reason": escalation_reason,
            "days_pending": days_pending,
            "escalated_at": now,
        },
    )

    return {
        "review_id": review_id,
        "asset_id": row["asset_id"],
        "version_id": row["version_id"],
        "reviewer_id": row["reviewer_id"],
        "reviewer_role": row["reviewer_role"],
        "escalation_reason": escalation_reason,
        "days_pending": days_pending,
        "escalated_at": now,
        "status": "escalated",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Marketplace Review Queue — human review for cross-tenant asset sharing"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", type=Path, default=None, help="Override database path")

    # Actions
    parser.add_argument("--submit", action="store_true", help="Submit asset for review")
    parser.add_argument("--assign", action="store_true", help="Assign reviewer to a review")
    parser.add_argument("--review", action="store_true", help="Complete a review with decision")
    parser.add_argument("--pending", action="store_true", help="List pending reviews")
    parser.add_argument("--get", action="store_true", help="Get review details")
    parser.add_argument("--escalate", action="store_true", help="Escalate a stale review")

    # Identifiers
    parser.add_argument("--review-id", help="Review record ID")
    parser.add_argument("--asset-id", help="Marketplace asset ID")
    parser.add_argument("--version-id", help="Marketplace version ID")
    parser.add_argument("--reviewer-id", help="Reviewer identity (email or user ID)")

    # Assign args
    parser.add_argument("--reviewer-role",
                        choices=sorted(VALID_REVIEWER_ROLES),
                        help="Reviewer role")

    # Review (complete) args
    parser.add_argument("--decision",
                        choices=sorted(VALID_DECISIONS),
                        help="Review decision")
    parser.add_argument("--rationale", help="Decision rationale")
    parser.add_argument("--conditions", help="Conditions JSON (for conditional approval)")
    parser.add_argument("--no-scan-review", action="store_true",
                        help="Mark scan results as not reviewed")
    parser.add_argument("--no-code-review", action="store_true",
                        help="Mark code as not reviewed")
    parser.add_argument("--no-compliance-review", action="store_true",
                        help="Mark compliance as not reviewed")

    # Escalate args
    parser.add_argument("--escalation-reason", help="Reason for escalation")

    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    try:
        result = None

        if args.submit:
            if not args.asset_id or not args.version_id:
                parser.error("--submit requires --asset-id and --version-id")
            result = submit_review(
                asset_id=args.asset_id,
                version_id=args.version_id,
                db_path=db_path,
            )

        elif args.assign:
            if not args.review_id or not args.reviewer_id or not args.reviewer_role:
                parser.error("--assign requires --review-id, --reviewer-id, and --reviewer-role")
            result = assign_reviewer(
                review_id=args.review_id,
                reviewer_id=args.reviewer_id,
                reviewer_role=args.reviewer_role,
                db_path=db_path,
            )

        elif args.review:
            if not args.review_id or not args.reviewer_id or not args.decision or not args.rationale:
                parser.error("--review requires --review-id, --reviewer-id, --decision, and --rationale")
            conditions = json.loads(args.conditions) if args.conditions else None
            result = complete_review(
                review_id=args.review_id,
                reviewer_id=args.reviewer_id,
                decision=args.decision,
                rationale=args.rationale,
                conditions=conditions,
                scan_results_reviewed=not args.no_scan_review,
                code_reviewed=not args.no_code_review,
                compliance_reviewed=not args.no_compliance_review,
                db_path=db_path,
            )

        elif args.pending:
            result = list_pending(
                reviewer_id=args.reviewer_id,
                db_path=db_path,
            )

        elif args.get:
            if not args.review_id:
                parser.error("--get requires --review-id")
            result = get_review(
                review_id=args.review_id,
                db_path=db_path,
            )

        elif args.escalate:
            if not args.review_id or not args.escalation_reason:
                parser.error("--escalate requires --review-id and --escalation-reason")
            result = escalate_review(
                review_id=args.review_id,
                escalation_reason=args.escalation_reason,
                db_path=db_path,
            )

        else:
            parser.print_help()
            return

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, dict):
                for key, value in result.items():
                    print(f"  {key}: {value}")
            else:
                print(result)

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
