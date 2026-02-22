#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Learning Collector -- ingests and evaluates learned behaviors reported by child applications.

Children report behaviors via A2A protocol. Parent evaluates each behavior
for universality, safety, and evidence strength before queueing for absorption.

ADR D213: Children report learned behaviors (optimizations, error recovery,
    compliance shortcuts, etc.) to the parent via a structured schema. Parent
    evaluates each behavior using the CapabilityEvaluator's 6-dimension scoring.

ADR D6: All ingested behaviors and evaluation decisions are append-only.

Behavior types:
    optimization         - Performance or resource optimization
    error_recovery       - Automatic error handling/recovery pattern
    compliance_shortcut  - More efficient compliance workflow
    performance_tuning   - Configuration or parameter tuning
    security_pattern     - Security improvement or hardening
    workflow_improvement - Process or workflow enhancement
    configuration        - Configuration discovery
    other                - Uncategorized behavior

Usage:
    python tools/registry/learning_collector.py --ingest \
        --child-id "child-abc123" --behavior-type "optimization" \
        --description "Cached STIG results for 30min" \
        --evidence '{"test_count": 5, "avg_speedup": 2.3}' \
        --confidence 0.85 --json

    python tools/registry/learning_collector.py --evaluate --behavior-id 42 --json

    python tools/registry/learning_collector.py --unevaluated --json
    python tools/registry/learning_collector.py --unevaluated --limit 10 --json

    python tools/registry/learning_collector.py --by-child --child-id "child-abc123" --json

    python tools/registry/learning_collector.py --summary --json
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
    from tools.registry.capability_evaluator import CapabilityEvaluator
    _HAS_EVALUATOR = True
except ImportError:
    _HAS_EVALUATOR = False

try:
    from tools.security.prompt_injection_detector import PromptInjectionDetector
    _pid = PromptInjectionDetector()
except Exception:
    _pid = None


# =========================================================================
# CONSTANTS
# =========================================================================
VALID_BEHAVIOR_TYPES = (
    "optimization",
    "error_recovery",
    "compliance_shortcut",
    "performance_tuning",
    "security_pattern",
    "workflow_improvement",
    "configuration",
    "other",
)

CHILD_LEARNED_BEHAVIORS_DDL = """
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
    classification TEXT DEFAULT 'CUI',
    trust_level TEXT DEFAULT 'child'
        CHECK(trust_level IN ('system', 'user', 'external', 'child')),
    injection_scan_result TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_child_learned_child
    ON child_learned_behaviors(child_id);
CREATE INDEX IF NOT EXISTS idx_child_learned_type
    ON child_learned_behaviors(behavior_type);
CREATE INDEX IF NOT EXISTS idx_child_learned_confidence
    ON child_learned_behaviors(confidence);
CREATE INDEX IF NOT EXISTS idx_child_learned_evaluated
    ON child_learned_behaviors(evaluated);
CREATE INDEX IF NOT EXISTS idx_child_learned_absorbed
    ON child_learned_behaviors(absorbed);
"""


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix="beh"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="learning-collector",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="icdev-genome",
            )
        except Exception:
            pass


# =========================================================================
# LEARNING COLLECTOR
# =========================================================================
class LearningCollector:
    """Ingests and evaluates learned behaviors reported by child applications.

    Children report behaviors (optimizations, error recovery patterns,
    compliance shortcuts, etc.) via A2A protocol. This collector:

        1. Ingests behaviors into child_learned_behaviors (append-only, D6)
        2. Evaluates behaviors using CapabilityEvaluator 6-dimension scoring
        3. Provides queries for unevaluated behaviors, per-child listings,
           and summary statistics
    """

    def __init__(self, db_path=None):
        """Initialize LearningCollector.

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
        """Create child_learned_behaviors table if it does not exist."""
        try:
            conn = self._get_conn()
            conn.executescript(CHILD_LEARNED_BEHAVIORS_DDL)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Table creation failed: {e}", file=sys.stderr)

    def _log_audit_event(self, conn, child_id, event_type, details):
        """Log an audit event for injection detection (append-only, D6)."""
        _audit(
            f"learning.{event_type}",
            f"Child {child_id}: {details}",
            {"child_id": child_id, "event_type": event_type, "details": details},
        )

    def ingest_behavior(
        self,
        child_id: str,
        behavior_type: str,
        description: str,
        evidence: dict = None,
        confidence: float = 0.5,
        trust_level: str = "child",
    ) -> Optional[str]:
        """Record a learned behavior from a child application.

        Inserts a new record into child_learned_behaviors (append-only, D6).
        The behavior starts as unevaluated (evaluated=0) and unabsorbed
        (absorbed=0).

        Args:
            child_id: Child app ID that reported the behavior.
            behavior_type: One of the valid behavior types (see VALID_BEHAVIOR_TYPES).
            description: Human-readable description of the learned behavior.
            evidence: Optional dict with supporting evidence (test results,
                      metrics, observations). Stored as JSON.
            confidence: Child's self-reported confidence score (0.0-1.0).
            trust_level: Trust level of the source ('system', 'user',
                         'external', 'child'). Default 'child'.

        Returns:
            The behavior_id (row ID as string) of the inserted record,
            or None on failure. May return a dict with rejection details
            if prompt injection is detected.
        """
        # Validate behavior_type
        if behavior_type not in VALID_BEHAVIOR_TYPES:
            print(
                f"Warning: Invalid behavior_type '{behavior_type}'. "
                f"Must be one of: {', '.join(VALID_BEHAVIOR_TYPES)}",
                file=sys.stderr,
            )
            return None

        # Clamp confidence to [0.0, 1.0]
        confidence = max(0.0, min(1.0, float(confidence)))

        evidence_json = json.dumps(evidence or {}, default=str)
        now = _now()

        # Phase 37 integration: scan for prompt injection
        injection_scan_result = None
        if _pid is not None:
            scan_target = f"{description} {json.dumps(evidence) if evidence else ''}"
            scan_result = _pid.scan_text(scan_target, source="child_learned_behavior")
            injection_scan_result = json.dumps(scan_result)
            if scan_result.get("detected") and scan_result.get("confidence", 0) >= 0.7:
                # Block high-confidence injection attempts
                self._log_audit_event(
                    None, child_id,
                    "learned_behavior_rejected",
                    f"Prompt injection detected (confidence={scan_result['confidence']:.2f})"
                )
                return {
                    "status": "rejected",
                    "reason": "prompt_injection_detected",
                    "confidence": scan_result["confidence"],
                    "behavior_id": None,
                }
            elif scan_result.get("detected") and scan_result.get("confidence", 0) >= 0.5:
                # Accept with warning, tag as external trust
                trust_level = "external"

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """INSERT INTO child_learned_behaviors
                   (child_id, behavior_type, description, evidence_json,
                    confidence, evaluated, absorbed, discovered_at,
                    classification, trust_level, injection_scan_result)
                   VALUES (?, ?, ?, ?, ?, 0, 0, ?, 'CUI', ?, ?)""",
                (
                    child_id,
                    behavior_type,
                    description,
                    evidence_json,
                    confidence,
                    now,
                    trust_level,
                    injection_scan_result,
                ),
            )
            conn.commit()

            behavior_id = str(cursor.lastrowid)

            _audit(
                "learning.behavior_ingested",
                f"Ingested behavior from child {child_id}: "
                f"{behavior_type} (confidence={confidence:.2f})",
                {
                    "behavior_id": behavior_id,
                    "child_id": child_id,
                    "behavior_type": behavior_type,
                    "confidence": confidence,
                    "description": description[:200],
                },
            )

            return behavior_id

        except Exception as e:
            print(f"Warning: Failed to ingest behavior: {e}", file=sys.stderr)
            return None
        finally:
            conn.close()

    def evaluate_behavior(self, behavior_id: str) -> dict:
        """Score a behavior using CapabilityEvaluator dimensions.

        Fetches the behavior record, builds a capability_data dict suitable
        for the CapabilityEvaluator, runs the evaluation, and marks the
        behavior as evaluated=1.

        Args:
            behavior_id: Row ID in child_learned_behaviors.

        Returns:
            Dict with evaluation result including score, outcome, dimensions,
            and recommendation. Returns error dict on failure.
        """
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM child_learned_behaviors WHERE id = ?",
                (behavior_id,),
            ).fetchone()

            if not row:
                return {"error": f"Behavior {behavior_id} not found"}

            behavior = dict(row)

            # Check if already evaluated
            if behavior.get("evaluated", 0) == 1:
                return {
                    "behavior_id": behavior_id,
                    "already_evaluated": True,
                    "message": "Behavior was already evaluated",
                }

            # Parse evidence
            try:
                evidence = json.loads(behavior.get("evidence_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                evidence = {}

            # Build capability_data for CapabilityEvaluator
            capability_data = {
                "id": f"beh-{behavior_id}",
                "name": f"{behavior.get('behavior_type', 'other')}: "
                        f"{behavior.get('description', '')[:80]}",
                "source": "child_report",
                "compliance_impact": self._infer_compliance_impact(behavior),
                "blast_radius": self._infer_blast_radius(behavior),
                "evidence_count": evidence.get("test_count", 0)
                                  or len(evidence),
                "field_hours": evidence.get("field_hours", 0.0),
                "existing_similar": False,
                "fills_gap": behavior.get("behavior_type") in (
                    "error_recovery", "security_pattern", "compliance_shortcut",
                ),
                "token_cost": evidence.get("token_cost", 0.1),
                "integration_effort": evidence.get("integration_effort", "medium"),
            }

            # Add target_children / total_children if available
            if "target_children" in evidence:
                capability_data["target_children"] = evidence["target_children"]
            if "total_children" in evidence:
                capability_data["total_children"] = evidence["total_children"]

            # Pass trust_level and injection_scan_result for security_assessment dimension
            capability_data["trust_level"] = behavior.get("trust_level", "child")
            capability_data["injection_scan_result"] = behavior.get("injection_scan_result")
            capability_data["description"] = behavior.get("description", "")

            # Run evaluation
            evaluation_result = {}
            if _HAS_EVALUATOR:
                try:
                    evaluator = CapabilityEvaluator(db_path=self.db_path)
                    evaluation_result = evaluator.evaluate(capability_data)
                except Exception as e:
                    evaluation_result = {
                        "error": f"Evaluator failed: {e}",
                        "score": behavior.get("confidence", 0.5),
                        "outcome": "log",
                    }
            else:
                # Fallback: use confidence directly as score
                conf = behavior.get("confidence", 0.5)
                if conf >= 0.85:
                    outcome = "auto_queue"
                elif conf >= 0.65:
                    outcome = "recommend"
                elif conf >= 0.40:
                    outcome = "log"
                else:
                    outcome = "archive"

                evaluation_result = {
                    "score": conf,
                    "outcome": outcome,
                    "rationale": (
                        f"Fallback evaluation using confidence={conf:.2f} "
                        "(CapabilityEvaluator not available)"
                    ),
                    "dimensions": {"confidence_proxy": conf},
                }

            # Mark as evaluated (update flag only)
            now = _now()
            conn.execute(
                """UPDATE child_learned_behaviors
                   SET evaluated = 1, evaluated_at = ?
                   WHERE id = ?""",
                (now, behavior_id),
            )
            conn.commit()

            result = {
                "behavior_id": behavior_id,
                "child_id": behavior.get("child_id", ""),
                "behavior_type": behavior.get("behavior_type", ""),
                "description": behavior.get("description", ""),
                "confidence": behavior.get("confidence", 0.0),
                "evaluation": evaluation_result,
                "evaluated_at": now,
                "recommendation": self._build_recommendation(evaluation_result),
            }

            _audit(
                "learning.behavior_evaluated",
                f"Evaluated behavior {behavior_id}: "
                f"score={evaluation_result.get('score', 0):.4f} "
                f"outcome={evaluation_result.get('outcome', 'unknown')}",
                {
                    "behavior_id": behavior_id,
                    "score": evaluation_result.get("score", 0),
                    "outcome": evaluation_result.get("outcome", "unknown"),
                },
            )

            return result

        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def _infer_compliance_impact(self, behavior: dict) -> str:
        """Infer compliance impact from behavior type.

        Args:
            behavior: Behavior record dict.

        Returns:
            One of 'positive', 'neutral', or 'negative'.
        """
        btype = behavior.get("behavior_type", "other")
        positive_types = ("compliance_shortcut", "security_pattern")
        neutral_types = (
            "optimization", "performance_tuning",
            "configuration", "workflow_improvement",
        )

        if btype in positive_types:
            return "positive"
        elif btype in neutral_types:
            return "neutral"
        else:
            return "neutral"

    def _infer_blast_radius(self, behavior: dict) -> str:
        """Infer blast radius from behavior type.

        Args:
            behavior: Behavior record dict.

        Returns:
            One of 'low', 'medium', 'high'.
        """
        btype = behavior.get("behavior_type", "other")
        low_risk_types = ("configuration", "performance_tuning", "optimization")
        high_risk_types = ("security_pattern",)

        if btype in low_risk_types:
            return "low"
        elif btype in high_risk_types:
            return "medium"
        else:
            return "medium"

    def _build_recommendation(self, evaluation_result: dict) -> str:
        """Build a human-readable recommendation from evaluation result.

        Args:
            evaluation_result: Dict with score and outcome.

        Returns:
            Recommendation string.
        """
        outcome = evaluation_result.get("outcome", "log")
        score = evaluation_result.get("score", 0.0)

        recommendations = {
            "auto_queue": (
                f"Score {score:.4f}: AUTO-QUEUE for absorption. "
                "Capability meets all thresholds. Will proceed through "
                "72-hour stability window."
            ),
            "recommend": (
                f"Score {score:.4f}: RECOMMENDED for absorption. "
                "Capability is promising but requires HITL approval "
                "before proceeding to stability window."
            ),
            "log": (
                f"Score {score:.4f}: LOGGED for future consideration. "
                "Capability does not currently meet absorption thresholds. "
                "May improve with additional evidence."
            ),
            "archive": (
                f"Score {score:.4f}: ARCHIVED. "
                "Capability does not meet minimum thresholds for absorption. "
                "Review evidence quality and universality."
            ),
        }

        return recommendations.get(outcome, f"Score {score:.4f}: Unknown outcome '{outcome}'")

    def get_unevaluated(self, limit: int = 50) -> list:
        """Return behaviors that have not been evaluated yet.

        Args:
            limit: Maximum number of records to return. Default 50.

        Returns:
            List of unevaluated behavior dicts, ordered by confidence
            descending (highest priority first).
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, child_id, behavior_type, description,
                          evidence_json, confidence, discovered_at
                   FROM child_learned_behaviors
                   WHERE evaluated = 0
                   ORDER BY confidence DESC, discovered_at ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()

            results = []
            for row in rows:
                record = dict(row)
                # Parse evidence JSON
                try:
                    record["evidence"] = json.loads(
                        record.pop("evidence_json", "{}")
                    )
                except (json.JSONDecodeError, TypeError):
                    record["evidence"] = {}
                results.append(record)

            return results

        finally:
            conn.close()

    def get_behaviors_by_child(self, child_id: str) -> list:
        """Return all learned behaviors for a specific child.

        Args:
            child_id: Child app ID.

        Returns:
            List of behavior dicts for the given child.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, child_id, behavior_type, description,
                          evidence_json, confidence, evaluated, absorbed,
                          discovered_at, evaluated_at, absorbed_at
                   FROM child_learned_behaviors
                   WHERE child_id = ?
                   ORDER BY discovered_at DESC""",
                (child_id,),
            ).fetchall()

            results = []
            for row in rows:
                record = dict(row)
                try:
                    record["evidence"] = json.loads(
                        record.pop("evidence_json", "{}")
                    )
                except (json.JSONDecodeError, TypeError):
                    record["evidence"] = {}
                results.append(record)

            return results

        finally:
            conn.close()

    def get_behavior_summary(self) -> dict:
        """Generate summary statistics for all learned behaviors.

        Returns:
            Dict with:
                total (int): Total number of behaviors
                evaluated (int): Number of evaluated behaviors
                absorbed (int): Number of absorbed behaviors
                unevaluated (int): Number of pending evaluation
                by_type (dict): Count per behavior_type
                by_child (dict): Count per child_id
                avg_confidence (float): Average confidence across all behaviors
        """
        conn = self._get_conn()
        try:
            # Total counts
            totals = conn.execute(
                """SELECT
                       COUNT(*) as total,
                       SUM(CASE WHEN evaluated = 1 THEN 1 ELSE 0 END) as evaluated,
                       SUM(CASE WHEN absorbed = 1 THEN 1 ELSE 0 END) as absorbed,
                       SUM(CASE WHEN evaluated = 0 THEN 1 ELSE 0 END) as unevaluated,
                       AVG(confidence) as avg_confidence
                   FROM child_learned_behaviors"""
            ).fetchone()

            total = totals["total"] if totals else 0
            evaluated = totals["evaluated"] if totals else 0
            absorbed = totals["absorbed"] if totals else 0
            unevaluated = totals["unevaluated"] if totals else 0
            avg_confidence = round(totals["avg_confidence"] or 0.0, 4) if totals else 0.0

            # By type
            type_rows = conn.execute(
                """SELECT behavior_type, COUNT(*) as cnt
                   FROM child_learned_behaviors
                   GROUP BY behavior_type
                   ORDER BY cnt DESC"""
            ).fetchall()
            by_type = {row["behavior_type"]: row["cnt"] for row in type_rows}

            # By child
            child_rows = conn.execute(
                """SELECT child_id, COUNT(*) as cnt
                   FROM child_learned_behaviors
                   GROUP BY child_id
                   ORDER BY cnt DESC"""
            ).fetchall()
            by_child = {row["child_id"]: row["cnt"] for row in child_rows}

            return {
                "total": total,
                "evaluated": evaluated,
                "absorbed": absorbed,
                "unevaluated": unevaluated,
                "avg_confidence": avg_confidence,
                "by_type": by_type,
                "by_child": by_child,
                "generated_at": _now(),
            }

        finally:
            conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "ICDEV Learning Collector -- ingest and evaluate "
            "learned behaviors from child applications (D213)"
        )
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--ingest", action="store_true",
        help="Ingest a learned behavior from a child",
    )
    group.add_argument(
        "--evaluate", action="store_true",
        help="Evaluate a behavior using 6-dimension scoring",
    )
    group.add_argument(
        "--unevaluated", action="store_true",
        help="List unevaluated behaviors",
    )
    group.add_argument(
        "--by-child", action="store_true",
        help="List behaviors for a specific child",
    )
    group.add_argument(
        "--summary", action="store_true",
        help="Show behavior summary statistics",
    )

    # Ingest args
    parser.add_argument("--child-id", help="Child app ID")
    parser.add_argument(
        "--behavior-type",
        choices=VALID_BEHAVIOR_TYPES,
        help="Type of learned behavior",
    )
    parser.add_argument("--description", help="Human-readable behavior description")
    parser.add_argument(
        "--evidence", help="JSON string with supporting evidence"
    )
    parser.add_argument(
        "--confidence", type=float, default=0.5,
        help="Confidence score (0.0-1.0, default: 0.5)",
    )

    # Evaluate args
    parser.add_argument("--behavior-id", help="Behavior row ID (for --evaluate)")

    # List args
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Result limit (default: 50)",
    )

    args = parser.parse_args()

    try:
        collector = LearningCollector(db_path=args.db_path)

        if args.ingest:
            if not args.child_id:
                parser.error("--ingest requires --child-id")
            if not args.behavior_type:
                parser.error("--ingest requires --behavior-type")
            if not args.description:
                parser.error("--ingest requires --description")

            evidence = {}
            if args.evidence:
                try:
                    evidence = json.loads(args.evidence)
                except json.JSONDecodeError as e:
                    result = {"error": f"Invalid JSON in --evidence: {e}"}
                    if args.json:
                        print(json.dumps(result, indent=2))
                    else:
                        print(f"ERROR: {result['error']}", file=sys.stderr)
                    sys.exit(1)

            behavior_id = collector.ingest_behavior(
                child_id=args.child_id,
                behavior_type=args.behavior_type,
                description=args.description,
                evidence=evidence,
                confidence=args.confidence,
            )

            if isinstance(behavior_id, dict):
                # Rejection result from injection scanning
                result = behavior_id
            elif behavior_id:
                result = {
                    "behavior_id": behavior_id,
                    "child_id": args.child_id,
                    "behavior_type": args.behavior_type,
                    "confidence": args.confidence,
                    "status": "ingested",
                    "ingested_at": _now(),
                }
            else:
                result = {"error": "Failed to ingest behavior"}

        elif args.evaluate:
            if not args.behavior_id:
                parser.error("--evaluate requires --behavior-id")
            result = collector.evaluate_behavior(args.behavior_id)

        elif args.unevaluated:
            result = collector.get_unevaluated(limit=args.limit)

        elif args.by_child:
            if not args.child_id:
                parser.error("--by-child requires --child-id")
            result = collector.get_behaviors_by_child(args.child_id)

        elif args.summary:
            result = collector.get_behavior_summary()

        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if args.ingest and isinstance(result, dict):
                if "error" in result:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                elif result.get("status") == "rejected":
                    print(f"REJECTED: {result.get('reason', 'unknown')}")
                    print(f"  Confidence: {result.get('confidence', 0):.2f}")
                else:
                    print(f"Ingested behavior ID: {result.get('behavior_id')}")
                    print(f"  Child:      {result.get('child_id')}")
                    print(f"  Type:       {result.get('behavior_type')}")
                    print(f"  Confidence: {result.get('confidence', 0):.2f}")

            elif args.evaluate and isinstance(result, dict):
                if "error" in result:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                elif result.get("already_evaluated"):
                    print(f"Behavior {result.get('behavior_id')} was already evaluated")
                else:
                    eval_data = result.get("evaluation", {})
                    print("Behavior Evaluation")
                    print("=" * 60)
                    print(f"  Behavior ID:   {result.get('behavior_id')}")
                    print(f"  Child:         {result.get('child_id')}")
                    print(f"  Type:          {result.get('behavior_type')}")
                    print(f"  Score:         {eval_data.get('score', 0):.4f}")
                    print(f"  Outcome:       {eval_data.get('outcome', 'N/A')}")
                    print(f"  Evaluated At:  {result.get('evaluated_at')}")
                    print()
                    print(f"  Recommendation: {result.get('recommendation', '')}")

            elif (args.unevaluated or args.by_child) and isinstance(result, list):
                title = "Unevaluated Behaviors" if args.unevaluated else f"Behaviors for {args.child_id}"
                print(title)
                print("=" * 80)
                if not result:
                    print("  No behaviors found")
                for beh in result:
                    eval_flag = ""
                    if "evaluated" in beh:
                        eval_flag = " [evaluated]" if beh["evaluated"] else ""
                    if "absorbed" in beh and beh.get("absorbed"):
                        eval_flag += " [absorbed]"
                    print(
                        f"  ID={beh.get('id', '?'):6s}  "
                        f"child={beh.get('child_id', '?'):16s}  "
                        f"type={beh.get('behavior_type', '?'):20s}  "
                        f"conf={beh.get('confidence', 0):.2f}"
                        f"{eval_flag}"
                    )

            elif args.summary and isinstance(result, dict):
                print("Learning Collector Summary")
                print("=" * 60)
                print(f"  Total Behaviors:    {result.get('total', 0)}")
                print(f"  Evaluated:          {result.get('evaluated', 0)}")
                print(f"  Absorbed:           {result.get('absorbed', 0)}")
                print(f"  Unevaluated:        {result.get('unevaluated', 0)}")
                print(f"  Avg Confidence:     {result.get('avg_confidence', 0):.4f}")
                print()
                by_type = result.get("by_type", {})
                if by_type:
                    print("  By Type:")
                    for btype, cnt in by_type.items():
                        print(f"    {btype:24s}  {cnt}")
                by_child = result.get("by_child", {})
                if by_child:
                    print("  By Child:")
                    for cid, cnt in by_child.items():
                        print(f"    {cid:24s}  {cnt}")

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
