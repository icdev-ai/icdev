#!/usr/bin/env python3
"""Record decisions with full rationale for audit trail.
Every ICDEV decision (why X over Y) is captured for traceability."""

import argparse
import json
from pathlib import Path
from tools.audit.audit_logger import log_event

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def record_decision(
    project_id: str,
    decision: str,
    rationale: str,
    alternatives: list = None,
    actor: str = "icdev-system",
    context: dict = None,
) -> int:
    """Record a decision with full context.

    Args:
        project_id: Which project this decision affects
        decision: What was decided
        rationale: Why this choice was made
        alternatives: What other options were considered
        actor: Who/what made the decision
        context: Additional context (requirements, constraints)

    Returns:
        Audit trail entry ID
    """
    details = {
        "decision": decision,
        "rationale": rationale,
        "alternatives": alternatives or [],
        "context": context or {},
    }

    return log_event(
        event_type="decision_made",
        actor=actor,
        action=f"Decision: {decision}",
        project_id=project_id,
        details=details,
    )


def main():
    parser = argparse.ArgumentParser(description="Record a decision")
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument("--decision", required=True, help="What was decided")
    parser.add_argument("--rationale", required=True, help="Why this choice")
    parser.add_argument("--alternatives", help="Comma-separated alternatives considered")
    parser.add_argument("--actor", default="icdev-system", help="Who made the decision")
    args = parser.parse_args()

    alternatives = args.alternatives.split(",") if args.alternatives else []

    entry_id = record_decision(
        project_id=args.project,
        decision=args.decision,
        rationale=args.rationale,
        alternatives=alternatives,
        actor=args.actor,
    )
    print(f"Decision recorded (audit #{entry_id}): {args.decision}")


if __name__ == "__main__":
    main()
