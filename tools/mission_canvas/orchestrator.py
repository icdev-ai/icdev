# CUI // SP-CTI
"""Mission Canvas — Autonomous AI Agent Orchestration wrapper.

Wraps tools.agent.team_orchestrator to dispatch agent teams
for mission-specific tasks.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Optional

logger = get_logger("icdev.mission_canvas.orchestrator")


def run_mission_team(
    mission_id: str,
    objective: str,
    agents: Optional[list[str]] = None,
    context: Optional[dict] = None,
) -> dict:
    """Dispatch an agent team for a mission objective.

    Returns a status dict with workflow_id, status, and any immediate results.
    """
    try:
        from tools.agent.team_orchestrator import TeamOrchestrator

        orchestrator = TeamOrchestrator()
        workflow = orchestrator.decompose_task(
            task_description=objective,
            project_id=mission_id,
            agent_config={"agents": agents or [], "context": context or {}},
        )
        executed = orchestrator.execute_workflow(workflow)
        return {
            "mission_id": mission_id,
            "workflow_id": getattr(executed, "workflow_id", str(workflow.workflow_id)),
            "status": executed.status if hasattr(executed, "status") else "running",
            "agents_dispatched": agents or [],
            "detail": executed.__dict__ if hasattr(executed, "__dict__") else executed,
        }
    except Exception as exc:
        logger.warning("Agent orchestration failed for mission %s: %s", mission_id, exc)
        return {
            "mission_id": mission_id,
            "status": "error",
            "error": str(exc),
        }


def get_team_status(workflow_id: str) -> dict:
    """Poll the status of a dispatched agent team."""
    try:
        from tools.agent.team_orchestrator import TeamOrchestrator

        orchestrator = TeamOrchestrator()
        return orchestrator.get_workflow_status(workflow_id)
    except Exception as exc:
        logger.warning("Team status query failed for %s: %s", workflow_id, exc)
        return {"workflow_id": workflow_id, "status": "unknown", "error": str(exc)}
