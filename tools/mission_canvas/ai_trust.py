# CUI // SP-CTI
"""Mission Canvas — AI Trust Mechanisms wrapper.

Wraps tools.security.confabulation_detector and tools.aiml_canvas.governance_assessor
to surface AI trust status for mission models and outputs.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Optional

logger = get_logger("icdev.mission_canvas.ai_trust")


def assess_ai_trust(mission_id: str, model_id: Optional[str] = None) -> dict:
    """Run AI trust assessment: confabulation risk + governance posture.

    Returns a combined trust score and any flagged issues.
    """
    result = {
        "mission_id": mission_id,
        "model_id": model_id,
        "confabulation_risk": None,
        "governance_score": None,
        "trust_score": 0,
        "status": "ok",
    }
    try:
        from tools.security.confabulation_detector import get_summary

        result["confabulation_risk"] = get_summary(project_id=model_id or mission_id)
    except Exception as exc:
        logger.warning("Confabulation detection failed: %s", exc)
        result["confabulation_risk"] = {"error": str(exc)}

    try:
        from tools.aiml_canvas.governance_assessor import run_all

        result["governance_score"] = run_all(
            graph={"project_id": model_id or mission_id},
            design={},
        )
    except Exception as exc:
        logger.warning("Governance assessment failed: %s", exc)
        result["governance_score"] = {"error": str(exc)}

    # Simple composite trust score
    conf = result["confabulation_risk"]
    gov = result["governance_score"]
    if isinstance(conf, dict) and isinstance(gov, dict):
        conf_score = conf.get("score", 50)
        gov_score = gov.get("score", 50)
        result["trust_score"] = round((conf_score + gov_score) / 2, 1)

    return result
