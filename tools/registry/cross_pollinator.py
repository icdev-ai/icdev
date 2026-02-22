#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Cross-Pollinator -- brokers capability sharing between child applications.

Capabilities proven in one child can be proposed for adoption by siblings.
All cross-pollination requires human-in-the-loop approval (REQ-36-040).

ADR D6: All proposals, approvals, and executions are append-only audit entries.
ADR D212: Cross-pollinated capabilities must also pass the 72-hour stability
    window before the target child considers them stable.
ADR D213: Cross-pollination draws from the same child_learned_behaviors data
    as the absorption engine but targets sibling children instead of the
    parent genome.

Pipeline:
    1. find_candidates()        -- discover shareable capabilities
    2. propose_pollination()    -- create a HITL proposal
    3. approve_pollination()    -- HITL approves the proposal
    4. execute_pollination()    -- add capability to target children
    5. get_proposals()          -- query proposal status

Usage:
    python tools/registry/cross_pollinator.py --find-candidates --json
    python tools/registry/cross_pollinator.py --find-candidates \
        --source-child-id "child-abc123" --json

    python tools/registry/cross_pollinator.py --propose \
        --source-child-id "child-abc123" \
        --capability-name "stig_cache_optimization" \
        --target-child-ids "child-def456,child-ghi789" \
        --proposed-by "architect@mil" --json

    python tools/registry/cross_pollinator.py --approve \
        --proposal-id "xp-abc12345" --approver "isso@mil" --json

    python tools/registry/cross_pollinator.py --execute \
        --proposal-id "xp-abc12345" --json

    python tools/registry/cross_pollinator.py --list-proposals --json
    python tools/registry/cross_pollinator.py --list-proposals --status proposed --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1

try:
    from tools.registry.child_registry import ChildRegistry
    _HAS_REGISTRY = True
except ImportError:
    _HAS_REGISTRY = False

try:
    from tools.security.prompt_injection_detector import PromptInjectionDetector
    _pid = PromptInjectionDetector()
except Exception:
    _pid = None


# =========================================================================
# CONSTANTS
# =========================================================================
CROSS_POLLINATION_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS cross_pollination_proposals (
    id TEXT PRIMARY KEY,
    source_child_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    target_child_ids TEXT NOT NULL,
    proposed_by TEXT NOT NULL DEFAULT 'system',
    approver TEXT,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed', 'approved', 'rejected',
                         'executing', 'completed', 'failed',
                         'cancelled')),
    compatibility_scores_json TEXT DEFAULT '{}',
    rationale TEXT,
    rejection_reason TEXT,
    proposed_at TEXT NOT NULL,
    approved_at TEXT,
    executed_at TEXT,
    classification TEXT DEFAULT 'CUI'
);

CREATE INDEX IF NOT EXISTS idx_xpoll_source
    ON cross_pollination_proposals(source_child_id);
CREATE INDEX IF NOT EXISTS idx_xpoll_status
    ON cross_pollination_proposals(status);
CREATE INDEX IF NOT EXISTS idx_xpoll_proposed_at
    ON cross_pollination_proposals(proposed_at);
"""

VALID_PROPOSAL_STATUSES = (
    "proposed", "approved", "rejected",
    "executing", "completed", "failed", "cancelled",
)


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix="xp"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="cross-pollinator",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="icdev-genome",
            )
        except Exception:
            pass


# =========================================================================
# CROSS POLLINATOR
# =========================================================================
class CrossPollinator:
    """Brokers capability sharing between child applications.

    Capabilities proven in one child can be proposed for adoption by sibling
    children. All cross-pollination requires human-in-the-loop (HITL) approval
    per REQ-36-040.

    The cross-pollination pipeline:
        1. find_candidates()       -- discover shareable capabilities
        2. propose_pollination()   -- create a proposal (status='proposed')
        3. approve_pollination()   -- HITL approval (status='approved')
        4. execute_pollination()   -- propagate to targets (status='completed')
        5. get_proposals()         -- query proposals by status
    """

    def __init__(self, db_path=None):
        """Initialize CrossPollinator.

        Args:
            db_path: Path to SQLite database. Defaults to data/icdev.db.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_tables()

    def _get_conn(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self):
        """Ensure required tables exist."""
        ddl = CROSS_POLLINATION_PROPOSALS_DDL + """
        CREATE TABLE IF NOT EXISTS child_learned_behaviors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            child_id TEXT NOT NULL,
            behavior_type TEXT NOT NULL
                CHECK(behavior_type IN ('optimization', 'error_recovery',
                                        'compliance_shortcut', 'performance_tuning',
                                        'security_pattern', 'workflow_improvement',
                                        'configuration', 'other')),
            description TEXT NOT NULL,
            evidence_json TEXT DEFAULT '{}',
            confidence REAL DEFAULT 0.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
            evaluated INTEGER DEFAULT 0,
            absorbed INTEGER DEFAULT 0,
            discovered_at TEXT DEFAULT (datetime('now')),
            evaluated_at TEXT,
            absorbed_at TEXT,
            classification TEXT DEFAULT 'CUI'
        );

        CREATE TABLE IF NOT EXISTS child_app_registry (
            id TEXT PRIMARY KEY,
            parent_project_id TEXT,
            child_name TEXT,
            child_type TEXT DEFAULT 'microservice',
            project_path TEXT,
            target_cloud TEXT DEFAULT 'aws',
            compliance_required INTEGER DEFAULT 1,
            blueprint_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'registered',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS child_capabilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            child_id TEXT NOT NULL,
            capability_name TEXT NOT NULL,
            version TEXT DEFAULT '1.0.0',
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'parent',
            learned_at TEXT DEFAULT (datetime('now')),
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(child_id, capability_name)
        );

        CREATE TABLE IF NOT EXISTS propagation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            capability_name TEXT NOT NULL,
            genome_version TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_child_id TEXT,
            target_child_id TEXT NOT NULL,
            propagation_status TEXT DEFAULT 'pending',
            evaluation_id INTEGER,
            staging_env_id TEXT,
            error_details TEXT,
            initiated_by TEXT DEFAULT 'evolution-engine',
            initiated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            classification TEXT DEFAULT 'CUI'
        );
        """
        try:
            conn = self._get_conn()
            conn.executescript(ddl)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Table creation failed: {e}", file=sys.stderr)

    def _log_audit_event(self, conn, child_id, event_type, details):
        """Log an audit event for injection detection (append-only, D6)."""
        _audit(
            f"cross_pollination.{event_type}",
            f"Child {child_id}: {details}",
            {"child_id": child_id, "event_type": event_type, "details": details},
        )

    def find_candidates(self, source_child_id: str = None) -> list:
        """Find capabilities from children that could benefit siblings.

        Scans child_learned_behaviors for evaluated behaviors with high
        confidence, then identifies sibling children that do not already
        have those capabilities.

        Args:
            source_child_id: Optional filter to only consider capabilities
                from a specific child. If None, considers all children.

        Returns:
            List of candidate dicts, each containing:
                source_child (str): Child ID that discovered the capability
                capability (dict): Behavior details
                candidate_targets (list): Sibling child IDs that could benefit
                compatibility_score (float): Estimated compatibility (0.0-1.0)
        """
        conn = self._get_conn()
        try:
            # Find evaluated behaviors with reasonable confidence
            query = """
                SELECT id, child_id, behavior_type, description,
                       evidence_json, confidence, absorbed
                FROM child_learned_behaviors
                WHERE evaluated = 1
                  AND confidence >= 0.5
            """
            params = []

            if source_child_id:
                query += " AND child_id = ?"
                params.append(source_child_id)

            query += " ORDER BY confidence DESC"

            behaviors = conn.execute(query, params).fetchall()

            if not behaviors:
                return []

            # Get all active children
            children = conn.execute(
                """SELECT id, child_name, child_type, compliance_required
                   FROM child_app_registry
                   WHERE status = 'active'"""
            ).fetchall()

            child_map = {row["id"]: dict(row) for row in children}

            # For each behavior, find sibling targets that don't have it
            candidates = []
            for beh in behaviors:
                beh_dict = dict(beh)
                source_cid = beh_dict["child_id"]

                # Parse evidence
                try:
                    evidence = json.loads(beh_dict.get("evidence_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    evidence = {}

                # Phase 37 integration: scan evidence for injection
                if _pid is not None and evidence:
                    evidence_text = json.dumps(evidence) if isinstance(evidence, dict) else str(evidence)
                    scan_result = _pid.scan_text(evidence_text, source="cross_pollination_candidate")
                    if scan_result.get("detected") and scan_result.get("confidence", 0) >= 0.7:
                        # Skip this candidate -- injection detected
                        self._log_audit_event(
                            conn, beh_dict.get("child_id", "unknown"),
                            "cross_pollination_rejected",
                            f"Injection in evidence (confidence={scan_result['confidence']:.2f})"
                        )
                        continue

                # Find siblings without this capability
                existing_caps = conn.execute(
                    """SELECT DISTINCT child_id
                       FROM child_capabilities
                       WHERE capability_name = ?
                         AND status = 'active'""",
                    (beh_dict["description"][:200],),
                ).fetchall()

                children_with_cap = {row["child_id"] for row in existing_caps}
                children_with_cap.add(source_cid)  # Source already has it

                target_ids = []
                for cid in child_map:
                    if cid not in children_with_cap:
                        target_ids.append(cid)

                if not target_ids:
                    continue

                # Compute compatibility score
                compatibility = self._compute_compatibility(
                    beh_dict, child_map, target_ids
                )

                candidates.append({
                    "source_child": source_cid,
                    "behavior_id": beh_dict["id"],
                    "capability": {
                        "behavior_type": beh_dict["behavior_type"],
                        "description": beh_dict["description"],
                        "confidence": beh_dict["confidence"],
                        "absorbed": bool(beh_dict.get("absorbed", 0)),
                    },
                    "candidate_targets": target_ids,
                    "target_count": len(target_ids),
                    "compatibility_score": compatibility,
                })

            # Sort by compatibility score descending
            candidates.sort(key=lambda c: c["compatibility_score"], reverse=True)

            return candidates

        finally:
            conn.close()

    def _compute_compatibility(
        self, behavior: dict, child_map: dict, target_ids: list
    ) -> float:
        """Compute compatibility score for cross-pollination.

        Considers behavior confidence, number of potential targets, and
        whether targets share compliance requirements with the source.

        Args:
            behavior: Behavior record dict.
            child_map: Dict of child_id -> child record.
            target_ids: List of target child IDs.

        Returns:
            Compatibility score between 0.0 and 1.0.
        """
        confidence = behavior.get("confidence", 0.5)
        source_cid = behavior.get("child_id", "")
        source_child = child_map.get(source_cid, {})
        source_compliance = source_child.get("compliance_required", 1)
        source_type = source_child.get("child_type", "")

        # Base score from confidence
        score = confidence * 0.50

        # Target coverage: more targets = more universal
        total_children = len(child_map)
        if total_children > 0:
            coverage = len(target_ids) / total_children
            score += coverage * 0.20

        # Compliance compatibility: if source requires compliance and targets
        # also require it, the behavior is more likely compatible
        compatible_count = 0
        for tid in target_ids:
            target = child_map.get(tid, {})
            if target.get("compliance_required", 1) == source_compliance:
                compatible_count += 1
            if target.get("child_type", "") == source_type:
                compatible_count += 1

        if target_ids:
            compat_ratio = compatible_count / (len(target_ids) * 2)
            score += compat_ratio * 0.20

        # Behavior type bonus: security and compliance behaviors are more universally useful
        btype = behavior.get("behavior_type", "other")
        universal_types = ("security_pattern", "compliance_shortcut", "error_recovery")
        if btype in universal_types:
            score += 0.10

        return round(min(score, 1.0), 4)

    def propose_pollination(
        self,
        source_child_id: str,
        capability_name: str,
        target_child_ids: list,
        proposed_by: str = "system",
        rationale: str = None,
    ) -> Optional[dict]:
        """Create a cross-pollination proposal.

        Records the proposal in cross_pollination_proposals and propagation_log
        with status='proposed'. Requires HITL approval before execution.

        Args:
            source_child_id: Child ID that discovered the capability.
            capability_name: Name/description of the capability to share.
            target_child_ids: List of sibling child IDs to receive the capability.
            proposed_by: Identity of the person/system creating the proposal.
            rationale: Optional human-readable rationale for the proposal.

        Returns:
            Dict with proposal details, or None on failure.
        """
        if not target_child_ids:
            return {"error": "No target children specified"}

        proposal_id = _generate_id("xp")
        now = _now()

        conn = self._get_conn()
        try:
            # Compute compatibility scores for each target
            compatibility_scores = {}
            for tid in target_child_ids:
                # Simple compatibility check: verify target exists
                target = conn.execute(
                    "SELECT id, child_name FROM child_app_registry WHERE id = ?",
                    (tid,),
                ).fetchone()
                if target:
                    compatibility_scores[tid] = {
                        "child_name": target["child_name"],
                        "status": "pending_approval",
                    }
                else:
                    compatibility_scores[tid] = {
                        "child_name": "unknown",
                        "status": "target_not_found",
                    }

            # Insert proposal (append-only)
            conn.execute(
                """INSERT INTO cross_pollination_proposals
                   (id, source_child_id, capability_name, target_child_ids,
                    proposed_by, status, compatibility_scores_json,
                    rationale, proposed_at, classification)
                   VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, 'CUI')""",
                (
                    proposal_id,
                    source_child_id,
                    capability_name,
                    json.dumps(target_child_ids),
                    proposed_by,
                    json.dumps(compatibility_scores),
                    rationale,
                    now,
                ),
            )

            # Record in propagation_log for each target (append-only, D6)
            for tid in target_child_ids:
                conn.execute(
                    """INSERT INTO propagation_log
                       (capability_name, genome_version, source_type,
                        source_child_id, target_child_id,
                        propagation_status, initiated_by,
                        initiated_at, classification)
                       VALUES (?, 'cross_pollination', 'child_learned', ?, ?,
                               'pending', ?, ?, 'CUI')""",
                    (
                        capability_name,
                        source_child_id,
                        tid,
                        proposed_by,
                        now,
                    ),
                )

            conn.commit()

            result = {
                "proposal_id": proposal_id,
                "source_child_id": source_child_id,
                "capability_name": capability_name,
                "target_child_ids": target_child_ids,
                "target_count": len(target_child_ids),
                "proposed_by": proposed_by,
                "status": "proposed",
                "rationale": rationale,
                "proposed_at": now,
            }

            _audit(
                "cross_pollination.proposed",
                f"Cross-pollination proposed: '{capability_name}' from "
                f"{source_child_id} to {len(target_child_ids)} targets",
                result,
            )

            return result

        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def approve_pollination(self, proposal_id: str, approver: str) -> bool:
        """Approve a cross-pollination proposal (HITL gate, REQ-36-040).

        Only proposals in 'proposed' status can be approved.

        Args:
            proposal_id: Proposal ID to approve.
            approver: Identity of the human approver.

        Returns:
            True if approval succeeded, False otherwise.
        """
        now = _now()
        conn = self._get_conn()
        try:
            # Verify proposal exists and is in 'proposed' status
            row = conn.execute(
                "SELECT * FROM cross_pollination_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()

            if not row:
                print(
                    f"Warning: Proposal {proposal_id} not found",
                    file=sys.stderr,
                )
                return False

            proposal = dict(row)
            if proposal["status"] != "proposed":
                print(
                    f"Warning: Proposal {proposal_id} is in status "
                    f"'{proposal['status']}', cannot approve",
                    file=sys.stderr,
                )
                return False

            # Update status to approved
            conn.execute(
                """UPDATE cross_pollination_proposals
                   SET status = 'approved', approver = ?, approved_at = ?
                   WHERE id = ?""",
                (approver, now, proposal_id),
            )
            conn.commit()

            _audit(
                "cross_pollination.approved",
                f"Cross-pollination {proposal_id} approved by {approver}",
                {
                    "proposal_id": proposal_id,
                    "approver": approver,
                    "capability_name": proposal.get("capability_name", ""),
                    "approved_at": now,
                },
            )

            return True

        except Exception as e:
            print(f"Warning: Approval failed: {e}", file=sys.stderr)
            return False
        finally:
            conn.close()

    def execute_pollination(self, proposal_id: str) -> dict:
        """Execute an approved cross-pollination proposal.

        Adds the capability to each target child via child_capabilities table
        (or ChildRegistry if available). The proposal must be in 'approved'
        status.

        Args:
            proposal_id: Proposal ID to execute.

        Returns:
            Dict with execution results per target child.
        """
        conn = self._get_conn()
        try:
            # Fetch proposal
            row = conn.execute(
                "SELECT * FROM cross_pollination_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()

            if not row:
                return {"error": f"Proposal {proposal_id} not found"}

            proposal = dict(row)

            if proposal["status"] != "approved":
                return {
                    "error": (
                        f"Proposal {proposal_id} is in status "
                        f"'{proposal['status']}'. Must be 'approved' to execute."
                    ),
                }

            # Update status to executing
            conn.execute(
                """UPDATE cross_pollination_proposals
                   SET status = 'executing'
                   WHERE id = ?""",
                (proposal_id,),
            )
            conn.commit()

            # Parse target children
            try:
                target_ids = json.loads(proposal.get("target_child_ids", "[]"))
            except (json.JSONDecodeError, TypeError):
                target_ids = []

            capability_name = proposal.get("capability_name", "")
            source_child_id = proposal.get("source_child_id", "")
            now = _now()

            # Execute propagation to each target
            results_per_target = {}
            all_success = True

            for tid in target_ids:
                try:
                    if _HAS_REGISTRY:
                        # Use ChildRegistry to add capability
                        registry = ChildRegistry(db_path=self.db_path)
                        registry.add_capability(
                            child_id=tid,
                            capability_name=capability_name,
                            version="1.0.0",
                            source="learned",
                            metadata={
                                "source_child": source_child_id,
                                "proposal_id": proposal_id,
                                "pollinated_at": now,
                            },
                        )
                    else:
                        # Fallback: insert directly into child_capabilities
                        conn.execute(
                            """INSERT OR REPLACE INTO child_capabilities
                               (child_id, capability_name, version, status,
                                source, learned_at, metadata, updated_at)
                               VALUES (?, ?, '1.0.0', 'active', 'learned',
                                       ?, ?, ?)""",
                            (
                                tid,
                                capability_name,
                                now,
                                json.dumps({
                                    "source_child": source_child_id,
                                    "proposal_id": proposal_id,
                                    "pollinated_at": now,
                                }),
                                now,
                            ),
                        )

                    # Update propagation_log for this target
                    conn.execute(
                        """UPDATE propagation_log
                           SET propagation_status = 'success', completed_at = ?
                           WHERE capability_name = ?
                             AND source_child_id = ?
                             AND target_child_id = ?
                             AND propagation_status = 'pending'""",
                        (now, capability_name, source_child_id, tid),
                    )

                    results_per_target[tid] = {"status": "success"}

                except Exception as e:
                    results_per_target[tid] = {
                        "status": "failed",
                        "error": str(e),
                    }
                    all_success = False

                    # Update propagation_log for failure
                    conn.execute(
                        """UPDATE propagation_log
                           SET propagation_status = 'failed',
                               error_details = ?,
                               completed_at = ?
                           WHERE capability_name = ?
                             AND source_child_id = ?
                             AND target_child_id = ?
                             AND propagation_status = 'pending'""",
                        (str(e), now, capability_name, source_child_id, tid),
                    )

            # Update proposal status
            final_status = "completed" if all_success else "failed"
            conn.execute(
                """UPDATE cross_pollination_proposals
                   SET status = ?, executed_at = ?
                   WHERE id = ?""",
                (final_status, now, proposal_id),
            )
            conn.commit()

            result = {
                "proposal_id": proposal_id,
                "capability_name": capability_name,
                "source_child_id": source_child_id,
                "status": final_status,
                "results": results_per_target,
                "targets_succeeded": sum(
                    1 for r in results_per_target.values()
                    if r["status"] == "success"
                ),
                "targets_failed": sum(
                    1 for r in results_per_target.values()
                    if r["status"] == "failed"
                ),
                "executed_at": now,
            }

            _audit(
                "cross_pollination.executed",
                f"Cross-pollination {proposal_id} {final_status}: "
                f"{result['targets_succeeded']}/{len(target_ids)} targets succeeded",
                result,
            )

            return result

        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def get_proposals(self, status: str = None) -> list:
        """List cross-pollination proposals, optionally filtered by status.

        Args:
            status: Optional status filter. Must be one of VALID_PROPOSAL_STATUSES.

        Returns:
            List of proposal dicts ordered by proposed_at descending.
        """
        conn = self._get_conn()
        try:
            query = """
                SELECT id, source_child_id, capability_name, target_child_ids,
                       proposed_by, approver, status, rationale,
                       rejection_reason, proposed_at, approved_at, executed_at
                FROM cross_pollination_proposals
            """
            params = []

            if status:
                if status not in VALID_PROPOSAL_STATUSES:
                    return [{"error": f"Invalid status '{status}'. Must be one of: "
                             f"{', '.join(VALID_PROPOSAL_STATUSES)}"}]
                query += " WHERE status = ?"
                params.append(status)

            query += " ORDER BY proposed_at DESC"

            rows = conn.execute(query, params).fetchall()

            results = []
            for row in rows:
                record = dict(row)
                # Parse target_child_ids from JSON
                try:
                    record["target_child_ids"] = json.loads(
                        record.get("target_child_ids", "[]")
                    )
                except (json.JSONDecodeError, TypeError):
                    record["target_child_ids"] = []
                record["target_count"] = len(record["target_child_ids"])
                results.append(record)

            return results

        finally:
            conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "ICDEV Cross-Pollinator -- broker capability sharing "
            "between child applications (REQ-36-040, HITL required)"
        )
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--find-candidates", action="store_true",
        help="Find capabilities suitable for cross-pollination",
    )
    group.add_argument(
        "--propose", action="store_true",
        help="Create a cross-pollination proposal",
    )
    group.add_argument(
        "--approve", action="store_true",
        help="Approve a cross-pollination proposal (HITL)",
    )
    group.add_argument(
        "--execute", action="store_true",
        help="Execute an approved cross-pollination",
    )
    group.add_argument(
        "--list-proposals", action="store_true",
        help="List cross-pollination proposals",
    )

    # Candidate search args
    parser.add_argument(
        "--source-child-id",
        help="Source child ID (for --find-candidates, --propose)",
    )

    # Proposal creation args
    parser.add_argument("--capability-name", help="Capability name (for --propose)")
    parser.add_argument(
        "--target-child-ids",
        help="Comma-separated target child IDs (for --propose)",
    )
    parser.add_argument(
        "--proposed-by", default="system",
        help="Proposer identity (for --propose)",
    )
    parser.add_argument("--rationale", help="Proposal rationale (for --propose)")

    # Approval/execution args
    parser.add_argument("--proposal-id", help="Proposal ID (for --approve, --execute)")
    parser.add_argument("--approver", help="Approver identity (for --approve)")

    # Filter args
    parser.add_argument(
        "--status",
        choices=VALID_PROPOSAL_STATUSES,
        help="Filter proposals by status (for --list-proposals)",
    )

    args = parser.parse_args()

    try:
        pollinator = CrossPollinator(db_path=args.db_path)

        if args.find_candidates:
            result = pollinator.find_candidates(
                source_child_id=args.source_child_id
            )

        elif args.propose:
            if not args.source_child_id:
                parser.error("--propose requires --source-child-id")
            if not args.capability_name:
                parser.error("--propose requires --capability-name")
            if not args.target_child_ids:
                parser.error("--propose requires --target-child-ids")

            target_ids = [
                tid.strip()
                for tid in args.target_child_ids.split(",")
                if tid.strip()
            ]

            result = pollinator.propose_pollination(
                source_child_id=args.source_child_id,
                capability_name=args.capability_name,
                target_child_ids=target_ids,
                proposed_by=args.proposed_by,
                rationale=args.rationale,
            )

        elif args.approve:
            if not args.proposal_id:
                parser.error("--approve requires --proposal-id")
            if not args.approver:
                parser.error("--approve requires --approver")

            success = pollinator.approve_pollination(
                proposal_id=args.proposal_id,
                approver=args.approver,
            )
            result = {
                "proposal_id": args.proposal_id,
                "approved": success,
                "approver": args.approver,
                "approved_at": _now() if success else None,
            }

        elif args.execute:
            if not args.proposal_id:
                parser.error("--execute requires --proposal-id")
            result = pollinator.execute_pollination(
                proposal_id=args.proposal_id
            )

        elif args.list_proposals:
            result = pollinator.get_proposals(status=args.status)

        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if args.find_candidates and isinstance(result, list):
                print("Cross-Pollination Candidates")
                print("=" * 80)
                if not result:
                    print("  No candidates found")
                for cand in result:
                    cap = cand.get("capability", {})
                    print(
                        f"  Source: {cand.get('source_child', '?'):16s}  "
                        f"Type: {cap.get('behavior_type', '?'):20s}  "
                        f"Conf: {cap.get('confidence', 0):.2f}  "
                        f"Targets: {cand.get('target_count', 0)}  "
                        f"Compat: {cand.get('compatibility_score', 0):.4f}"
                    )
                    desc = cap.get("description", "")[:60]
                    if desc:
                        print(f"         {desc}")

            elif args.propose and isinstance(result, dict):
                if "error" in result:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                else:
                    print(f"Proposal Created: {result.get('proposal_id')}")
                    print(f"  Capability:     {result.get('capability_name')}")
                    print(f"  Source:         {result.get('source_child_id')}")
                    print(f"  Targets:        {result.get('target_count', 0)}")
                    print(f"  Status:         {result.get('status')}")
                    print(f"  Proposed By:    {result.get('proposed_by')}")
                    print("  NOTE: Requires HITL approval before execution")

            elif args.approve and isinstance(result, dict):
                ok = result.get("approved", False)
                print(f"Approval: {'APPROVED' if ok else 'FAILED'}")
                print(f"  Proposal: {result.get('proposal_id')}")
                print(f"  Approver: {result.get('approver')}")

            elif args.execute and isinstance(result, dict):
                if "error" in result:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                else:
                    status = result.get("status", "unknown")
                    print(f"Execution: {status.upper()}")
                    print(f"  Proposal:   {result.get('proposal_id')}")
                    print(f"  Capability: {result.get('capability_name')}")
                    print(
                        f"  Succeeded:  {result.get('targets_succeeded', 0)}"
                        f"/{result.get('targets_succeeded', 0) + result.get('targets_failed', 0)}"
                    )
                    results = result.get("results", {})
                    for tid, tres in results.items():
                        status_str = tres.get("status", "unknown")
                        err = tres.get("error", "")
                        print(f"    {tid}: {status_str}" + (f" ({err})" if err else ""))

            elif args.list_proposals and isinstance(result, list):
                print("Cross-Pollination Proposals")
                print("=" * 90)
                if not result:
                    print("  No proposals found")
                for prop in result:
                    print(
                        f"  {prop.get('id', '?'):16s}  "
                        f"{prop.get('status', '?'):12s}  "
                        f"cap={prop.get('capability_name', '?')[:25]:25s}  "
                        f"targets={prop.get('target_count', 0)}  "
                        f"by={prop.get('proposed_by', '?')}"
                    )
                    if prop.get("approver"):
                        print(f"    Approved by: {prop['approver']}")

            else:
                print(json.dumps(result, indent=2, default=str))

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
