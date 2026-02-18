# CUI // SP-CTI
"""Domain Authority — enforces agent veto rights per topic.

Loads the authority matrix from args/agent_authority.yaml, checks whether
an agent has authority (hard/soft veto) over a given topic, records vetoes
in the agent_vetoes table, and handles veto overrides.

Decision D42: YAML-defined authority matrix, vetoes append-only.
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"
AUTHORITY_CONFIG_PATH = BASE_DIR / "args" / "agent_authority.yaml"

logger = logging.getLogger("icdev.authority")

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None) -> sqlite3.Connection:
    """Open a DB connection with row factory."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Authority Matrix
# ---------------------------------------------------------------------------

def load_authority_matrix(config_path=None) -> dict:
    """Load the domain authority matrix from args/agent_authority.yaml.

    Returns a dict keyed by agent_id with veto_type and topics list.
    Falls back to a minimal default if the file is missing or yaml unavailable.

    Args:
        config_path: Optional path override for the authority config file.

    Returns:
        {"agent-id": {"veto_type": "hard"|"soft", "topics": [...], "description": "..."}}
    """
    path = Path(config_path) if config_path else AUTHORITY_CONFIG_PATH

    if yaml and path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            authority = raw.get("authority", {})
            logger.info("Loaded authority matrix: %d agents from %s",
                        len(authority), path)
            return authority
        except Exception as exc:
            logger.warning("Failed to load authority matrix from %s: %s", path, exc)

    # Fallback defaults
    logger.info("Using default authority matrix (yaml unavailable or file missing)")
    return {
        "security-agent": {
            "veto_type": "hard",
            "topics": [
                "code_generation", "dependency_addition",
                "infrastructure_change", "secret_management",
                "container_configuration",
            ],
            "description": "Security agent can hard-veto security-sensitive changes",
        },
        "compliance-agent": {
            "veto_type": "hard",
            "topics": [
                "artifact_generation", "deployment",
                "classification_marking", "ato_submission",
                "data_handling",
            ],
            "description": "Compliance agent can hard-veto compliance-sensitive changes",
        },
        "architect-agent": {
            "veto_type": "soft",
            "topics": [
                "system_design", "schema_change", "api_design",
                "technology_selection", "architecture_pattern",
            ],
            "description": "Architect agent can soft-veto design decisions",
        },
    }


def check_authority(agent_id: str, topic: str, config_path=None) -> dict:
    """Check if an agent has authority (hard/soft veto) over a topic.

    Args:
        agent_id: The agent to check authority for.
        topic: The domain topic to check against.
        config_path: Optional config path override.

    Returns:
        {"has_authority": bool, "veto_type": "hard"|"soft"|None, "topics": [...]}
    """
    matrix = load_authority_matrix(config_path)
    agent_config = matrix.get(agent_id, {})

    if not agent_config:
        return {"has_authority": False, "veto_type": None, "topics": []}

    topics = agent_config.get("topics", [])
    veto_type = agent_config.get("veto_type", "soft")

    has_authority = topic in topics
    return {
        "has_authority": has_authority,
        "veto_type": veto_type if has_authority else None,
        "topics": topics,
        "description": agent_config.get("description", ""),
    }


def get_required_reviewers(topic: str, config_path=None) -> list:
    """Get list of agents with authority (hard or soft) over a topic.

    Args:
        topic: The domain topic to find reviewers for.
        config_path: Optional config path override.

    Returns:
        List of dicts: [{"agent_id": ..., "veto_type": "hard"|"soft"}]
    """
    matrix = load_authority_matrix(config_path)
    reviewers = []

    for agent_id, config in matrix.items():
        topics = config.get("topics", [])
        if topic in topics:
            reviewers.append({
                "agent_id": agent_id,
                "veto_type": config.get("veto_type", "soft"),
                "description": config.get("description", ""),
            })

    return reviewers


# ---------------------------------------------------------------------------
# Veto Recording (append-only — NIST AU compliance)
# ---------------------------------------------------------------------------

def record_veto(authority_agent_id: str, vetoed_agent_id: str,
                task_id: str, workflow_id: str, project_id: str,
                topic: str, veto_type: str, reason: str,
                evidence: str = None, db_path=None) -> int:
    """Record a veto in the agent_vetoes table. Returns veto ID.

    This is an append-only operation — vetoes are never deleted or modified
    in place. Override status is tracked separately.

    Args:
        authority_agent_id: Agent issuing the veto.
        vetoed_agent_id: Agent whose output was vetoed.
        task_id: Associated task ID (may be None).
        workflow_id: Associated workflow ID (may be None).
        project_id: Project scope.
        topic: Domain topic of the veto.
        veto_type: "hard" or "soft".
        reason: Explanation for the veto.
        evidence: Supporting evidence (optional).
        db_path: Optional database path override.

    Returns:
        The auto-incremented veto ID.
    """
    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO agent_vetoes
               (authority_agent_id, vetoed_agent_id, task_id, workflow_id,
                project_id, topic, veto_type, reason, evidence, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (authority_agent_id, vetoed_agent_id, task_id, workflow_id,
             project_id, topic, veto_type, reason, evidence),
        )
        conn.commit()
        veto_id = cursor.lastrowid
        logger.info("Veto #%d recorded: %s %s-veto on '%s' for agent '%s'",
                     veto_id, authority_agent_id, veto_type, topic, vetoed_agent_id)

        # Audit trail
        try:
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="agent_veto_issued",
                actor=authority_agent_id,
                action=f"{veto_type} veto issued on topic '{topic}': {reason}",
                project_id=project_id,
                details={
                    "veto_id": veto_id,
                    "vetoed_agent_id": vetoed_agent_id,
                    "topic": topic,
                    "veto_type": veto_type,
                },
                classification="CUI",
            )
        except ImportError:
            pass

        return veto_id
    finally:
        conn.close()


def record_override(veto_id: int, overridden_by: str, justification: str,
                    approval_id: str = None, db_path=None) -> bool:
    """Mark a veto as overridden with justification.

    Soft vetoes can be overridden by the orchestrator with justification.
    Hard vetoes require a human approval_id from approval_workflows.

    Args:
        veto_id: ID of the veto to override.
        overridden_by: Who is overriding (agent or human).
        justification: Reason for the override.
        approval_id: Required for hard veto overrides (from approval_workflows).
        db_path: Optional database path override.

    Returns:
        True if successfully overridden, False otherwise.
    """
    conn = _get_db(db_path)
    try:
        # Fetch current veto
        row = conn.execute(
            "SELECT * FROM agent_vetoes WHERE id = ?", (veto_id,)
        ).fetchone()

        if not row:
            logger.error("Veto #%d not found", veto_id)
            return False

        if row["status"] != "active":
            logger.warning("Veto #%d is already %s", veto_id, row["status"])
            return False

        # Hard vetoes require approval_id
        if row["veto_type"] == "hard" and not approval_id:
            logger.error("Hard veto #%d requires approval_id for override", veto_id)
            return False

        conn.execute(
            """UPDATE agent_vetoes
               SET status = 'overridden',
                   overridden_by = ?,
                   override_justification = ?,
                   override_approval_id = ?
               WHERE id = ?""",
            (overridden_by, justification, approval_id, veto_id),
        )
        conn.commit()
        logger.info("Veto #%d overridden by %s: %s", veto_id, overridden_by, justification)

        # Audit trail
        try:
            from tools.audit.audit_logger import log_event
            log_event(
                event_type="agent_veto_overridden",
                actor=overridden_by,
                action=f"Veto #{veto_id} overridden: {justification}",
                project_id=row["project_id"],
                details={
                    "veto_id": veto_id,
                    "original_authority": row["authority_agent_id"],
                    "veto_type": row["veto_type"],
                    "approval_id": approval_id,
                },
                classification="CUI",
            )
        except ImportError:
            pass

        return True
    finally:
        conn.close()


def get_veto_history(project_id: str = None, agent_id: str = None,
                     db_path=None) -> list:
    """Get veto history filtered by project and/or agent.

    Args:
        project_id: Filter by project (optional).
        agent_id: Filter by authority agent (optional).
        db_path: Optional database path override.

    Returns:
        List of veto records as dicts.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM agent_vetoes WHERE 1=1"
        params = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if agent_id:
            query += " AND (authority_agent_id = ? OR vetoed_agent_id = ?)"
            params.extend([agent_id, agent_id])

        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI for domain authority management."""
    parser = argparse.ArgumentParser(
        description="ICDEV Domain Authority — enforce agent veto rights per topic"
    )
    sub = parser.add_subparsers(dest="command", help="Command to execute")

    # Check authority
    p_check = sub.add_parser("check", help="Check if agent has authority over topic")
    p_check.add_argument("--agent-id", required=True, help="Agent ID")
    p_check.add_argument("--topic", required=True, help="Domain topic")

    # List reviewers
    p_reviewers = sub.add_parser("reviewers", help="List agents with authority over a topic")
    p_reviewers.add_argument("--topic", required=True, help="Domain topic")

    # Record veto
    p_veto = sub.add_parser("veto", help="Record a domain veto")
    p_veto.add_argument("--authority", required=True, help="Authority agent ID")
    p_veto.add_argument("--vetoed", required=True, help="Vetoed agent ID")
    p_veto.add_argument("--project-id", required=True, help="Project ID")
    p_veto.add_argument("--topic", required=True, help="Domain topic")
    p_veto.add_argument("--veto-type", required=True, choices=["hard", "soft"])
    p_veto.add_argument("--reason", required=True, help="Veto reason")
    p_veto.add_argument("--evidence", help="Supporting evidence")
    p_veto.add_argument("--task-id", help="Task ID")
    p_veto.add_argument("--workflow-id", help="Workflow ID")

    # Override veto
    p_override = sub.add_parser("override", help="Override a veto")
    p_override.add_argument("--veto-id", required=True, type=int, help="Veto ID")
    p_override.add_argument("--by", required=True, help="Who is overriding")
    p_override.add_argument("--justification", required=True, help="Justification")
    p_override.add_argument("--approval-id", help="Approval workflow ID (required for hard vetoes)")

    # History
    p_history = sub.add_parser("history", help="Get veto history")
    p_history.add_argument("--project-id", help="Filter by project")
    p_history.add_argument("--agent-id", help="Filter by agent")

    # Load matrix
    sub.add_parser("matrix", help="Display the authority matrix")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "check":
        result = check_authority(args.agent_id, args.topic)
        print(json.dumps(result, indent=2))

    elif args.command == "reviewers":
        result = get_required_reviewers(args.topic)
        print(json.dumps(result, indent=2))

    elif args.command == "veto":
        veto_id = record_veto(
            authority_agent_id=args.authority,
            vetoed_agent_id=args.vetoed,
            task_id=args.task_id,
            workflow_id=args.workflow_id,
            project_id=args.project_id,
            topic=args.topic,
            veto_type=args.veto_type,
            reason=args.reason,
            evidence=args.evidence,
        )
        print(json.dumps({"veto_id": veto_id, "status": "active"}, indent=2))

    elif args.command == "override":
        success = record_override(
            veto_id=args.veto_id,
            overridden_by=args.by,
            justification=args.justification,
            approval_id=args.approval_id,
        )
        print(json.dumps({"success": success, "veto_id": args.veto_id}, indent=2))

    elif args.command == "history":
        results = get_veto_history(
            project_id=args.project_id,
            agent_id=args.agent_id,
        )
        print(json.dumps(results, indent=2, default=str))

    elif args.command == "matrix":
        matrix = load_authority_matrix()
        print(json.dumps(matrix, indent=2))


if __name__ == "__main__":
    main()
