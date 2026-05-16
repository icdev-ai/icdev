# CUI // SP-CTI
"""MCIP DTI Scorer Reflex — computes and persists the Diplomatic Tension Index every 6 hours."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run(ctx: dict, session) -> dict:
    """Compute DTI and persist a snapshot. Called by the Genesis reflex daemon."""
    try:
        from tools.mcip.analytics import compute_dti, record_dti_score
        from tools.mcip.constants import DTI_UPDATE_INTERVAL_HOURS

        result = compute_dti(window_hours=DTI_UPDATE_INTERVAL_HOURS)
        row_id = record_dti_score(result["score"], result["sub_scores"], result["event_count"])
        logger.info(
            "mcip_dti_scorer: DTI=%.2f band=%s events=%d id=%s",
            result["score"], result["band"], result["event_count"], row_id,
        )
        return {
            "status": "ok",
            "score": result["score"],
            "band": result["band"],
            "event_count": result["event_count"],
            "persisted_id": row_id,
        }
    except Exception as exc:
        logger.error("mcip_dti_scorer failed: %s", exc)
        return {"status": "error", "error": str(exc)}
