# CUI // SP-CTI
"""Mission Canvas — Automated Discovery & Visualization wrapper.

Wraps tools.awareness.health_prober and tools.awareness.component_indexer
to auto-discover and visualize mission-relevant components.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from typing import Optional

logger = get_logger("icdev.mission_canvas.discovery")


def discover_components(mission_id: str, scope: Optional[str] = None) -> dict:
    """Run health probes and component indexing for a mission scope.

    Returns discovered components with health status and topology hints.
    """
    result = {
        "mission_id": mission_id,
        "components": [],
        "health_summary": {},
        "status": "ok",
    }
    try:
        from tools.awareness.health_prober import run_all

        health = run_all()
        result["health_summary"] = health
    except Exception as exc:
        logger.warning("Health probing failed: %s", exc)
        result["health_summary"] = {"error": str(exc)}

    try:
        from tools.awareness.component_indexer import scan
        from pathlib import Path

        components = scan(base=Path("."), scope=Path(scope or "."))
        result["components"] = components.get("components", [])
    except Exception as exc:
        logger.warning("Component indexing failed: %s", exc)
        result["components"] = []

    return result
