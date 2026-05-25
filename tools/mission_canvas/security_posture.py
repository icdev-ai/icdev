# CUI // SP-CTI
"""Mission Canvas — FedRAMP Security & Zero Trust wrapper.

Wraps tools.devsecops.zta_maturity_scorer to surface
security posture for a mission environment.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Optional

logger = get_logger("icdev.mission_canvas.security_posture")


def get_security_posture(mission_id: str, scope: Optional[str] = None) -> dict:
    """Return ZTA maturity / FedRAMP posture for the mission.

    Aggregates scores across identity, device, network, application,
    data, and visibility pillars.
    """
    try:
        from tools.devsecops.zta_maturity_scorer import score_all_pillars

        result = score_all_pillars(project_id=mission_id)
        return {
            "mission_id": mission_id,
            "overall_score": result.get("overall_score", 0),
            "pillar_scores": result.get("pillar_scores", {}),
            "gaps": result.get("gaps", []),
            "status": "ok",
        }
    except Exception as exc:
        logger.warning("Security posture scoring failed: %s", exc)
        return {
            "mission_id": mission_id,
            "overall_score": 0,
            "pillar_scores": {},
            "gaps": ["Unable to retrieve posture data"],
            "status": "error",
            "error": str(exc),
        }
