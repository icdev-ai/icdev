# CUI // SP-CTI
"""PIR Alert Generator — baseline comparison and automated PIR creation.

Pure business logic that bridges the threat-analysis baseline validator with
the PIR manager.  When an indicator score exceeds its operator-defined
baseline threshold a PIR (or CCIR/EEI) alert is generated automatically.

NIST 800-53: SI-4 (System Monitoring), RA-5 (Vulnerability Monitoring).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Any

from tools.intelligence.pir_manager import create_pir
from tools.threat_analysis.service import validate_indicator_score

logger = get_logger("icdev.intelligence.pir_alert_generator")


# Severity-to-priority mapping (1 = critical … 4 = low)
_SEVERITY_PRIORITY = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}


def _severity_to_priority(severity_band: str | None) -> int:
    if not severity_band:
        return 3
    return _SEVERITY_PRIORITY.get(severity_band.lower(), 3)


def evaluate_indicator_and_alert(
    indicator_name: str,
    score: float,
    scope_id: str = "",
    scope: str = "project",
    operator_id: str = "",
    pir_type: str = "PIR",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Compare an indicator score against its baseline and generate a PIR alert if exceeded.

    Steps:
        1. ``validate_indicator_score`` — hierarchical lookup (user → project → tenant → platform → global).
        2. If ``exceeded`` is ``True``, build a topic / description and call ``create_pir``.
        3. Return the validation result plus the alert (or ``None``).

    Args:
        indicator_name: The indicator key to evaluate.
        score: Observed numeric score.
        scope_id: Entity ID for the requested scope level.
        scope: Starting scope level (global, platform, tenant, project, user).
        operator_id: User or system to task the alert to.
        pir_type: One of PIR, CCIR, EEI.
        db_path: Optional override DB path for testing.

    Returns:
        Dict with keys:
        - ``validation`` — full result from ``validate_indicator_score``.
        - ``alert`` — PIR dict when threshold exceeded, otherwise ``None``.
    """
    validation = validate_indicator_score(
        indicator_name=indicator_name,
        score=score,
        scope_id=scope_id,
        scope=scope,
        db_path=db_path,
    )

    if not validation["exceeded"]:
        return {"validation": validation, "alert": None}

    severity_band = validation.get("severity_band") or "medium"
    priority = _severity_to_priority(severity_band)
    threshold = validation["threshold"]
    delta = validation["delta"]

    topic = f"{indicator_name} threshold exceeded"
    description = (
        f"Indicator '{indicator_name}' scored {score}, "
        f"exceeding baseline {threshold} by {delta}. "
        f"Severity band: {severity_band}."
    )

    alert = create_pir(
        pir_type=pir_type,
        topic=topic,
        description=description,
        collection_priority=priority,
        tasked_to=operator_id,
    )

    logger.info(
        "PIR alert generated indicator=%s score=%s threshold=%s pir_id=%s",
        indicator_name,
        score,
        threshold,
        alert.get("id"),
    )

    return {"validation": validation, "alert": alert}
