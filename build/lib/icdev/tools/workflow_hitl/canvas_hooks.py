# CUI // SP-CTI
"""Per-canvas default workflow template mapping and auto-trigger hooks."""
from __future__ import annotations

from tools.workflow_hitl.constants import CANVAS_ROLE_DEFAULTS

# Maps canvas_type → system template ID (seeded in migration 079)
CANVAS_DEFAULT_TEMPLATES: dict[str, str] = {
    ct: f"sys-wft-{ct.lower()}" for ct in CANVAS_ROLE_DEFAULTS
}
CANVAS_DEFAULT_TEMPLATES[None] = "sys-wft-global"  # type: ignore[index]


def get_default_for_canvas(canvas_type: str | None) -> dict | None:
    """Return the default workflow template for a canvas type."""
    from tools.workflow_hitl import template_manager
    return template_manager.get_default(canvas_type)


def auto_create_instance_if_assigned(
    task_id: str,
    project_id: str | None,
    canvas_type: str | None,
) -> str | None:
    """If a team is assigned to this task/project, auto-create a wf_instance.

    Called by canvas blueprints when a design artifact is finalized.
    Returns the instance_id if created, None otherwise.
    """
    import os
    if os.getenv("ICDEV_HITL_ENABLED", "false").lower() not in ("true", "1"):
        return None

    from tools.workflow_hitl import team_manager
    resolved = team_manager.resolve_team(task_id)
    if not resolved:
        return None

    # Check if an instance already exists
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM wf_instances WHERE task_id=%s AND status IN ('active','waiting_external') LIMIT 1",
            (task_id,),
        ).fetchone()
        if existing:
            return existing[0]
    finally:
        conn.close()

    from tools.workflow_hitl.engine import WorkflowEngine
    return WorkflowEngine().create_instance(
        task_id=task_id,
        project_id=project_id,
        canvas_type=canvas_type,
    )
