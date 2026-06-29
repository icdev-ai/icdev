#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Dependency Health Reflex — runs pip check, pip-audit (optional),
and pip list --outdated; files [DEP-HEALTH] kanban tasks for critical/high findings.

Delegates all logic to tools/testing/dep_health.py.
GREEN tier — reads requirements.txt, shells out to pip, creates kanban tasks.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("dep_health_reflex")


def run(config: dict, state: object) -> dict:
    """Entry point called by Genesis daemon."""
    try:
        from tools.testing.dep_health import run as _run
        result = _run(config, state)
        pip_audit = result.get("pip_audit", {})
        n_critical = pip_audit.get("critical", 0) if isinstance(pip_audit, dict) else 0
        n_high = pip_audit.get("high", 0) if isinstance(pip_audit, dict) else 0
        return {
            "success": True,
            "metric_value": float(n_critical + n_high),
            "details": result,
        }
    except Exception as exc:
        logger.exception("dep_health_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


if __name__ == "__main__":
    import json
    print(json.dumps(run({}, None), indent=2))
