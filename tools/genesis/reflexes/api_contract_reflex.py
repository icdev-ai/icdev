#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis API Contract Reflex — validates live API endpoints against OpenAPI spec.

Delegates all logic to tools/testing/api_contract_tester.py.
GREEN tier — reads OpenAPI spec, hits live endpoints, creates kanban bug tasks.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("api_contract_reflex")


def run(config: dict, state: object) -> dict:
    """Entry point called by Genesis daemon."""
    try:
        from tools.testing.api_contract_tester import run as _run
        result = _run(config, state)
        return {
            "success": True,
            "metric_value": float(result.get("failed", 0)),
            "details": result,
        }
    except Exception as exc:
        logger.exception("api_contract_reflex failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


if __name__ == "__main__":
    import json
    print(json.dumps(run({}, None), indent=2))
