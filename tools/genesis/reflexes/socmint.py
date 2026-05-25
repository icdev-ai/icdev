# CUI // SP-CTI
"""Genesis Reflex — SOCMINT Harvester (6-hour cadence).

Wraps tools.strategos.socmint_harvester.run() and surfaces inserted_count
as the primary metric. Air-gap safe: returns success=False with a clear
error when the harvester module is unavailable (e.g. pre-sgx-socmint-03).
"""
IMPLEMENTATION_STATUS = "full"

from typing import Any, Dict


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the SOCMINT Harvester reflex.

    Args:
        config: Reflex configuration from genesis_config.yaml
        trust: TrustKernel instance (unused — scanner tier)

    Returns:
        {"success": bool, "metric_value": float, "details": dict}
    """
    try:
        from tools.strategos.socmint_harvester import run as harvester_run
    except ImportError as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": f"socmint_harvester import failed: {exc}"},
        }

    try:
        result = harvester_run()
    except Exception as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": str(exc)},
        }

    inserted = result.get("inserted", 0)
    skipped = result.get("skipped", 0)
    status = result.get("status", "unknown")

    return {
        "success": status != "error",
        "metric_value": float(inserted),
        "details": {
            "inserted": inserted,
            "skipped": skipped,
            "status": status,
        },
    }