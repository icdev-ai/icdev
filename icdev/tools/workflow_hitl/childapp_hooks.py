# CUI // SP-CTI
"""Child app template inheritance — child app inherits parent canvas's wf_template."""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os

logger = get_logger(__name__)


def get_inherited_template(child_app_id: str, canvas_type: str | None = None) -> dict | None:
    """Return the workflow template for a child app.

    Resolution order:
      1. Child app-specific .env override: WF_TEMPLATE_<CHILD_APP_ID_UPPER>
      2. Canvas-type default from args/workflow_hitl_config.yaml
      3. Global system default
    """
    env_key = f"WF_TEMPLATE_{child_app_id.upper().replace('-', '_')}"
    override_id = os.getenv(env_key)

    from tools.workflow_hitl import template_manager

    if override_id:
        tmpl = template_manager.get(override_id)
        if tmpl:
            return tmpl
        logger.warning("Child app %s env override template %r not found; falling back.", child_app_id, override_id)

    return template_manager.get_default(canvas_type)


def create_instance_for_child_app(
    child_app_id: str,
    task_id: str | None = None,
    project_id: str | None = None,
    canvas_type: str | None = None,
) -> str | None:
    """Create a wf_instance for a child app, inheriting the canvas template."""
    import os
    if os.getenv("ICDEV_HITL_ENABLED", "false").lower() not in ("true", "1"):
        return None

    tmpl = get_inherited_template(child_app_id, canvas_type)
    if not tmpl:
        logger.warning("No template found for child app %s (canvas=%s)", child_app_id, canvas_type)
        return None

    from tools.workflow_hitl.engine import WorkflowEngine
    return WorkflowEngine().create_instance(
        task_id=task_id,
        project_id=project_id,
        canvas_type=canvas_type,
        template_id=tmpl["id"],
    )
