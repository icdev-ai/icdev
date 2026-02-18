#!/usr/bin/env python3
"""Append-only audit trail writer. Satisfies NIST 800-53 AU controls.
No UPDATE or DELETE operations — all entries are immutable."""

import argparse
import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_EVENT_TYPES = (
    "project_created", "project_updated",
    "code_generated", "code_reviewed", "code_approved", "code_rejected",
    "test_written", "test_executed", "test_passed", "test_failed",
    "security_scan", "vulnerability_found", "vulnerability_resolved",
    "compliance_check", "ssp_generated", "poam_generated", "stig_checked", "sbom_generated",
    "deployment_initiated", "deployment_succeeded", "deployment_failed", "rollback_executed",
    "decision_made", "approval_granted", "approval_denied",
    "agent_task_submitted", "agent_task_completed", "agent_task_failed",
    "self_heal_triggered", "pattern_detected", "knowledge_recorded",
    "config_changed", "secret_rotated",
    # RICOAS events (Phase 20)
    "intake_session_created", "intake_session_resumed", "intake_session_completed",
    "requirement_captured", "requirement_refined", "requirement_approved",
    "gap_detected", "ambiguity_detected",
    "readiness_scored", "decomposition_generated",
    "document_uploaded", "document_extracted",
    "bdd_criteria_generated",
    # Boundary & Supply Chain events
    "boundary_assessed", "boundary_impact_red", "boundary_alternative_generated",
    "ato_system_registered", "isa_created", "isa_expired", "isa_renewed",
    "scrm_assessed", "cve_triaged", "cve_impact_propagated",
    "supply_chain_risk_escalated",
    # Simulation & COA events
    "simulation_created", "simulation_completed", "monte_carlo_completed",
    "coa_generated", "coa_alternative_generated", "coa_compared",
    "coa_selected", "coa_rejected", "coa_presented",
    # Integration events
    "integration_configured", "integration_sync_push", "integration_sync_pull",
    "integration_sync_error", "reqif_exported",
    "approval_submitted", "approval_reviewed", "approval_approved",
    "approval_rejected", "approval_escalated",
    "rtm_generated", "rtm_gap_detected",
    # Observability events (TAC-8 Phase A)
    "hook_event_logged", "agent_execution_started", "agent_execution_completed",
    "agent_execution_failed", "agent_execution_retried",
    # NLQ events (TAC-8 Phase B)
    "nlq_query_executed", "nlq_query_blocked",
    # Worktree & GitLab events (TAC-8 Phase C)
    "worktree_created", "worktree_cleaned",
    "gitlab_task_claimed", "gitlab_task_completed", "gitlab_task_failed",
    # Agent Orchestration events (Opus 4.6 Multi-Agent)
    "bedrock_invoked", "bedrock_fallback", "bedrock_rate_limited",
    "workflow_created", "workflow_completed", "workflow_failed",
    "subtask_dispatched", "subtask_completed", "subtask_failed",
    "agent_health_stale",
    "agent_veto_issued", "agent_veto_overridden",
    "agent_collaboration_started", "agent_collaboration_completed",
    "agent_message_sent", "agent_memory_stored", "agent_memory_recalled",
    "agent_escalation_created",
)


def log_event(
    event_type: str,
    actor: str,
    action: str,
    project_id: str = None,
    details: dict = None,
    affected_files: list = None,
    classification: str = "CUI",
    ip_address: str = None,
    session_id: str = None,
    db_path: Path = None,
) -> int:
    """Write an immutable audit trail entry. Returns the entry ID."""
    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"Invalid event_type '{event_type}'. Valid: {VALID_EVENT_TYPES}")

    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    c.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, affected_files,
            classification, ip_address, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            event_type,
            actor,
            action,
            json.dumps(details) if details else None,
            json.dumps(affected_files) if affected_files else None,
            classification,
            ip_address,
            session_id,
        ),
    )
    conn.commit()
    entry_id = c.lastrowid
    conn.close()
    return entry_id


def main():
    parser = argparse.ArgumentParser(description="Log an audit trail event")
    parser.add_argument("--event", required=True, choices=VALID_EVENT_TYPES, help="Event type")
    parser.add_argument("--actor", required=True, help="Who performed the action")
    parser.add_argument("--action", required=True, help="Human-readable description")
    parser.add_argument("--project", help="Project ID")
    parser.add_argument("--details", help="JSON details string")
    parser.add_argument("--files", help="Comma-separated affected file paths")
    parser.add_argument("--classification", default="CUI", help="Classification marking")
    args = parser.parse_args()

    details = json.loads(args.details) if args.details else None
    affected_files = args.files.split(",") if args.files else None

    entry_id = log_event(
        event_type=args.event,
        actor=args.actor,
        action=args.action,
        project_id=args.project,
        details=details,
        affected_files=affected_files,
        classification=args.classification,
    )
    print(f"Audit entry #{entry_id} logged: [{args.event}] {args.action}")


if __name__ == "__main__":
    main()
