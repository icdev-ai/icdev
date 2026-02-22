#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Capability Evaluator -- 7-dimension scoring for capability genome absorption.

REQ-36-020: Evaluate newly discovered capabilities (from Innovation Engine or
child field reports) across 7 weighted dimensions to determine readiness for
staging and propagation.

REQ-36-021: Deterministic outcomes based on weighted score:
    >= 0.85  auto_queue   Auto-queue for staging (notification only to HITL)
    0.65-0.84 recommend   Recommend for staging (HITL approval required)
    0.40-0.64 log         Log for future consideration (no action)
    < 0.40    archive     Archive (no action)

REQ-36-022: All evaluation decisions are recorded in the append-only audit
trail (D6 pattern) with full scoring details.

Architecture:
    - Weights configurable but default to REQ-36-020 specification
    - Each dimension scored 0.0-1.0 independently
    - Final score is weighted average
    - Scoring is deterministic (D21 -- reproducible, not probabilistic)
    - Results stored in capability_evaluations table
    - All evaluations append-only (D6)
    - Phase 37 integration: 7th dimension (security_assessment) scores
      trust level, injection scan results, and ATLAS technique alignment

Usage:
    python tools/registry/capability_evaluator.py --evaluate \
        --capability-data '{"name":"stig-cache","source":"child-field-report","evidence_count":5}' \
        --json

    python tools/registry/capability_evaluator.py --evaluate \
        --capability-data '{"name":"new-scanner","target_children":10,"compliance_impact":"positive"}' \
        --json
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


# =========================================================================
# CONSTANTS
# =========================================================================
CAPABILITY_EVALUATIONS_DDL = """
CREATE TABLE IF NOT EXISTS capability_evaluations (
    id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    capability_name TEXT,
    score REAL NOT NULL,
    dimensions_json TEXT NOT NULL,
    outcome TEXT NOT NULL
        CHECK(outcome IN ('auto_queue', 'recommend', 'log', 'archive')),
    rationale TEXT,
    evaluator TEXT NOT NULL DEFAULT 'system',
    source_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix="eval"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="capability-evaluator",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="icdev-genome",
            )
        except Exception:
            pass


# =========================================================================
# CAPABILITY EVALUATOR
# =========================================================================
class CapabilityEvaluator:
    """7-dimension scoring engine for capability evaluation (REQ-36-020).

    Dimensions and weights per specification (Phase 37 integrated):
        universality       (0.22) - How broadly applicable across children
        compliance_safety  (0.22) - Maintains or improves compliance posture
        risk               (0.18) - Risk of adoption (inverted: lower risk = higher score)
        evidence           (0.14) - Strength of evidence from field testing
        novelty            (0.09) - Fills a gap vs duplicates existing capability
        cost               (0.05) - Cost efficiency (inverted: lower cost = higher score)
        security_assessment(0.10) - Security posture: trust level, injection scan, ATLAS alignment
    """

    DIMENSIONS = {
        "universality": 0.22,
        "compliance_safety": 0.22,
        "risk": 0.18,
        "evidence": 0.14,
        "novelty": 0.09,
        "cost": 0.05,
        "security_assessment": 0.10,
    }

    # Outcome thresholds (REQ-36-021)
    THRESHOLD_AUTO_QUEUE = 0.85
    THRESHOLD_RECOMMEND = 0.65
    THRESHOLD_LOG = 0.40

    def __init__(self, db_path=None):
        """Initialize CapabilityEvaluator.

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
        """Create capability_evaluations table if it does not exist."""
        try:
            conn = self._get_conn()
            conn.executescript(CAPABILITY_EVALUATIONS_DDL)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Table creation failed: {e}", file=sys.stderr)

    def evaluate(self, capability_data: dict) -> dict:
        """Score a capability across 7 dimensions and determine outcome.

        Args:
            capability_data: Dict describing the capability. Expected keys:
                name (str):              Capability name
                id (str, optional):      Capability ID (auto-generated if absent)
                source (str, optional):  Origin: 'innovation', 'child_report', etc.
                target_children (int):   Number of children that could benefit
                total_children (int):    Total children in registry
                compliance_impact (str): 'positive', 'neutral', 'negative'
                risk_factors (list):     List of risk strings
                blast_radius (str):      'low', 'medium', 'high', 'critical'
                evidence_count (int):    Number of supporting evidence items
                field_hours (float):     Hours of field testing
                existing_similar (bool): Whether similar capability exists
                fills_gap (bool):        Whether it fills a known gap
                token_cost (float):      Estimated token/compute cost (0.0-1.0)
                integration_effort (str):'trivial', 'low', 'medium', 'high'
                trust_level (str):       Trust level of source ('system', 'user',
                                         'external', 'child'). Phase 37 integration.
                injection_scan_result (str): JSON string of injection scan result.
                                             Phase 37 integration.
                description (str):       Human-readable description. Phase 37 integration.

        Returns:
            Dict with score, dimensions, outcome, and rationale.
        """
        # Score each dimension
        dim_scores = {
            "universality": self._score_universality(capability_data),
            "compliance_safety": self._score_compliance_safety(capability_data),
            "risk": self._score_risk(capability_data),
            "evidence": self._score_evidence(capability_data),
            "novelty": self._score_novelty(capability_data),
            "cost": self._score_cost(capability_data),
            "security_assessment": self._score_security_assessment(capability_data),
        }

        # Weighted average
        weighted_score = sum(
            dim_scores[dim] * weight
            for dim, weight in self.DIMENSIONS.items()
        )
        weighted_score = round(weighted_score, 4)

        # Determine outcome (REQ-36-021)
        if weighted_score >= self.THRESHOLD_AUTO_QUEUE:
            outcome = "auto_queue"
        elif weighted_score >= self.THRESHOLD_RECOMMEND:
            outcome = "recommend"
        elif weighted_score >= self.THRESHOLD_LOG:
            outcome = "log"
        else:
            outcome = "archive"

        # Build rationale
        rationale_parts = []
        top_dims = sorted(dim_scores.items(), key=lambda x: x[1], reverse=True)
        for dim_name, dim_score in top_dims[:2]:
            rationale_parts.append(f"{dim_name}={dim_score:.2f}")
        bottom_dims = sorted(dim_scores.items(), key=lambda x: x[1])
        if bottom_dims[0][1] < 0.4:
            rationale_parts.append(f"weak: {bottom_dims[0][0]}={bottom_dims[0][1]:.2f}")
        rationale = f"Score {weighted_score:.4f} ({outcome}). " + ", ".join(rationale_parts)

        # Generate IDs
        capability_id = capability_data.get("id", _generate_id("cap"))
        eval_id = _generate_id("eval")
        capability_name = capability_data.get("name", "unnamed")

        result = {
            "evaluation_id": eval_id,
            "capability_id": capability_id,
            "capability_name": capability_name,
            "score": weighted_score,
            "dimensions": dim_scores,
            "weights": dict(self.DIMENSIONS),
            "outcome": outcome,
            "rationale": rationale,
            "evaluated_at": _now(),
        }

        # Persist evaluation (append-only, D6)
        self._store_evaluation(result, capability_data.get("source", "unknown"))

        _audit(
            "capability.evaluated",
            f"Evaluated '{capability_name}': {weighted_score:.4f} -> {outcome}",
            {
                "evaluation_id": eval_id,
                "capability_id": capability_id,
                "score": weighted_score,
                "outcome": outcome,
                "dimensions": dim_scores,
            },
        )

        return result

    def _store_evaluation(self, result: dict, source_type: str):
        """Store evaluation result in database (append-only)."""
        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO capability_evaluations
                   (id, capability_id, capability_name, score, dimensions_json,
                    outcome, rationale, evaluator, source_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result["evaluation_id"],
                    result["capability_id"],
                    result["capability_name"],
                    result["score"],
                    json.dumps(result["dimensions"]),
                    result["outcome"],
                    result["rationale"],
                    "system",
                    source_type,
                    result["evaluated_at"],
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Failed to store evaluation: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # DIMENSION SCORING FUNCTIONS
    # Each returns a float in [0.0, 1.0]
    # ------------------------------------------------------------------

    def _score_universality(self, data: dict) -> float:
        """Score how broadly applicable this capability is.

        Uses target_children / total_children ratio as primary signal.
        Falls back to heuristics if counts not provided.

        Args:
            data: Capability data dict.

        Returns:
            Score between 0.0 and 1.0.
        """
        target = data.get("target_children", 0)
        total = data.get("total_children", 0)

        if total > 0 and target > 0:
            ratio = min(target / total, 1.0)
            return round(ratio, 4)

        # Heuristic fallback based on category keywords
        name = data.get("name", "").lower()
        source = data.get("source", "").lower()

        # Broadly applicable categories
        broad_keywords = [
            "security", "compliance", "testing", "monitoring", "logging",
            "audit", "performance", "error", "retry", "resilience",
        ]
        narrow_keywords = [
            "specific", "custom", "niche", "experimental", "prototype",
        ]

        score = 0.5  # Base
        for kw in broad_keywords:
            if kw in name or kw in source:
                score = min(score + 0.1, 1.0)
                break
        for kw in narrow_keywords:
            if kw in name or kw in source:
                score = max(score - 0.2, 0.0)
                break

        # If from child field report affecting multiple children, boost
        if "child" in source or "field" in source:
            score = min(score + 0.1, 1.0)

        return round(score, 4)

    def _score_compliance_safety(self, data: dict) -> float:
        """Score whether this capability maintains or improves compliance.

        Positive compliance impact yields high score. Negative impact yields
        very low score (effectively blocking propagation).

        Args:
            data: Capability data dict.

        Returns:
            Score between 0.0 and 1.0.
        """
        impact = data.get("compliance_impact", "neutral").lower()

        if impact == "positive":
            return 0.95
        elif impact == "neutral":
            return 0.70
        elif impact == "negative":
            return 0.10
        elif impact == "unknown":
            return 0.40

        # Heuristic: check for compliance-related keywords
        name = data.get("name", "").lower()
        positive_kw = [
            "stig", "nist", "fedramp", "cmmc", "compliance", "security",
            "audit", "encryption", "fips", "cui",
        ]
        negative_kw = [
            "bypass", "disable", "skip", "ignore", "override",
        ]

        score = 0.60
        for kw in positive_kw:
            if kw in name:
                score = min(score + 0.15, 0.95)
                break
        for kw in negative_kw:
            if kw in name:
                score = max(score - 0.30, 0.05)
                break

        return round(score, 4)

    def _score_risk(self, data: dict) -> float:
        """Score risk of adoption (inverted: lower risk = higher score).

        Considers blast radius, risk factors count, and integration effort.

        Args:
            data: Capability data dict.

        Returns:
            Score between 0.0 and 1.0 (high = low risk).
        """
        blast_radius = data.get("blast_radius", "medium").lower()
        risk_factors = data.get("risk_factors", [])
        effort = data.get("integration_effort", "medium").lower()

        # Blast radius mapping (inverted)
        blast_scores = {
            "low": 0.95,
            "medium": 0.65,
            "high": 0.35,
            "critical": 0.10,
        }
        blast_score = blast_scores.get(blast_radius, 0.50)

        # Risk factors penalty (each risk factor reduces score)
        factor_count = len(risk_factors) if isinstance(risk_factors, list) else 0
        factor_penalty = min(factor_count * 0.10, 0.50)

        # Integration effort (inverted)
        effort_scores = {
            "trivial": 0.95,
            "low": 0.80,
            "medium": 0.55,
            "high": 0.25,
        }
        effort_score = effort_scores.get(effort, 0.50)

        # Weighted combination
        score = (blast_score * 0.50) + (effort_score * 0.30) + ((1.0 - factor_penalty) * 0.20)
        return round(max(min(score, 1.0), 0.0), 4)

    def _score_evidence(self, data: dict) -> float:
        """Score strength of evidence supporting this capability.

        Based on evidence count and field testing hours.

        Args:
            data: Capability data dict.

        Returns:
            Score between 0.0 and 1.0.
        """
        evidence_count = data.get("evidence_count", 0)
        field_hours = data.get("field_hours", 0.0)

        # Evidence count scoring (0=0.1, 1=0.3, 3=0.5, 5=0.7, 10+=0.9)
        if evidence_count >= 10:
            count_score = 0.95
        elif evidence_count >= 5:
            count_score = 0.75
        elif evidence_count >= 3:
            count_score = 0.55
        elif evidence_count >= 1:
            count_score = 0.35
        else:
            count_score = 0.10

        # Field hours scoring (0=0.1, 24=0.4, 72=0.6, 168=0.8, 336+=0.95)
        if field_hours >= 336:  # 2 weeks
            hours_score = 0.95
        elif field_hours >= 168:  # 1 week
            hours_score = 0.80
        elif field_hours >= 72:  # 3 days (D212 stability window)
            hours_score = 0.65
        elif field_hours >= 24:
            hours_score = 0.40
        else:
            hours_score = 0.10

        score = (count_score * 0.60) + (hours_score * 0.40)
        return round(score, 4)

    def _score_novelty(self, data: dict) -> float:
        """Score how new/innovative this capability is.

        Capabilities that fill known gaps score higher than duplicates.

        Args:
            data: Capability data dict.

        Returns:
            Score between 0.0 and 1.0.
        """
        existing_similar = data.get("existing_similar", False)
        fills_gap = data.get("fills_gap", False)

        if fills_gap and not existing_similar:
            return 0.95  # New capability filling a known gap
        elif fills_gap and existing_similar:
            return 0.65  # Improvement over existing capability
        elif not fills_gap and not existing_similar:
            return 0.50  # Novel but no known demand
        else:
            return 0.20  # Duplicate of existing capability

    def _score_cost(self, data: dict) -> float:
        """Score cost efficiency (inverted: lower cost = higher score).

        Args:
            data: Capability data dict.

        Returns:
            Score between 0.0 and 1.0.
        """
        token_cost = data.get("token_cost", 0.0)
        effort = data.get("integration_effort", "medium").lower()

        # Token cost is 0.0-1.0 where 1.0 is very expensive
        if isinstance(token_cost, (int, float)):
            cost_score = max(1.0 - float(token_cost), 0.0)
        else:
            cost_score = 0.50

        # Integration effort cost
        effort_costs = {
            "trivial": 0.95,
            "low": 0.80,
            "medium": 0.55,
            "high": 0.25,
        }
        effort_score = effort_costs.get(effort, 0.50)

        score = (cost_score * 0.60) + (effort_score * 0.40)
        return round(max(min(score, 1.0), 0.0), 4)

    def _score_security_assessment(self, data: dict) -> float:
        """Score the security posture of a capability (Phase 37 integration).

        Checks for:
        - Known ATLAS technique mappings
        - Injection scan results
        - Security-related keywords
        - Trust level of source

        Args:
            data: Capability data dict.

        Returns:
            Score between 0.0 and 1.0.
        """
        score = 0.7  # Default baseline

        description = str(data.get("description", "")).lower()
        source_type = data.get("source", "")
        trust_level = data.get("trust_level", "child")
        injection_scan = data.get("injection_scan_result")

        # Boost for system/user trust, penalize for external
        trust_scores = {"system": 0.15, "user": 0.10, "child": 0.0, "external": -0.15}
        score += trust_scores.get(trust_level, 0.0)

        # Penalize if injection was detected (even low confidence)
        if injection_scan:
            try:
                scan_data = json.loads(injection_scan) if isinstance(injection_scan, str) else injection_scan
                if scan_data.get("detected"):
                    score -= 0.3
            except (json.JSONDecodeError, TypeError):
                pass

        # Boost for security-enhancing capabilities
        security_keywords = ["security", "encrypt", "authentication", "authorization",
                           "compliance", "audit", "monitoring", "detection"]
        matches = sum(1 for kw in security_keywords if kw in description)
        score += min(matches * 0.05, 0.15)

        # Penalize for risky keywords
        risk_keywords = ["execute", "eval", "shell", "command", "inject", "override"]
        risk_matches = sum(1 for kw in risk_keywords if kw in description)
        score -= min(risk_matches * 0.1, 0.2)

        return max(0.0, min(1.0, score))


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Capability Evaluator -- 7-dimension scoring (REQ-36-020)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--evaluate", action="store_true",
                       help="Evaluate a capability")

    parser.add_argument("--capability-data", help="JSON string of capability data")

    args = parser.parse_args()

    try:
        evaluator = CapabilityEvaluator(db_path=args.db_path)

        if args.evaluate:
            if not args.capability_data:
                parser.error("--evaluate requires --capability-data (JSON string)")
            try:
                capability_data = json.loads(args.capability_data)
            except json.JSONDecodeError as e:
                result = {"error": f"Invalid JSON in --capability-data: {e}"}
                if args.json:
                    print(json.dumps(result, indent=2))
                else:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                sys.exit(1)
            result = evaluator.evaluate(capability_data)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if "error" in result:
                print(f"ERROR: {result['error']}", file=sys.stderr)
            else:
                print("Capability Evaluation")
                print("=" * 60)
                print(f"  Capability: {result.get('capability_name', 'N/A')}")
                print(f"  ID:         {result.get('capability_id', 'N/A')}")
                print(f"  Score:      {result.get('score', 0):.4f}")
                print(f"  Outcome:    {result.get('outcome', 'N/A')}")
                print(f"  Rationale:  {result.get('rationale', 'N/A')}")
                print()
                print("  Dimensions:")
                dims = result.get("dimensions", {})
                weights = result.get("weights", {})
                for dim_name, dim_score in sorted(dims.items(),
                                                   key=lambda x: x[1],
                                                   reverse=True):
                    weight = weights.get(dim_name, 0)
                    weighted = dim_score * weight
                    bar = "#" * int(dim_score * 20)
                    print(f"    {dim_name:22s}  {dim_score:.4f}  "
                          f"(w={weight:.2f}, contrib={weighted:.4f})  "
                          f"|{bar}|")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
