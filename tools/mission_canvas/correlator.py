# CUI // SP-CTI
"""Mission Canvas — Real-Time Correlation wrapper.

Wraps strategos.temporal_correlator and awareness.drift_detector
to surface real-time correlation, monitoring, and alerting.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Optional

logger = get_logger("icdev.mission_canvas.correlator")


def correlate_events(
    mission_id: str,
    event_stream: list[dict],
    lookback_hours: int = 24,
) -> dict:
    """Run temporal correlation across a mission event stream.

    Returns correlated clusters and any anomaly flags.
    """
    try:
        from tools.strategos.temporal_correlator import correlate

        result = correlate(
            window_hours=lookback_hours,
        )
        return {
            "mission_id": mission_id,
            "clusters": result.get("clusters", []),
            "anomalies": result.get("anomalies", []),
            "status": "ok",
        }
    except Exception as exc:
        logger.warning("Temporal correlation failed: %s", exc)
        return {
            "mission_id": mission_id,
            "status": "error",
            "error": str(exc),
        }


def detect_drift(mission_id: str, baseline: Optional[dict] = None) -> dict:
    """Detect drift in mission environment vs. a baseline."""
    try:
        from tools.awareness.drift_detector import detect

        result = detect(
            baseline_days=baseline.get("days", 7) if baseline else 7,
        )
        result["mission_id"] = mission_id
        return result
    except Exception as exc:
        logger.warning("Drift detection failed: %s", exc)
        return {"mission_id": mission_id, "status": "error", "error": str(exc)}
