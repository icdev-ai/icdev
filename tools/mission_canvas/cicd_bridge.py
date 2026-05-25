# CUI // SP-CTI
"""Mission Canvas — DevSecOps CI/CD Integration wrapper.

Wraps tools.pipeline.blueprint and tools.pipeline.deploy_catalog to surface
pipeline health, deployment queue, and CI/CD status.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger


logger = get_logger("icdev.mission_canvas.cicd_bridge")


def get_cicd_status(mission_id: str) -> dict:
    """Return current CI/CD pipeline health and deployment catalog for mission."""
    result = {
        "mission_id": mission_id,
        "pipeline": {},
        "deploy_catalog": {},
        "status": "ok",
    }
    try:
        from tools.pipeline.blueprint import create_pipeline_blueprint

        result["pipeline"] = create_pipeline_blueprint()
    except Exception as exc:
        logger.warning("Pipeline status fetch failed: %s", exc)
        result["pipeline"] = {"error": str(exc)}

    try:
        from tools.pipeline.deploy_catalog import get_deploy_info

        result["deploy_catalog"] = get_deploy_info(node_type=mission_id)
    except Exception as exc:
        logger.warning("Deploy catalog fetch failed: %s", exc)
        result["deploy_catalog"] = {"error": str(exc)}

    return result


def trigger_deployment(mission_id: str, artifact_id: str, env: str = "staging") -> dict:
    """Trigger a deployment via the CI/CD bridge."""
    return {
        "mission_id": mission_id,
        "artifact_id": artifact_id,
        "environment": env,
        "status": "not_implemented",
        "message": "Deployment trigger requires pipeline runner integration (not yet available via wrapper)",
    }
