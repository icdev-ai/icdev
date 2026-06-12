#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Report Reflex — generate weekly autonomous status report.

Delegates to tools/genesis/reporter.py which aggregates all reflex
activity, promotions, circuit breakers, and recommendations into a
comprehensive markdown report.

GREEN tier (read-only, informational).  Always succeeds.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Report Reflex."""
    try:
        from tools.genesis.reporter import generate_report

        lookback = config.get("lookback_days", 7)
        result = generate_report(lookback_days=lookback)

        return {
            "success": True,
            "metric_value": 1.0,  # Boolean — report delivered
            "details": {
                "report_file": result.get("file", ""),
                "period": result.get("period", ""),
                "summary": result.get("summary", {}),
            },
        }
    except Exception as e:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": f"{type(e).__name__}: {e}"},
        }
