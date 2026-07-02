#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Flaky Tracker Reflex — ingests pytest JUnit XML results and files
kanban bug tasks for tests with failure rate >= threshold.

Delegates all logic to tools/testing/flaky_tracker.py.
GREEN tier — reads test result XML files, writes to local SQLite history DB,
creates kanban tasks.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("flaky_tracker_reflex")


def run(config: dict, state: object) -> dict:
    """Entry point called by Genesis daemon."""
    try:
        from tools.testing.flaky_tracker import run as _run
        result = _run(config, state)
        return {
            "success": True,
            "metric_value": float(result.get("total_flaky", 0)),
            "details": result,
        }
    except Exception as exc:
        logger.exception("flaky_tracker_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


if __name__ == "__main__":
    import json
    print(json.dumps(run({}, None), indent=2))
