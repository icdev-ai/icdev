#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Dead Code Reflex — runs dead_code.py to surface orphan files, dead
functions, and import cycles; files [DEAD-CODE] kanban tasks.

Delegates all logic to tools/code_intelligence/dead_code.py.
GREEN tier — reads codebase, creates kanban tasks.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("dead_code_reflex")


def run(config: dict, state: object) -> dict:
    """Entry point called by Genesis daemon."""
    try:
        from tools.code_intelligence.dead_code import run as _run
        result = _run(config, state)
        return {
            "success": True,
            "metric_value": float(result.get("orphan_files", 0) + result.get("dead_functions", 0)),
            "details": result,
        }
    except Exception as exc:
        logger.exception("dead_code_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


if __name__ == "__main__":
    import json
    print(json.dumps(run({}, None), indent=2))
