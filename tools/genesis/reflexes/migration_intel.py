#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Reflex — Migration Intelligence Engine.

Runs the MI scan pipeline autonomously via Genesis reflex system.
Air-gap safe: local canvas scan only when ICDEV_AIRGAP=true.
Zero Claude tokens consumed (scanner-tier only).
"""
IMPLEMENTATION_STATUS = "full"

import os
from datetime import datetime, timezone
from typing import Any, Dict


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_airgap() -> bool:
    return os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Migration Intelligence reflex.

    Args:
        config: Reflex configuration from genesis_config.yaml
        trust: TrustKernel instance (unused — scanner tier)

    Returns:
        {"success": bool, "metric_value": float, "details": dict}
    """
    try:
        from tools.migration_intelligence.migration_manager import run_full_pipeline
    except ImportError as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": f"migration_manager import failed: {exc}"},
        }

    try:
        result = run_full_pipeline()
    except Exception as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": str(exc)},
        }

    scan = result.get("stages", {}).get("scan", {})
    total_found = scan.get("total_found", 0)
    scored = result.get("stages", {}).get("score", {}).get("scored", 0)

    return {
        "success": True,
        "metric_value": float(total_found),
        "details": {
            "pipeline_id": result.get("pipeline_id"),
            "airgap_mode": _is_airgap(),
            "opportunities_found": total_found,
            "opportunities_scored": scored,
            "quiet_hours_skipped": result.get("quiet_hours", False),
            "stages_run": list(result.get("stages", {}).keys()),
            "completed_at": result.get("completed_at"),
        },
    }