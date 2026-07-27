# CUI // SP-CTI
"""ZTA Continuous Monitoring Reflex (G-18, NIST CA-7/SI-4).

Triggered by the genesis reflex scheduler every 30 days.
Runs a ZTA maturity assessment and emits a drift alert if score drops ≥ threshold.
"""
from __future__ import annotations

import os
from tools.logging.icdev_logger import get_logger

logger = get_logger("genesis.reflexes.zta_monitor")

_DRIFT_THRESHOLD = float(os.environ.get("ICDEV_ZTA_DRIFT_THRESHOLD", "0.1"))
_PROJECT_ID = os.environ.get("ICDEV_ZTA_PROJECT_ID", "icdev-platform")


def run(context: dict, session) -> dict:
    """Run a scheduled ZTA maturity assessment with drift detection.

    Args:
        context: Genesis reflex context dict (may contain project_id, drift_threshold).
        session: Not used; kept for reflex scheduler compatibility.

    Returns:
        Assessment result dict with drift_alert flag.
    """
    try:
        from tools.devsecops.zta_maturity_scorer import run_scheduled_assessment

        project_id = context.get("project_id", _PROJECT_ID)
        drift_threshold = float(context.get("drift_threshold", _DRIFT_THRESHOLD))

        result = run_scheduled_assessment(project_id=project_id, drift_threshold=drift_threshold)
        current_score = result.get("current_score", 0.0)
        drift_alert = result.get("drift_alert", False)

        if drift_alert:
            drift = result.get("drift", 0.0)
            prev = result.get("previous_score", 0.0)
            logger.warning(
                "ZTA drift alert: project=%s score=%.3f prev=%.3f drift=%.3f",
                project_id, current_score, prev, drift,
            )
            _emit_kanban_alert(project_id, current_score, prev, drift)
        else:
            logger.info(
                "ZTA assessment OK: project=%s score=%.3f no_drift",
                project_id, current_score,
            )

        return result

    except Exception as exc:
        logger.error("ZTA monitor reflex error: %s", exc)
        return {"error": str(exc), "drift_alert": False}


def _emit_kanban_alert(project_id: str, current_score: float, prev_score: float, drift: float) -> None:
    """Write a ZTA drift alert card to the kanban board (best-effort)."""
    try:
        from tools.kanban.state_machine import create_card
        create_card(
            title=f"ZTA Drift Alert — {project_id}",
            description=(
                f"ZTA maturity score dropped by {drift:.1%}. "
                f"Previous: {prev_score:.3f} → Current: {current_score:.3f}. "
                f"Review ZTA pillar scores and remediate gaps."
            ),
            priority="high",
            tags=["security", "zta", "drift", "G-18"],
        )
    except Exception as exc:
        logger.debug("Could not emit kanban alert for ZTA drift: %s", exc)
